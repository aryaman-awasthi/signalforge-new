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

from fastapi import Depends, HTTPException, Query, Request, WebSocket, WebSocketException

from signalforge.contracts.ingest import HDR_KEY_ID, HDR_SIGNATURE, HDR_TIMESTAMP
from signalforge.contracts.roles import Permission
from signalforge.domain.identity.errors import AuthenticationError
from signalforge.domain.ingest.streams import IngestStream
from signalforge.application.adapters.connector_service import ConnectorService
from signalforge.application.adapters.health import ConnectorHealthSources
from signalforge.application.adapters.invoker import ConnectorInvoker
from signalforge.application.adapters.registry_service import AdapterRegistryService
from signalforge.application.detection.poller import MetricRollupPoller
from signalforge.application.detection.probes import ProbeRunner
from signalforge.application.detection.service import DetectionService
from signalforge.application.incidents.changes import (
    ChangeWindowProjector,
    ChangeWindowService,
)
from signalforge.application.incidents.common import (
    CHANGE_TOPIC_PATTERNS,
    CHANGE_WINDOW_GROUP,
)
from signalforge.application.incidents.itsm import IncidentTicketSync
from signalforge.application.incidents.service import IncidentService
from signalforge.application.notifications.inbox import NotificationInbox
from signalforge.application.warroom.bridge import BridgeService
from signalforge.application.warroom.paging import PagingService
from signalforge.application.warroom.service import WarRoomService
from signalforge.application.warroom.tasks import IncidentTaskService
from signalforge.application.onboarding.apps import ApplicationService
from signalforge.application.onboarding.discovery import DiscoveryService
from signalforge.application.onboarding.testing import (
    ConnectionTestService,
    ReadinessService,
)
from signalforge.application.onboarding.wizard import OnboardingService
from signalforge.application.events.bus import EventBus
from signalforge.application.events.consumers import (
    BUS_METRICS_GROUP,
    COCKPIT_SOCKET_GROUP,
    COCKPIT_TOPIC_PATTERNS,
    BusMetricsConsumer,
    CockpitSocketConsumer,
)
from signalforge.application.events.dead_letters import DeadLetterService
from signalforge.application.events.dispatcher import Dispatcher
from signalforge.application.events.replay import ReplayService
from signalforge.application.events.retention import RetentionService
from signalforge.application.events.sealing import SealService
from signalforge.application.ingest.ingest_service import (
    AuthenticatedIngest,
    IngestService,
)
from signalforge.application.ingest.keys_service import IngestKeyService
from signalforge.application.observability.health import ConnectorHealthService
from signalforge.application.observability.platform_status import PlatformStatusService
from signalforge.application.identity.auth_service import AuthService
from signalforge.application.llm.reporting import LlmDegradationReporter
from signalforge.application.identity.oncall_service import OnCallService
from signalforge.application.identity.org_service import OrgService
from signalforge.application.identity.principal import Principal
from signalforge.domain.events.retention import RetentionPolicy
from signalforge.domain.llm.budget import InMemoryTokenLedger
from signalforge.infrastructure.adapters.builtin import register_builtin_runtimes
from signalforge.infrastructure.adapters.limits import CallBudget
from signalforge.infrastructure.adapters.registry import FileAdapterRegistry
from signalforge.infrastructure.adapters.runtimes import AdapterRuntimeRegistry
from signalforge.infrastructure.adapters.services import AdapterServices
from signalforge.infrastructure.clock import SwitchableClock, SystemClock
from signalforge.infrastructure.notifications.hub import InProcessNotificationHub
from signalforge.infrastructure.onboarding.probe import HttpEndpointProbe
from signalforge.infrastructure.persistence.integrations import (
    SqlIntegrationUnitOfWork,
)
from signalforge.infrastructure.config import (
    VERSION,
    LlmSettings,
    Settings,
    load_llm_settings,
    load_settings,
)
from signalforge.infrastructure.llm.embeddings import GatewayEmbeddingProvider
from signalforge.infrastructure.llm.provider import build_llm_provider
from signalforge.infrastructure.persistence.llm_cache import SqlResponseCache
from signalforge.infrastructure.observability.metrics import build_registry
from signalforge.infrastructure.observability.sources import (
    DatabaseHealthSource,
    EventBusHealthSource,
    WorkerHealthSource,
)
from signalforge.infrastructure.persistence.db import make_engine, make_session_factory
from signalforge.infrastructure.persistence.event_uow import SqlEventUnitOfWork
from signalforge.infrastructure.persistence.rollups import (
    SqlDetectionUnitOfWork,
    create_rollup_schema,
)
from signalforge.infrastructure.persistence.uow import SqlIdentityUnitOfWork
from signalforge.workers.lifecycle import lifecycle_jobs
from signalforge.workers.warroom import warroom_jobs
from signalforge.workers.pollers import detection_jobs
from signalforge.workers.runner import PlatformWorker, ScheduledJob
from signalforge.infrastructure.security.hmac_verify import IngestHmacVerifier
from signalforge.infrastructure.security.passwords import Argon2PasswordHasher
from signalforge.infrastructure.security.ratelimit import SlidingWindowRateLimiter
from signalforge.infrastructure.security.tokens import (
    JWTAccessTokenService,
    JWTSocketTicketService,
)


class Container:
    def __init__(
        self,
        settings: Optional[Settings] = None,
        engine=None,
        clock=None,
        llm_settings: Optional[LlmSettings] = None,
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
        # The socket credential (REQ-033). Signed with the same secret and typed
        # differently, so a ticket cannot be presented as a bearer token and a
        # bearer token cannot be redeemed as a ticket. Wall time, like every
        # other credential: a drill clock must not be able to age one out or
        # keep one alive.
        self.socket_tickets = JWTSocketTicketService(
            secret=self.settings.jwt_secret, clock=self.wall_clock
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
            socket_tickets=self.socket_tickets,
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

        # The shipped adapters (S3-T02). Their persistence is a unit of work of
        # its own: an adapter must be able to store a ticket or a receipt
        # without holding a transaction that also contains identity state.
        self.notification_hub = InProcessNotificationHub()
        # The cockpit's push half (S4 carry-over, REQ-033/REQ-045). A consumer
        # group rather than a call from the war-room services, so the arrow keeps
        # pointing inward and a node with no cockpit open pays nothing: it nudges
        # nobody and advances its offset.
        self.dispatcher.register(
            COCKPIT_SOCKET_GROUP,
            CockpitSocketConsumer(self.notification_hub, metrics=self.metrics),
            topics=COCKPIT_TOPIC_PATTERNS,
        )
        self.integration_uow_factory = lambda: SqlIntegrationUnitOfWork(
            self.session_factory
        )
        # The model path (S5-T02, REQ-051). Built unconditionally and never
        # allowed to stop a boot: with no key, or with an endpoint that is not
        # the approved gateway, the provider holds no client and every call
        # answers deterministically while announcing system.llm.degraded.
        self.llm_settings = llm_settings or load_llm_settings()
        self.llm_degradation = LlmDegradationReporter(
            self.event_uow_factory, self.bus, self.wall_clock, metrics=self.metrics
        )
        self.llm = build_llm_provider(
            self.llm_settings,
            cache=SqlResponseCache(self.session_factory, self.wall_clock),
            ledger=InMemoryTokenLedger(self.llm_settings.incident_token_budget),
            listener=self.llm_degradation,
            metrics=self.metrics,
        )
        self.embeddings = GatewayEmbeddingProvider(self.llm)

        self.adapter_services = AdapterServices(
            uow_factory=self.integration_uow_factory,
            clock=self.clock,
            budget=CallBudget(lambda: self.wall_clock.now().timestamp()),
            notifications=self.notification_hub,
            metrics=self.metrics,
            # kb-index blends embeddings from the same gateway the rest of the
            # model path uses, and ranks with BM25 alone whenever it degrades
            # (REQ-035).
            embeddings=self.embeddings,
        )
        self.installed_adapters = register_builtin_runtimes(
            self.adapter_runtimes, self.adapter_services
        )
        self.invoker = ConnectorInvoker(
            self.uow_factory,
            self.adapter_registry,
            self.adapter_runtimes,
            self.connectors,
            metrics=self.metrics,
        )
        self.notification_inbox = NotificationInbox(self.uow_factory, self.clock)

        # Push ingestion (S3-T03). Both clocks appear here on purpose: the
        # replay window and the "when did this reach us" stamp read wall time,
        # so a drill clock can neither widen the window nor age a batch out,
        # while the platform clock stamps the events a drill produces.
        self.ingest_keys = IngestKeyService(
            self.uow_factory, self.adapter_registry, self.bus, self.clock
        )
        self.ingest_rate_limiter = SlidingWindowRateLimiter(
            limit=self.settings.ingest_rate_limit,
            window_s=self.settings.ingest_rate_window_s,
            clock=self.wall_clock,
        )
        self.ingest = IngestService(
            self.uow_factory,
            self.bus,
            IngestHmacVerifier(
                clock=self.wall_clock,
                replay_window_s=self.settings.ingest_replay_window_s,
            ),
            self.ingest_rate_limiter,
            self.invoker,
            clock=self.clock,
            recording_clock=self.wall_clock,
            metrics=self.metrics,
        )

        # Onboarding (S3-T04). The wizard's services sit on top of everything
        # above: the registry decides what can be suggested, the invoker is the
        # only way a connection test or a baseline reaches an application, and
        # the discovery probe is the one unauthenticated outbound call this
        # platform makes.
        self.applications = ApplicationService(self.uow_factory, self.bus, self.clock)
        self.endpoint_probe = HttpEndpointProbe(self.adapter_services)
        self.discovery = DiscoveryService(
            self.uow_factory,
            self.adapter_registry,
            self.adapter_runtimes,
            self.endpoint_probe,
            self.bus,
            self.clock,
        )
        self.onboarding = OnboardingService(
            self.uow_factory,
            self.adapter_registry,
            self.invoker,
            self.bus,
            self.clock,
        )
        self.connection_tests = ConnectionTestService(
            self.uow_factory,
            self.adapter_registry,
            self.adapter_runtimes,
            self.invoker,
            self.bus,
            self.clock,
        )
        self.readiness = ReadinessService(
            self.uow_factory,
            self.adapter_registry,
            self.invoker,
            self.connection_tests,
            self.bus,
            self.clock,
        )

        # Detection (S4-T01). Its own engine and its own database: rollups are
        # derived, written every five seconds and pruned in hours, and they have
        # no business sharing a transaction with the audit hash chain.
        self.rollup_engine = make_engine(self.settings.rollup_db_url, null_pool=True)
        self.rollup_session_factory = make_session_factory(self.rollup_engine)
        self.detection_uow_factory = lambda: SqlDetectionUnitOfWork(
            self.rollup_session_factory
        )
        self.rollup_poller = MetricRollupPoller(
            self.uow_factory,
            self.detection_uow_factory,
            self.adapter_registry,
            self.invoker,
            self.bus,
            self.clock,
            metrics=self.metrics,
        )
        self.probe_runner = ProbeRunner(
            self.uow_factory,
            self.detection_uow_factory,
            self.adapter_registry,
            self.invoker,
            self.clock,
            metrics=self.metrics,
        )
        self.detection = DetectionService(
            self.uow_factory,
            self.detection_uow_factory,
            self.bus,
            self.clock,
            metrics=self.metrics,
            baseline_window_s=self.settings.detection_baseline_window_s,
        )
        # Removing an application removes what the detector believes about it.
        # Wired here rather than imported by onboarding, so the dependency still
        # points inward.
        self.applications.on_removed.append(self.detection.forget)

        # Incident lifecycle (S4-T02). The change-window projection is a consumer
        # group on the bus, so a deployment announced through the frozen ingest
        # path becomes a chip by the same route an auditor replays.
        self.changes = ChangeWindowService(self.uow_factory, self.clock)
        self.dispatcher.register(
            CHANGE_WINDOW_GROUP,
            ChangeWindowProjector(metrics=self.metrics),
            topics=CHANGE_TOPIC_PATTERNS,
        )
        self.incident_tickets = IncidentTicketSync(
            self.uow_factory,
            self.adapter_registry,
            self.invoker,
            self.bus,
            self.clock,
            metrics=self.metrics,
        )
        self.incidents = IncidentService(
            self.uow_factory,
            self.bus,
            self.clock,
            self.changes,
            self.incident_tickets,
            detection=self.detection,
            metrics=self.metrics,
        )

        # The war room (S4-T03). Assembled here rather than by the incident
        # service importing it, so the dependency keeps pointing one way: the
        # lifecycle knows nothing about paging, and a node could run incidents
        # with the room disabled without changing a line of the lifecycle.
        self.war_rooms = WarRoomService(
            self.uow_factory,
            self.bus,
            self.clock,
            self.adapter_registry,
            metrics=self.metrics,
        )
        self.paging = PagingService(
            self.uow_factory,
            self.bus,
            self.clock,
            self.adapter_registry,
            self.invoker,
            rooms=self.war_rooms,
            metrics=self.metrics,
        )
        self.bridge = BridgeService(
            self.uow_factory,
            self.bus,
            self.clock,
            self.invoker,
            rooms=self.war_rooms,
            metrics=self.metrics,
        )
        self.incident_tasks = IncidentTaskService(
            self.uow_factory, self.bus, self.clock, metrics=self.metrics
        )
        # Declaring opens the room; opening it pages the room. Both are hooks
        # rather than calls, and both survive their own failure: a P0 that could
        # not be declared because a pager was down would be the worst bug in
        # this product.
        self.war_rooms.on_opened.append(self.paging.page_room)
        self.incidents.on_declared.append(self.war_rooms.open_for)
        self.incidents.on_closed.append(self._stand_down)

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
        jobs = [
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
        if self.settings.detection_enabled:
            jobs += detection_jobs(
                self.rollup_poller,
                self.probe_runner,
                self.detection,
                rollup_interval_s=self.settings.rollup_interval_s,
                detection_interval_s=self.settings.detection_interval_s,
                probe_interval_s=self.settings.probe_interval_s,
                retention_s=self.settings.rollup_retention_s,
            )
        # The ITSM retry sweep runs whether or not detection does: an incident
        # declared by hand on a node with no detector still has a ticket to keep
        # in step.
        jobs += lifecycle_jobs(
            self.incident_tickets,
            interval_s=self.settings.itsm_sync_interval_s,
        )
        # Escalation and the commitment harvest, for the same reason: a page that
        # nobody answered has to escalate on a node with no detector.
        jobs += warroom_jobs(
            self.paging,
            self.bridge,
            escalation_interval_s=self.settings.escalation_interval_s,
            harvest_interval_s=self.settings.bridge_harvest_interval_s,
        )
        return jobs

    async def _stand_down(self, incident) -> None:
        """The incident closed: stop ringing phones, and close the room.

        Acknowledged pages are left alone - what somebody took responsibility for
        is not un-taken by the incident ending - and the room stays readable,
        because what was decided in it is what a post-incident review reads.
        """
        await self.paging.cancel_open_pages(incident)
        await self.war_rooms.close_for(incident)

    async def prepare_rollup_store(self) -> None:
        """Create `rollups.db`'s tables if they are not there.

        Called from the API lifespan rather than from a migration: this database
        is derived and rebuildable, and Alembic's environment targets the
        incident database's metadata (see rollup_models.py).
        """
        await create_rollup_schema(self.rollup_engine)

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
        # Install-wide counts only: an incident id and an app_id belong to one
        # tenant, and this scrape has no session (S4-T02, S4-T03).
        await self.incidents.refresh_gauges()
        await self.paging.refresh_gauges()


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


BEARER_SUBPROTOCOL = "bearer"
TICKET_SUBPROTOCOL = "ticket"


def websocket_credential(websocket: WebSocket) -> tuple[Optional[str], Optional[str]]:
    """The (kind, value) a handshake presented, or (None, None).

    Three places, none of them a URL. A `?token=` query parameter is deliberately
    not accepted for either credential: it would put live credential material
    into every access log and proxy history on the path.

    | Where | Who uses it |
    |---|---|
    | `Authorization: Bearer <access token>` | server-side callers and tests |
    | `bearer, <access token>` subprotocol | a caller that holds a token in JavaScript |
    | `ticket, <socket ticket>` subprotocol | **the cockpit** |

    The cockpit uses the third and can use no other, and that is the point. Its
    session token lives in an httpOnly cookie that browser JavaScript cannot
    read, and a cross-origin WebSocket handshake sends no cookie - so the browser
    asks its own origin for a short-lived, single-use ticket and spends it here.
    The httpOnly control is untouched, and a ticket captured off the wire is
    worth one handshake for thirty seconds and nothing on the HTTP API.
    """
    header = websocket.headers.get("authorization", "")
    if header.startswith("Bearer "):
        return BEARER_SUBPROTOCOL, header[len("Bearer "):]
    subprotocols = list(websocket.scope.get("subprotocols") or [])
    if len(subprotocols) >= 2 and subprotocols[0] in (
        BEARER_SUBPROTOCOL,
        TICKET_SUBPROTOCOL,
    ):
        return subprotocols[0], subprotocols[1]
    return None, None


async def get_ws_principal(
    websocket: WebSocket, container: "Container" = Depends(get_container)
) -> Principal:
    """The WebSocket counterpart of get_principal.

    A failure closes the handshake with 1008 (policy violation) rather than
    raising into the HTTP error handlers, which never see a socket. The security
    suite's router walk treats this and get_principal as equivalent: both resolve
    a real session before any handler runs - and a ticket resolves through the
    *same* session lookup a bearer token does, so a revoked session closes the
    socket door as firmly as it closes the HTTP one.

    The refusal says nothing about why. An expired ticket, a replayed one, one
    signed for a session that has since been revoked and one that was never
    valid all close with the same reason, because telling a caller which of
    those it was is telling them how to get closer.
    """
    kind, credential = websocket_credential(websocket)
    if not credential:
        raise WebSocketException(code=1008, reason="missing socket credential")
    try:
        if kind == TICKET_SUBPROTOCOL:
            return await container.auth.authenticate_socket_ticket(credential)
        return await container.auth.authenticate(credential)
    except AuthenticationError as exc:
        raise WebSocketException(code=1008, reason="authentication failed") from exc


async def read_ingest_body(request: Request, limit: int) -> bytes:
    """The exact bytes the signature covers, bounded before they are read.

    Content-Length is checked first so an oversized body is refused without
    being buffered, and the length is re-checked afterwards because a chunked
    request declares no length at all. The bytes are kept rather than re-encoded
    from a parsed object: an HMAC over a re-serialisation is an HMAC over
    something the sender never sent.
    """
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > limit:
        raise HTTPException(status_code=413, detail=f"body exceeds {limit} bytes")
    body = await request.body()
    if len(body) > limit:
        raise HTTPException(status_code=413, detail=f"body exceeds {limit} bytes")
    return body


def require_ingest(stream: IngestStream):
    """Ingest authentication factory - the machine counterpart of `require`.

    A sending application holds no session and no role: it holds an ingest key
    this platform issued, and the credential is the bearer token plus an HMAC
    over "<timestamp>.<raw body>" within the replay window, exactly as the frozen
    contract specifies. Everything the request then writes is scoped to the
    tenant, the application and the stream that key names, so there is no
    parameter through which it could reach another tenant's data.

    Marked so the security suite's router walk can recognise it as a credential
    resolver: the walk's rule is that no product route runs a handler before a
    real, revocable credential has been resolved, and this satisfies it without
    pretending a machine is a person holding permissions.
    """

    async def dependency(
        request: Request, container: "Container" = Depends(get_container)
    ) -> AuthenticatedIngest:
        body = await read_ingest_body(request, container.settings.ingest_max_body_bytes)
        key = await container.ingest.authenticate(
            stream=stream,
            authorization=request.headers.get("Authorization", ""),
            signature=request.headers.get(HDR_SIGNATURE, ""),
            timestamp=request.headers.get(HDR_TIMESTAMP, ""),
            key_id=request.headers.get(HDR_KEY_ID),
            body=body,
        )
        return AuthenticatedIngest(key=key, stream=stream, body=body)

    dependency.resolves_ingest_credential = True
    dependency.ingest_stream = stream
    return dependency


def require(permission: Permission):
    """RBAC dependency factory. app_id comes from the query string so per-app
    bindings apply; the denied path writes the audit event before raising.

    The permission and where the application is named are attached to the
    dependency as well as closed over, so the published API document can state
    what each route requires by reading the route's own guard rather than a
    hand-kept list (S3-T07, signalforge/api/openapi.py).
    """

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

    dependency.required_permission = permission
    dependency.app_id_source = "query"
    return dependency


def require_app(permission: Permission):
    """The same decision for routes that name the application in the *path*.

    `/apps/{app_id}/...` asks the same question `?app_id=` does - which
    application is this call about - so a per-app role binding has to be
    evaluated against it. It is a second factory rather than a branch in
    `require` because FastAPI resolves a name declared as a query parameter as a
    query parameter even when the route has a path parameter of that name; the
    path value is read from the request instead, and `require` keeps documenting
    its query parameter for every route that has one.
    """

    async def dependency(
        request: Request,
        principal: Principal = Depends(get_principal),
        container: Container = Depends(get_container),
    ) -> Principal:
        app_id = request.path_params.get("app_id")
        await container.auth.authorize(
            principal, permission, app_id, resource=request.url.path
        )
        return principal

    dependency.required_permission = permission
    dependency.app_id_source = "path"
    return dependency


def require_incident(permission: Permission):
    """The same decision for a route that names an *incident* (S4-T02).

    `/incidents/{incident_id}/close` asks the same question the other two
    factories do - which application is this call about - but the answer is not
    in the URL: it is on the incident. So the incident is resolved first, inside
    the caller's own tenant, and the permission is then evaluated against the
    application it names. A per-application incident commander is therefore
    bound to the incidents of their own applications and to no others.

    Resolving before authorizing means an id that exists in this tenant is
    distinguishable from one that does not, which is deliberate and bounded: the
    lookup is tenant-scoped, so a caller learns nothing about any other tenant,
    and reading an incident of one's own tenant needs no permission anyway.
    """

    async def dependency(
        request: Request,
        principal: Principal = Depends(get_principal),
        container: Container = Depends(get_container),
    ) -> Principal:
        incident_id = request.path_params.get("incident_id")
        app_id = await container.incidents.app_of(principal.tenant_id, incident_id)
        await container.auth.authorize(
            principal, permission, app_id, resource=request.url.path
        )
        return principal

    dependency.required_permission = permission
    dependency.app_id_source = "incident"
    return dependency
