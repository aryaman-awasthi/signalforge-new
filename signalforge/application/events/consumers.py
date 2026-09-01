"""Consumer groups the platform itself runs (REQ-025).

BusMetricsConsumer is the first: it counts what flows through the bus by topic
and tracks the head of the log, which is what turns /metrics from a request
counter into a view of the event backbone. It is also what keeps the dispatcher
exercised in the running product from Sprint 2 onward, rather than only in
tests - the domain consumers that arrive in later sprints join the same loop.
"""

from __future__ import annotations

from typing import Optional, Protocol

from signalforge.application.events.uow import EventUnitOfWork
from signalforge.contracts.envelope import EventEnvelope
from signalforge.domain.events.ports import EventHandler

BUS_METRICS_GROUP = "bus-metrics"

COCKPIT_SOCKET_GROUP = "cockpit-socket"

COCKPIT_TOPIC_PATTERNS: tuple[str, ...] = (
    "incident.*",
    "warroom.*",
    "brain.*",
    "execution.*",
    "policy.*",
    "comms.*",
    "validation.*",
    "nba.*",
    "plan.*",
)
"""What a war-room screen is refreshed by. Deliberately not `detection.*`: the
app-detail screen reads a stance that is recomputed every five seconds anyway,
and nudging on every evaluation would turn a five-second poll into a five-second
push with more moving parts and no more truth.

`brain.*` joins the same subscription rather than opening a second socket
(S5-T05): a hypothesis that moved is exactly the kind of thing a cockpit should
learn about without waiting for a poll, and the frame still carries no payload -
the screen re-reads through the authorised route it was already polling.

The Sprint 6 families join for the same reason and matter more (S6-T06). An
execution that has just landed `uncertain`, an approval whose countdown started,
a communication the platform has flagged as contradicted and a validation cycle
that refused closure are all things a war room must not learn about on the next
poll. The frame still carries no payload: the screen re-reads through the route
it is already authorised for, so a push can never widen what a viewer can see."""


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


class SocketNudgeSink(Protocol):
    """What the cockpit consumer needs from the socket fan-out, and no more."""

    def nudge(self, tenant_id: str, incident_id: str, frame: dict) -> int: ...


class CockpitSocketConsumer(EventHandler):
    """Turns incident and war-room events into "something moved" frames (REQ-045).

    This is the push half of the cockpit, and its most important property is
    what it does *not* send. The frame names the incident, the topic and the
    sequence number; it carries no payload at all. The screen re-reads through
    the same authorised HTTP route it was already polling, so the socket never
    becomes a second read path with its own authorisation to get wrong, and a
    nudge for a room the reader may not read produces a 403 on the re-read
    rather than a leak.

    It is a consumer group like any other, which is what makes it recoverable:
    a node whose socket fan-out was down does not lose the events, it lags, and
    the offset catches up. And a nudge that never arrives costs nothing but
    latency, because every screen that listens is still polling underneath.
    """

    name = COCKPIT_SOCKET_GROUP

    def __init__(self, hub: SocketNudgeSink, metrics=None):
        self._hub = hub
        self._metrics = metrics

    async def handle(self, envelope: EventEnvelope, uow: EventUnitOfWork) -> None:
        incident_id: Optional[str] = envelope.incident_id
        if not incident_id:
            # An incident event that names no incident is a lifecycle event about
            # an application (a change window folding, say). Nothing is watching
            # an incident id for it, so there is nobody to nudge.
            return
        delivered = self._hub.nudge(
            envelope.tenant_id,
            incident_id,
            {
                "type": "room.changed",
                "incident_id": incident_id,
                "topic": envelope.topic,
                "seq": envelope.seq,
            },
        )
        if self._metrics is not None and delivered:
            self._metrics.increment(
                "signalforge_socket_nudges_total", {"topic": envelope.topic}
            )
