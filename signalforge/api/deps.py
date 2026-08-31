"""API composition root and request dependencies.

The Container wires infrastructure into the application services once; tests
override get_container with an in-memory database and a fake clock. The RBAC
dependency (require) sits on every protected router (REQ-012): it resolves the
principal, decides from the roles matrix, writes the audit event on denial and
raises the domain error the handlers map to a clean 403.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from fastapi import Depends, Query, Request

from signalforge.contracts.roles import Permission
from signalforge.domain.identity.errors import AuthenticationError
from signalforge.application.adapters.connector_service import ConnectorService
from signalforge.application.adapters.health import ConnectorHealthSources
from signalforge.application.adapters.registry_service import AdapterRegistryService
from signalforge.application.events.bus import EventBus
from signalforge.application.events.consumers import BUS_METRICS_GROUP, BusMetricsConsumer
from signalforge.application.events.dead_letters import DeadLetterService
from signalforge.application.events.dispatcher import Dispatcher
from signalforge.application.events.replay import ReplayService
from signalforge.application.events.retention import RetentionService
from signalforge.application.events.sealing import SealService
from signalforge.application.observability.health import ConnectorHealthService
from signalforge.application.observability.platform_status import PlatformStatusService
from signalforge.application.identity.auth_service import AuthService
from signalforge.application.identity.oncall_service import OnCallService
from signalforge.application.identity.org_service import OrgService
from signalforge.application.identity.principal import Principal
from signalforge.domain.events.retention import RetentionPolicy
from signalforge.infrastructure.adapters.registry import FileAdapterRegistry
from signalforge.infrastructure.adapters.runtimes import AdapterRuntimeRegistry
from signalforge.infrastructure.clock import SwitchableClock, SystemClock
from signalforge.infrastructure.config import VERSION, Settings, load_settings
from signalforge.infrastructure.observability.metrics import build_registry
from signalforge.infrastructure.observability.sources import (
    DatabaseHealthSource,
    EventBusHealthSource,
    WorkerHealthSource,
)
from signalforge.infrastructure.persistence.db import make_engine, make_session_factory
from signalforge.infrastructure.persistence.event_uow import SqlEventUnitOfWork
from signalforge.infrastructure.persistence.uow import SqlIdentityUnitOfWork
from signalforge.workers.runner import PlatformWorker, ScheduledJob
from signalforge.infrastructure.security.passwords import Argon2PasswordHasher
from signalforge.infrastructure.security.ratelimit import SlidingWindowRateLimiter
from signalforge.infrastructure.security.tokens import JWTAccessTokenService


class Container:
    def __init__(
        self,
        settings: Optional[Settings] = None,
        engine=None,
        clock=None,
    ):
        self.settings = settings or load_settings()
        # Two clocks (REQ-024). wall_clock is what security and durability read -
        # token expiry, refresh windows, rate limits, recorded_at, retention -
        # so a drill cannot expire a session or age out real data early. clock is
        # the platform clock a drill may switch to scenario time. An injected
        # clock (tests, drills) is used for both, which is what makes a frozen
        # clock able to prove an expiry.
        self.wall_clock = clock or SystemClock()
        self.clock = clock or SwitchableClock(self.wall_clock)
        self.engine = engine if engine is not None else make_engine(self.settings.db_url)
        self.session_factory = make_session_factory(self.engine)
        self.hasher = Argon2PasswordHasher()
        self.token_service = JWTAccessTokenService(
            secret=self.settings.jwt_secret,
            clock=self.wall_clock,
            ttl_s=self.settings.access_ttl_s,
        )
        self.rate_limiter = SlidingWindowRateLimiter(
            limit=self.settings.login_rate_limit,
            window_s=self.settings.login_rate_window_s,
            clock=self.wall_clock,
        )
        self.uow_factory = lambda: SqlIdentityUnitOfWork(
            self.session_factory,
            fernet_key=self.settings.fernet_key,
            clock=self.clock,
        )
        self.auth = AuthService(
            self.uow_factory, self.hasher, self.token_service,
            self.rate_limiter, self.clock,
            access_ttl_s=self.settings.access_ttl_s,
            refresh_ttl_s=self.settings.refresh_ttl_s,
        )
        self.org = OrgService(
            self.uow_factory, self.hasher, self.auth, self.clock,
            invite_ttl_s=self.settings.invite_ttl_s,
        )
        self.oncall = OnCallService(self.uow_factory, self.clock)

        # Event backbone (S2). recorded_at always comes from the wall clock even
        # when a drill has moved the clock the rest of the system reads.
        self.metrics = build_registry()
        self.metrics.set_gauge(
            "signalforge_build_info",
            1,
            {"version": VERSION, "clock_source": getattr(self.clock, "source", "wall")},
        )
        self.event_uow_factory = lambda: SqlEventUnitOfWork(
            self.session_factory, clock=self.clock, recording_clock=self.wall_clock
        )
        self.bus = EventBus(recording_clock=self.wall_clock, metrics=self.metrics)
        self.replay = ReplayService(self.event_uow_factory)
        self.seals = SealService(self.event_uow_factory, clock=self.clock)
        self.dispatcher = Dispatcher(
            self.event_uow_factory, clock=self.wall_clock, metrics=self.metrics
        )
        self.dispatcher.register(BUS_METRICS_GROUP, BusMetricsConsumer(self.metrics))
        self.dead_letters = DeadLetterService(self.event_uow_factory, clock=self.clock)
        self.retention = RetentionService(
            self.event_uow_factory,
            self.bus,
            clock=self.wall_clock,
            policy=RetentionPolicy(self.settings.signal_retention_days),
        )

        # Adapter registry (S3-T01). Manifests are discovered from the adapters
        # tree; runtimes register themselves here and an adapter with a manifest
        # but no runtime stays visible and honestly unmeasurable.
        self.adapter_registry = FileAdapterRegistry(Path(self.settings.adapters_dir))
        self.adapter_runtimes = AdapterRuntimeRegistry()
        self.adapters = AdapterRegistryService(
            self.adapter_registry, self.adapter_runtimes
        )
        self.connectors = ConnectorService(
            self.uow_factory, self.adapter_registry, self.bus, self.clock
        )

        # Self-observability (S2-T03).
        self.health = ConnectorHealthService(
            self.event_uow_factory, self.bus, clock=self.wall_clock, metrics=self.metrics
        )
        self.worker = PlatformWorker(self._jobs(), clock=self.wall_clock)
        self.health.register(DatabaseHealthSource(self.session_factory))
        self.health.register(EventBusHealthSource(self.event_uow_factory))
        # Connector bindings join the same loop, re-read every poll so a binding
        # an operator just disabled leaves it on the next tick (S3-T01).
        self.health.register_provider(
            ConnectorHealthSources(
                self.uow_factory,
                self.adapter_registry,
                self.adapter_runtimes,
                self.connectors,
                clock=self.wall_clock,
            )
        )
        if self.settings.worker_enabled:
            # An API-only node (the enterprise split the architecture allows)
            # has no worker group of its own, so it does not report one; a node
            # that should have one and does not is DOWN, not silent.
            self.health.register(WorkerHealthSource(self.worker))
        self.platform_status = PlatformStatusService(
            self.event_uow_factory,
            self.health,
            self.worker,
            clock=self.clock,
            version=VERSION,
            retention_days=self.settings.signal_retention_days,
        )

    def _jobs(self) -> list[ScheduledJob]:
        """What runs beside the API. Consumers and pollers join this list as the
        sprints add them; each one owns its own failures."""
        return [
            ScheduledJob(
                name="dispatcher",
                interval_s=self.settings.dispatch_interval_s,
                run=self.dispatcher.run_once,
            ),
            ScheduledJob(
                name="connector-health",
                interval_s=self.settings.health_interval_s,
                run=self.health.report,
            ),
            ScheduledJob(
                name="retention",
                interval_s=self.settings.retention_interval_s,
                run=self.retention.sweep,
            ),
        ]

    async def refresh_platform_gauges(self) -> None:
        """Point-in-time gauges are computed when /metrics is scraped rather than
        kept up to date on every write, so a scrape costs one query and a quiet
        platform costs nothing."""
        bus = await self.platform_status.bus_status()
        self.metrics.set_gauge("signalforge_event_seq", bus.head_seq)
        for group, lag in bus.groups.items():
            self.metrics.set_gauge("signalforge_consumer_lag", lag, {"group": group})
        for state, count in bus.dead_letters.items():
            self.metrics.set_gauge("signalforge_dead_letters", count, {"state": state})
        for status in self.worker.statuses:
            self.metrics.set_gauge(
                "signalforge_connector_health",
                1.0 if status.healthy else 0.0,
                {"connector": f"job:{status.name}"},
            )


_container: Optional[Container] = None


def get_container() -> Container:
    global _container
    if _container is None:
        _container = Container()
    return _container


async def get_principal(
    request: Request, container: Container = Depends(get_container)
) -> Principal:
    header = request.headers.get("Authorization", "")
    if not header.startswith("Bearer "):
        raise AuthenticationError("missing bearer token")
    return await container.auth.authenticate(header[len("Bearer "):])


def require(permission: Permission):
    """RBAC dependency factory. app_id comes from the query string so per-app
    bindings apply; the denied path writes the audit event before raising."""

    async def dependency(
        request: Request,
        principal: Principal = Depends(get_principal),
        container: Container = Depends(get_container),
        app_id: Optional[str] = Query(default=None),
    ) -> Principal:
        await container.auth.authorize(
            principal, permission, app_id, resource=request.url.path
        )
        return principal

    return dependency
