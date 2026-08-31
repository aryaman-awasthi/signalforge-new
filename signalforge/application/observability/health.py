"""Connector health reporting (REQ-025, REQ-030).

Polls every registered source, publishes one system.connector.health event per
source so the cockpit and any consumer learn about it the same way they learn
about everything else, and writes an audit entry only when a source's verdict
changes - health is polled every thirty seconds, and an audit chain full of
identical heartbeats is an audit chain nobody reads.

A source whose own check raises is reported DOWN with the error as its detail.
The poll loop must survive the thing it is monitoring being broken.

Two kinds of source share this loop (S3). Platform sources - database, bus,
worker - are registered once at composition time and belong to no tenant.
Connector bindings are supplied by a provider that is re-asked on every poll,
because bindings are created, disabled and deleted while the process runs; a
list captured at startup would be a list of yesterday's integrations. What
separates them everywhere afterwards is HealthSource.tenant_id:

- readiness: only platform sources decide whether this node answers /health,
  so one customer's dead connector never takes the node out of rotation;
- visibility: /platform/status shows a tenant its own connectors and no others;
- events: a connector's health lands on its own tenant's timeline, while
  platform health stays under the reserved _platform tenant;
- metrics: /metrics is unauthenticated, so gauges are keyed by the source's
  metric_label - the adapter id, never the binding id - and several bindings of
  one adapter collapse to their worst verdict rather than to a row each.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Awaitable, Callable, Optional, Sequence

from signalforge.application.events.bus import EventBus
from signalforge.application.events.uow import EventUnitOfWork
from signalforge.contracts.canonical import ComponentHealth, HealthReport, HealthStatus
from signalforge.contracts.envelope import (
    Actor,
    ActorType,
    Authority,
    EventEnvelope,
    Plane,
    Source,
)
from signalforge.domain.identity.ports import Clock
from signalforge.domain.observability.health import (
    CONNECTOR_HEALTH_TOPIC,
    PLATFORM_TENANT_ID,
    HealthSource,
    overall_status,
    transitioned,
)

logger = logging.getLogger("signalforge.health")

SourceProvider = Callable[[], Awaitable[Sequence[HealthSource]]]

_GAUGE_VALUE = {
    HealthStatus.UP: 1.0,
    HealthStatus.DEGRADED: 0.5,
    HealthStatus.UNKNOWN: 0.5,
    HealthStatus.DOWN: 0.0,
}


@dataclass(frozen=True)
class Observation:
    tenant_id: Optional[str]
    app_id: Optional[str]
    component: ComponentHealth
    checked_at: datetime


class ConnectorHealthService:
    def __init__(
        self,
        uow_factory: Callable[[], EventUnitOfWork],
        bus: EventBus,
        clock: Clock,
        metrics: Optional[object] = None,
    ):
        self._uow_factory = uow_factory
        self._bus = bus
        self._clock = clock
        self._metrics = metrics
        self._sources: list[HealthSource] = []
        self._providers: list[SourceProvider] = []
        self._previous: dict[str, HealthStatus] = {}
        self._observations: dict[str, Observation] = {}
        self._last_checked_at: Optional[datetime] = None

    def register(self, source: HealthSource) -> None:
        """A platform component, known at composition time."""
        self._sources.append(source)

    def register_provider(self, provider: SourceProvider) -> None:
        """A source of sources, re-asked on every poll. Connector bindings come
        this way because they change while the process runs."""
        self._providers.append(provider)

    @property
    def sources(self) -> tuple[str, ...]:
        return tuple(s.source_id for s in self._sources)

    @property
    def providers(self) -> tuple[SourceProvider, ...]:
        return tuple(self._providers)

    @property
    def last_checked_at(self) -> Optional[datetime]:
        return self._last_checked_at

    @property
    def last_report(self) -> Optional[HealthReport]:
        """The platform's own last verdict - what readiness means here."""
        if self._last_checked_at is None:
            return None
        components = self.components_for()
        return HealthReport(
            status=overall_status(components),
            components=components,
            checked_at=self._last_checked_at,
        )

    def components_for(self, tenant_id: Optional[str] = None) -> list[ComponentHealth]:
        """Platform components, plus this tenant's connectors when asked. Never
        another tenant's: the operator screen is per-tenant even though the poll
        loop is not."""
        return [
            observation.component
            for observation in self._observations.values()
            if observation.tenant_id is None
            or (tenant_id is not None and observation.tenant_id == tenant_id)
        ]

    async def report(self) -> HealthReport:
        """The full poll: platform components and every connector binding.
        This is what the worker's connector-health job runs."""
        sources = list(self._sources)
        for provider in self._providers:
            sources.extend(await self._provided(provider))
        return await self._poll(sources)

    async def platform_report(self) -> HealthReport:
        """Readiness of this node only. /health calls this: a probe is asking
        whether this process can serve, and a customer's broken integration is
        not an answer to that question."""
        return await self._poll(list(self._sources))

    # -- internals ---------------------------------------------------------

    async def _provided(self, provider: SourceProvider) -> Sequence[HealthSource]:
        try:
            return list(await provider())
        except Exception as exc:
            # The provider reads the database. If that read fails the platform
            # sources will already be reporting it; losing the connector list
            # for one cycle must not lose the platform's own verdict too.
            logger.warning(
                "connector health source provider failed",
                extra={"error": f"{type(exc).__name__}: {exc}"},
            )
            return []

    async def _poll(self, sources: Sequence[HealthSource]) -> HealthReport:
        now = self._clock.now()
        components = [await self._check(source) for source in sources]
        self._last_checked_at = now

        worst: dict[str, float] = {}
        for source, component in zip(sources, components):
            self._observations[component.component] = Observation(
                tenant_id=source.tenant_id,
                app_id=source.app_id,
                component=component,
                checked_at=now,
            )
            value = _GAUGE_VALUE[component.status]
            label = source.metric_label
            worst[label] = min(worst.get(label, value), value)
            changed = transitioned(self._previous.get(component.component), component.status)
            self._previous[component.component] = component.status
            await self._record(source, component, now, changed)

        self._publish_gauges(worst, components)
        # The returned report is what the caller asked about: the sources it
        # passed in, not the accumulated view.
        return HealthReport(
            status=overall_status(components), components=components, checked_at=now
        )

    def _publish_gauges(
        self, worst: dict[str, float], components: Sequence[ComponentHealth]
    ) -> None:
        if self._metrics is None:
            return
        for label, value in worst.items():
            self._metrics.set_gauge(
                "signalforge_connector_health", value, {"connector": label}
            )
        counts = {status.value: 0 for status in HealthStatus}
        for observation in self._observations.values():
            if observation.tenant_id is not None:
                counts[observation.component.status.value] += 1
        for state, count in counts.items():
            self._metrics.set_gauge(
                "signalforge_connector_bindings", count, {"state": state}
            )

    async def _check(self, source: HealthSource) -> ComponentHealth:
        try:
            return await source.check()
        except Exception as exc:
            logger.warning(
                "health check failed",
                extra={"connector": source.source_id, "error": f"{type(exc).__name__}: {exc}"},
            )
            return ComponentHealth(
                component=source.source_id,
                status=HealthStatus.DOWN,
                detail=f"{type(exc).__name__}: {exc}"[:300],
            )

    async def _record(
        self, source: HealthSource, component: ComponentHealth, now, changed: bool
    ) -> None:
        payload = {
            "connector": component.component,
            "kind": source.kind.value if source.kind else None,
            "status": component.status.value,
            "detail": component.detail,
        }
        if source.app_id:
            payload["app_id"] = source.app_id
        tenant_id = source.tenant_id or PLATFORM_TENANT_ID
        async with self._uow_factory() as uow:
            await self._bus.publish(
                uow,
                EventEnvelope(
                    topic=CONNECTOR_HEALTH_TOPIC,
                    tenant_id=tenant_id,
                    app_id=source.app_id,
                    # Health is polled continuously: signal plane, ages out.
                    plane=Plane.SIGNAL,
                    occurred_at=now,
                    actor=Actor(type=ActorType.SYSTEM, id="connector-health"),
                    source=Source(
                        system=component.component,
                        kind=source.kind,
                        authority=Authority.OBSERVED,
                    ),
                    payload=payload,
                ),
            )
            if changed:
                await uow.audit.append(
                    tenant_id=tenant_id,
                    topic=CONNECTOR_HEALTH_TOPIC,
                    actor_type="system",
                    actor_id="connector-health",
                    app_id=source.app_id,
                    payload=payload,
                )
            await uow.commit()
