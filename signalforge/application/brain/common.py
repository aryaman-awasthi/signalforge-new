"""Incident-brain topics, and the one way the brain writes to the timeline.

Everything here lands on the **incident plane** with the incident id set, for the
same reasons the war room's does: incident events are kept and sealed rather
than aged out, and the incident is the partition key, so one brain's events are
ordered among themselves and never wait behind another's.

The actor is always `AGENT` and the authority is always `INFERRED`, and both are
load-bearing rather than decorative. The brain does not measure anything: it
correlates things other subsystems measured. An event from here recorded as
`OBSERVED` would put a correlation into the audit chain wearing the authority of
a measurement, which is precisely the confusion this sprint exists to prevent.

Audited by default, because CLAUDE.md's audit floor is not conditional on who
acted. What an agent concluded about a P0, and on what evidence, is a thing a
post-incident review reads.
"""

from __future__ import annotations

from typing import Any, Mapping, Optional

from signalforge.application.events.bus import EventBus
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

SOURCE_SYSTEM = "incident-brain"

BRAIN_UPDATED_TOPIC = "brain.updated"
"""One per governor pass that changed something. Deliberately one rather than one
per node: a pass that writes forty nodes must not put forty rows on a timeline a
person is reading."""
HYPOTHESIS_MOVED_TOPIC = "brain.hypothesis.moved"
CONTRADICTION_TOPIC = "brain.contradiction.detected"
INJECTION_TOPIC = "brain.injection.observed"
COPILOT_ANSWERED_TOPIC = "brain.copilot.answered"
GUIDANCE_TOPIC = "brain.guidance.raised"

BRAIN_TOPIC_PATTERNS: tuple[str, ...] = ("brain.*",)
"""What the cockpit consumer adds to its subscription. The war-room screen
already re-reads on `incident.*` and `warroom.*`; the brain joins the same
socket rather than opening a second one (REQ-033)."""


async def publish_agent(
    uow: IdentityUnitOfWork,
    bus: EventBus,
    clock: Clock,
    *,
    tenant_id: str,
    app_id: str,
    incident_id: str,
    topic: str,
    payload: Mapping[str, Any],
    agent: str = SOURCE_SYSTEM,
    audit: bool = True,
    confidence: Optional[float] = None,
) -> None:
    """What an agent concluded, on the timeline and in the audit chain.

    `confidence` rides on the envelope's own field rather than only in the
    payload, so a reader filtering the timeline for low-confidence conclusions
    does not have to know each topic's payload shape.
    """
    if audit:
        await uow.audit.append(
            tenant_id=tenant_id,
            topic=topic,
            actor_type=ActorType.AGENT.value,
            actor_id=agent,
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
            actor=Actor(type=ActorType.AGENT, id=agent),
            # Inferred, always. The brain correlates; it does not observe.
            source=Source(system=SOURCE_SYSTEM, authority=Authority.INFERRED),
            confidence=confidence,
            payload=dict(payload),
        ),
    )
