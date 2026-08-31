"""Connector health reporting (REQ-025).

Polls every registered source, publishes one system.connector.health event per
source so the cockpit and any consumer learn about it the same way they learn
about everything else, and writes an audit entry only when a source's verdict
changes - health is polled every thirty seconds, and an audit chain full of
identical heartbeats is an audit chain nobody reads.

A source whose own check raises is reported DOWN with the error as its detail.
The poll loop must survive the thing it is monitoring being broken.
"""

from __future__ import annotations

import logging
from typing import Callable, Optional

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

_GAUGE_VALUE = {
    HealthStatus.UP: 1.0,
    HealthStatus.DEGRADED: 0.5,
    HealthStatus.UNKNOWN: 0.5,
    HealthStatus.DOWN: 0.0,
}


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
        self._previous: dict[str, HealthStatus] = {}
        self._last_report: Optional[HealthReport] = None

    def register(self, source: HealthSource) -> None:
        self._sources.append(source)

    @property
    def sources(self) -> tuple[str, ...]:
        return tuple(s.source_id for s in self._sources)

    @property
    def last_report(self) -> Optional[HealthReport]:
        return self._last_report

    async def report(self) -> HealthReport:
        now = self._clock.now()
        components = [await self._check(source) for source in self._sources]
        report = HealthReport(
            status=overall_status(components), components=components, checked_at=now
        )
        self._last_report = report

        for source, component in zip(self._sources, components):
            if self._metrics is not None:
                self._metrics.set_gauge(
                    "signalforge_connector_health",
                    _GAUGE_VALUE[component.status],
                    {"connector": component.component},
                )
            changed = transitioned(self._previous.get(component.component), component.status)
            self._previous[component.component] = component.status
            await self._record(source, component, now, changed)
        return report

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
        async with self._uow_factory() as uow:
            await self._bus.publish(
                uow,
                EventEnvelope(
                    topic=CONNECTOR_HEALTH_TOPIC,
                    tenant_id=PLATFORM_TENANT_ID,
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
                    tenant_id=PLATFORM_TENANT_ID,
                    topic=CONNECTOR_HEALTH_TOPIC,
                    actor_type="system",
                    actor_id="connector-health",
                    payload=payload,
                )
            await uow.commit()
