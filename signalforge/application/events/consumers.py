"""Consumer groups the platform itself runs (REQ-025).

BusMetricsConsumer is the first: it counts what flows through the bus by topic
and tracks the head of the log, which is what turns /metrics from a request
counter into a view of the event backbone. It is also what keeps the dispatcher
exercised in the running product from Sprint 2 onward, rather than only in
tests - the domain consumers that arrive in later sprints join the same loop.
"""

from __future__ import annotations

from signalforge.application.events.uow import EventUnitOfWork
from signalforge.contracts.envelope import EventEnvelope
from signalforge.domain.events.ports import EventHandler

BUS_METRICS_GROUP = "bus-metrics"


class BusMetricsConsumer(EventHandler):
    name = BUS_METRICS_GROUP

    def __init__(self, metrics):
        self._metrics = metrics

    async def handle(self, envelope: EventEnvelope, uow: EventUnitOfWork) -> None:
        self._metrics.increment(
            "signalforge_events_consumed_total", {"topic": envelope.topic}
        )
        if envelope.seq is not None:
            self._metrics.set_gauge("signalforge_event_seq", envelope.seq)
