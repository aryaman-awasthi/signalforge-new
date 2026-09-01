"""Topics, and the one way the lifecycle writes to the timeline.

Detection publishes as a machine, on the signal plane, with `observed`
authority. The lifecycle publishes the opposite of that in every dimension, and
the contrast is the point:

- **incident plane.** Signal-plane events age out by configuration; incident
  events are kept and sealed (REQ-023). What an incident *was* has to survive
  retention, and a declaration that could be pruned would make a post-incident
  review depend on how long it took to hold one.
- **human actor.** Every write here is somebody pressing something. The actor is
  the session's own user id, never a service name.
- **authoritative.** SignalForge is the system of record for its own incident
  lifecycle. It is not the system of record for the telemetry that suggested one,
  which is why detection's events are `observed` and these are not.
- **incident_id set.** That makes the incident the partition key, so one
  incident's events are ordered among themselves and never wait behind another's
  (domain/events/lanes.py), and it is what an incident-scoped replay and a seal
  select on.

Every write is one audit entry and one event inside the caller's transaction, so
the chain and the timeline cannot disagree about what a person did.
"""

from __future__ import annotations

from typing import Any, Mapping, Optional

from signalforge.application.events.bus import EventBus
from signalforge.application.identity.principal import Principal
from signalforge.application.identity.uow import IdentityUnitOfWork
from signalforge.contracts.envelope import (
    Actor,
    ActorType,
    Authority,
    EventEnvelope,
    Plane,
    Source,
)
from signalforge.domain.identity.ports import Clock

SOURCE_SYSTEM = "lifecycle"

DECLARED_TOPIC = "incident.declared"
SEVERITY_TOPIC = "incident.severity.changed"
STATE_TOPIC = "incident.state.changed"
CLOSED_TOPIC = "incident.closed"
ITSM_SYNCED_TOPIC = "incident.itsm.synced"
ITSM_FAILED_TOPIC = "incident.itsm.sync.failed"
ITSM_UNCERTAIN_TOPIC = "incident.itsm.sync.uncertain"

CHANGE_TOPIC_PATTERNS: tuple[str, ...] = ("deployment.*", "change.*")
"""What the change-window projection subscribes to. Derived from the ingest
domain's own rule - `domain/ingest/streams.topic_for` publishes a change as
`deployment.<status>` when its type is a deployment and `change.<status>`
otherwise - rather than restated as a fixed list of four."""

CHANGE_WINDOW_GROUP = "change-windows"


async def record(
    uow: IdentityUnitOfWork,
    bus: EventBus,
    clock: Clock,
    *,
    principal: Principal,
    app_id: str,
    incident_id: str,
    topic: str,
    payload: Mapping[str, Any],
    actor_type: ActorType = ActorType.HUMAN,
    actor_id: Optional[str] = None,
) -> None:
    """A person acted on an incident. Audit entry and incident-plane event, one
    commit, inside the caller's transaction."""
    who = actor_id or principal.user_id
    await uow.audit.append(
        tenant_id=principal.tenant_id,
        topic=topic,
        actor_type=actor_type.value,
        actor_id=who,
        app_id=app_id,
        payload=dict(payload),
    )
    await bus.publish(
        uow,
        EventEnvelope(
            topic=topic,
            tenant_id=principal.tenant_id,
            app_id=app_id,
            incident_id=incident_id,
            plane=Plane.INCIDENT,
            occurred_at=clock.now(),
            actor=Actor(
                type=actor_type, id=who, display=principal.display_name or None
            ),
            source=Source(system=SOURCE_SYSTEM, authority=Authority.AUTHORITATIVE),
            payload=dict(payload),
        ),
    )


async def publish_system(
    uow: IdentityUnitOfWork,
    bus: EventBus,
    clock: Clock,
    *,
    tenant_id: str,
    app_id: str,
    incident_id: str,
    topic: str,
    payload: Mapping[str, Any],
) -> None:
    """What the platform did about an incident, rather than what a person did.

    Used for the ITSM sync, which is a machine talking to another machine. It is
    still on the incident plane - "we raised a ticket, or could not" is part of
    what the incident was - but the actor is the system and the authority is
    `observed`, because the ticket queue is the system of record for tickets.
    """
    await bus.publish(
        uow,
        EventEnvelope(
            topic=topic,
            tenant_id=tenant_id,
            app_id=app_id,
            incident_id=incident_id,
            plane=Plane.INCIDENT,
            occurred_at=clock.now(),
            actor=Actor(type=ActorType.SYSTEM, id=SOURCE_SYSTEM),
            source=Source(system=SOURCE_SYSTEM, authority=Authority.OBSERVED),
            payload=dict(payload),
        ),
    )
