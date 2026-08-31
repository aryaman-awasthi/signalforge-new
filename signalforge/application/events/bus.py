"""The publish side of the bus (REQ-020).

publish() takes the caller's unit of work rather than opening its own. That is
the whole point of the outbox: the use case that changed state hands the same
transaction to the bus, so there is no window in which the state exists without
its event or the event exists without its state.

recorded_at always comes from the recording clock - wall time, never a drill's
virtual clock - while occurred_at is whatever the emitter observed (REQ-024).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from signalforge.contracts.envelope import EventEnvelope
from signalforge.domain.identity.ports import Clock

if TYPE_CHECKING:  # pragma: no cover - typing only
    from signalforge.application.events.uow import EventUnitOfWork


class EventBus:
    def __init__(self, recording_clock: Clock, metrics: Optional[object] = None):
        self._recording_clock = recording_clock
        self._metrics = metrics

    async def publish(self, uow: "EventUnitOfWork", envelope: EventEnvelope) -> EventEnvelope:
        if envelope.recorded_at is None:
            envelope.recorded_at = self._recording_clock.now()
        stored = await uow.events.append(envelope)
        if self._metrics is not None:
            self._metrics.increment(
                "signalforge_events_published_total", {"topic": stored.topic}
            )
        return stored
