"""War-room topics, and the one way the room writes to the timeline.

Everything here lands on the **incident plane** with the incident id set, for the
same reasons `application/incidents/common.py` gives: incident events are kept
and sealed rather than aged out, and the incident is the partition key, so one
war room's events are ordered among themselves and never wait behind another's.

The actor is the honest part and it varies, which is why there are two writers
rather than one. A person acknowledging a page is `record` with
`ActorType.HUMAN`; an escalation firing because nobody answered is
`publish_system` with `ActorType.SYSTEM`. A platform that recorded an unanswered
page as though a person had done something would be lying in the one record a
post-incident review trusts.
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

SOURCE_SYSTEM = "war-room"

ROOM_OPENED_TOPIC = "warroom.opened"
ROOM_GAP_TOPIC = "warroom.membership.gap"
MEMBER_JOINED_TOPIC = "warroom.member.joined"
PAGE_SENT_TOPIC = "warroom.page.sent"
PAGE_FAILED_TOPIC = "warroom.page.failed"
PAGE_ACKED_TOPIC = "warroom.page.acknowledged"
PAGE_ESCALATED_TOPIC = "warroom.page.escalated"
PAGE_EXHAUSTED_TOPIC = "warroom.page.escalation.exhausted"
BRIDGE_POSTED_TOPIC = "warroom.bridge.posted"
TASK_CREATED_TOPIC = "warroom.task.created"
TASK_UPDATED_TOPIC = "warroom.task.updated"
BRIEF_READ_TOPIC = "warroom.brief.read"


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
) -> None:
    """A person acted in the war room. Audit entry and incident-plane event, one
    commit, inside the caller's transaction."""
    await uow.audit.append(
        tenant_id=principal.tenant_id,
        topic=topic,
        actor_type=ActorType.HUMAN.value,
        actor_id=principal.user_id,
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
                type=ActorType.HUMAN,
                id=principal.user_id,
                display=principal.display_name or None,
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
    audit: bool = True,
    actor_id: Optional[str] = None,
) -> None:
    """What the platform did about the room: opened it, paged somebody, gave up
    on somebody answering.

    Authoritative rather than observed, because SignalForge *is* the system of
    record for who it paged and when. The delivery underneath is somebody else's
    record, and the page row links to it rather than restating it.

    Audited by default. An escalation that woke a second person at 03:00 is a
    thing the platform did to somebody, and CLAUDE.md's audit floor means it is
    in the chain whether or not anyone asked for it.
    """
    if audit:
        await uow.audit.append(
            tenant_id=tenant_id,
            topic=topic,
            actor_type=ActorType.SYSTEM.value,
            actor_id=actor_id or SOURCE_SYSTEM,
            app_id=app_id,
            payload=dict(payload),
        )
    await bus.publish(
        uow,
        EventEnvelope(
            topic=topic,
            tenant_id=tenant_id,
            app_id=app_id,
            incident_id=incident_id,
            plane=Plane.INCIDENT,
            occurred_at=clock.now(),
            actor=Actor(type=ActorType.SYSTEM, id=actor_id or SOURCE_SYSTEM),
            source=Source(system=SOURCE_SYSTEM, authority=Authority.AUTHORITATIVE),
            payload=dict(payload),
        ),
    )
