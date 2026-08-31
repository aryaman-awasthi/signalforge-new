"""What an operator sees on the settings screen (REQ-025).

Two different audiences, two different shapes. /health answers a probe: is this
process able to serve, expressed as component statuses and nothing that
identifies anyone. /platform/status answers an operator behind RBAC: how far
behind are the consumers, what is parked in the dead-letter store, what has the
worker been doing, which clock is the platform on, and what has retention
removed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from signalforge.application.events.uow import EventUnitOfWork
from signalforge.application.observability.health import ConnectorHealthService
from signalforge.contracts.canonical import HealthReport, HealthStatus
from signalforge.domain.events.entities import DeadLetterState
from signalforge.domain.identity.ports import Clock
from signalforge.domain.observability.health import overall_status
from signalforge.infrastructure.observability.sources import EventBusHealthSource


@dataclass
class BusStatus:
    head_seq: int
    groups: dict[str, int]
    dead_letters: dict[str, int]

    @property
    def max_lag(self) -> int:
        return max(self.groups.values(), default=0)


@dataclass
class PlatformStatus:
    status: HealthStatus
    version: str
    checked_at: Any
    clock: dict[str, Any]
    bus: BusStatus
    worker: dict[str, Any]
    components: list[dict[str, Any]] = field(default_factory=list)
    retention: list[dict[str, Any]] = field(default_factory=list)


class PlatformStatusService:
    def __init__(
        self,
        uow_factory: Callable[[], EventUnitOfWork],
        health: ConnectorHealthService,
        worker,
        clock: Clock,
        *,
        version: str,
        retention_days: int,
    ):
        self._uow_factory = uow_factory
        self._health = health
        self._worker = worker
        self._clock = clock
        self._version = version
        self._retention_days = retention_days

    async def health_report(self) -> HealthReport:
        """Fresh check for the liveness probe - never a cached verdict, and only
        this node's own components: a customer's connector being down (S3) is
        not a reason for the process to answer 503."""
        return await self._health.platform_report()

    async def bus_status(self, tenant_id: Optional[str] = None) -> BusStatus:
        async with self._uow_factory() as uow:
            head = await uow.events.max_seq()
            offsets = list(await uow.offsets.list_all())
            dead = await uow.dead_letters.count_by_state(tenant_id)
        groups: dict[str, list[int]] = {}
        for group, _lane, last_seq in offsets:
            groups.setdefault(group, []).append(last_seq)
        return BusStatus(
            head_seq=head,
            groups={g: max(head - min(seqs), 0) for g, seqs in groups.items()},
            dead_letters={state.value: dead.get(state.value, 0) for state in DeadLetterState},
        )

    async def status(self, tenant_id: str) -> PlatformStatus:
        if self._health.last_checked_at is None:
            await self._health.report()
        # Platform components plus this tenant's connectors, never another
        # tenant's, even though one poll loop produced them all.
        components = self._health.components_for(tenant_id)
        checked_at = self._health.last_checked_at
        bus = await self.bus_status(tenant_id)
        async with self._uow_factory() as uow:
            watermarks = list(await uow.retention.list_for_tenant(tenant_id))
        clock = getattr(self._clock, "source", None)
        return PlatformStatus(
            status=overall_status(components),
            version=self._version,
            checked_at=checked_at,
            clock={
                "source": clock or "wall",
                "now": self._clock.now(),
                "speed": getattr(self._clock, "speed", 1.0),
                "paused": getattr(self._clock, "paused", False),
            },
            bus=bus,
            worker=self._worker.snapshot(),
            components=[
                {"component": c.component, "status": c.status.value, "detail": c.detail}
                for c in components
            ],
            retention=[
                {
                    "plane": w.plane.value,
                    "pruned_through_seq": w.pruned_through_seq,
                    "pruned_count": w.pruned_count,
                    "pruned_at": w.pruned_at,
                    "retention_days": self._retention_days,
                }
                for w in watermarks
            ],
        )
