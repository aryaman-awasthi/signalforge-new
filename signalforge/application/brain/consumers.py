"""The bus consumer that wakes the incident brain (REQ-052).

A consumer group like any other, and it does almost nothing: it marks the
incident as having something to think about and returns. The reasoning happens
in a scheduled job that drains the set.

That split is not fastidiousness. A governor pass reads several stores, may call
a model over the network and writes a transaction of its own; doing that inside
the dispatcher's transaction would hold a SQLite write lock for seconds against a
loop that is supposed to take milliseconds, and a second connection opened while
the first is held is a deadlock waiting for a busy night. Waking is cheap and
in-process; the work is somebody else's turn.

It is also what makes redelivery harmless. A wake is a key in a set, so an event
delivered twice wakes the same incident twice and produces one pass - and the
pass itself is idempotent, because a graph write against an unchanged world
writes nothing (`IncidentGraphRepository.upsert`).
"""

from __future__ import annotations

from signalforge.application.events.uow import EventUnitOfWork
from signalforge.contracts.envelope import EventEnvelope
from signalforge.domain.events.ports import EventHandler


class IncidentBrainConsumer(EventHandler):
    name = "incident-brain"

    def __init__(self, governor, metrics=None):
        self._governor = governor
        self._metrics = metrics

    async def handle(self, envelope: EventEnvelope, uow: EventUnitOfWork) -> None:
        incident_id = envelope.incident_id
        if not incident_id:
            # An incident-plane event about an application rather than an
            # incident - a change window folding, say. There is no brain to
            # wake, and inventing one would create graphs for incidents that do
            # not exist.
            return
        self._governor.wake(envelope.tenant_id, incident_id, envelope.topic)
        if self._metrics is not None:
            self._metrics.increment(
                "signalforge_brain_wakes_total", {"topic": envelope.topic}
            )
