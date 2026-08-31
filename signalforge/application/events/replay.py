"""Replay (REQ-023).

Everything downstream - the cockpit timeline, the audit scrubber, a consumer
rebuilding a projection - reads history through this one service, so the tenant
predicate and the bounds checks exist once. The tenant is taken from the
authenticated principal and never from the query string.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional, Sequence

from signalforge.application.events.uow import EventUnitOfWork
from signalforge.application.identity.principal import Principal
from signalforge.contracts.envelope import EventEnvelope, Plane
from signalforge.domain.events.errors import EventNotFoundError, ReplayRangeError

MAX_REPLAY_LIMIT = 1000
DEFAULT_REPLAY_LIMIT = 200


@dataclass(frozen=True)
class ReplayPage:
    events: tuple[EventEnvelope, ...]
    after_seq: int
    next_after_seq: int
    has_more: bool

    @property
    def count(self) -> int:
        return len(self.events)


class ReplayService:
    def __init__(self, uow_factory: Callable[[], EventUnitOfWork]):
        self._uow_factory = uow_factory

    async def read(
        self,
        principal: Principal,
        *,
        after_seq: int = 0,
        until: Optional[int] = None,
        topics: Optional[Sequence[str]] = None,
        incident_id: Optional[str] = None,
        plane: Optional[Plane] = None,
        limit: int = DEFAULT_REPLAY_LIMIT,
    ) -> ReplayPage:
        if after_seq < 0:
            raise ReplayRangeError("after_seq must not be negative")
        if until is not None and until <= after_seq:
            raise ReplayRangeError("until must be greater than after_seq")
        if not 1 <= limit <= MAX_REPLAY_LIMIT:
            raise ReplayRangeError(f"limit must be between 1 and {MAX_REPLAY_LIMIT}")

        async with self._uow_factory() as uow:
            # One extra row tells us whether another page exists without a count.
            rows = list(
                await uow.events.read(
                    tenant_id=principal.tenant_id,
                    after_seq=after_seq,
                    until_seq=until,
                    topics=topics,
                    incident_id=incident_id,
                    plane=plane,
                    limit=limit + 1,
                )
            )
        has_more = len(rows) > limit
        events = tuple(rows[:limit])
        return ReplayPage(
            events=events,
            after_seq=after_seq,
            next_after_seq=events[-1].seq if events else after_seq,
            has_more=has_more,
        )

    async def get(self, principal: Principal, event_id: str) -> EventEnvelope:
        async with self._uow_factory() as uow:
            envelope = await uow.events.get(principal.tenant_id, event_id)
        if envelope is None:
            raise EventNotFoundError(f"event {event_id} not found")
        return envelope

    async def causal_chain(
        self, principal: Principal, event_id: str
    ) -> tuple[EventEnvelope, ...]:
        """The exact causal chain around an event: its root cause, and every
        event that root transitively caused, in recording order."""
        async with self._uow_factory() as uow:
            if await uow.events.get(principal.tenant_id, event_id) is None:
                raise EventNotFoundError(f"event {event_id} not found")
            chain = await uow.events.causal_chain(
                tenant_id=principal.tenant_id, event_id=event_id
            )
        return tuple(chain)
