"""The platform's own health sources (REQ-025).

These are real checks against real state, not placeholders: the database is
queried, the bus lag is computed from the stored offsets, and the worker reports
what its jobs actually did. Sprint 3 registers adapter instances alongside them
through the same HealthSource port, which is why the integration health screen
can show a vendor API and this platform's own database in one list.
"""

from __future__ import annotations

from typing import Callable, Optional

from sqlalchemy import text

from signalforge.application.events.uow import EventUnitOfWork
from signalforge.contracts.canonical import ComponentHealth, HealthStatus
from signalforge.domain.events.entities import DeadLetterState
from signalforge.domain.events.lanes import LANE_COUNT
from signalforge.domain.observability.health import HealthSource

BUS_LAG_DEGRADED = 1_000
BUS_LAG_DOWN = 50_000


class DatabaseHealthSource(HealthSource):
    source_id = "database"

    def __init__(self, session_factory):
        self._session_factory = session_factory

    async def check(self) -> ComponentHealth:
        async with self._session_factory() as session:
            await session.execute(text("select 1"))
        return ComponentHealth(
            component=self.source_id, status=HealthStatus.UP, detail="reachable"
        )


class EventBusHealthSource(HealthSource):
    """Healthy means consumers are keeping up and nothing is parked."""

    source_id = "event-bus"

    def __init__(self, uow_factory: Callable[[], EventUnitOfWork]):
        self._uow_factory = uow_factory

    async def check(self) -> ComponentHealth:
        lag, dead = await self.lag_and_dead_letters(self._uow_factory)
        if lag >= BUS_LAG_DOWN:
            status, detail = HealthStatus.DOWN, f"consumers {lag} events behind"
        elif lag >= BUS_LAG_DEGRADED or dead:
            status = HealthStatus.DEGRADED
            detail = f"lag {lag}, {dead} dead letters"
        else:
            status, detail = HealthStatus.UP, f"lag {lag}"
        return ComponentHealth(component=self.source_id, status=status, detail=detail)

    @staticmethod
    async def lag_and_dead_letters(
        uow_factory: Callable[[], EventUnitOfWork]
    ) -> tuple[int, int]:
        async with uow_factory() as uow:
            head = await uow.events.max_seq()
            offsets = list(await uow.offsets.list_all())
            counts = await uow.dead_letters.count_by_state()
        groups: dict[str, list[int]] = {}
        for group, _lane, last_seq in offsets:
            groups.setdefault(group, []).append(last_seq)
        lag = max(
            (head - min(seqs) for seqs in groups.values() if seqs),
            default=0,
        )
        return max(lag, 0), counts.get(DeadLetterState.DEAD.value, 0)


class WorkerHealthSource(HealthSource):
    source_id = "worker"

    def __init__(self, worker):
        self._worker = worker

    async def check(self) -> ComponentHealth:
        statuses = self._worker.statuses
        if not self._worker.running:
            return ComponentHealth(
                component=self.source_id,
                status=HealthStatus.DOWN,
                detail="worker group is not running",
            )
        broken = [s.name for s in statuses if not s.healthy]
        if broken:
            return ComponentHealth(
                component=self.source_id,
                status=HealthStatus.DEGRADED,
                detail=f"failing jobs: {', '.join(sorted(broken))}",
            )
        return ComponentHealth(
            component=self.source_id,
            status=HealthStatus.UP,
            detail=f"{len(statuses)} jobs, {LANE_COUNT} bus lanes",
        )
