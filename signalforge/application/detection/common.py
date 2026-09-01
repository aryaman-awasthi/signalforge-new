"""Topics and the two ways detection writes to the timeline.

Detection publishes as a **machine**: `ActorType.SYSTEM`, `Authority.OBSERVED`,
on the signal plane. Observed rather than authoritative, deliberately - the
platform measured this, it is not the system of record for it, and an evidence
chain that cannot tell a measurement from a source of truth is one an auditor
cannot use.

Two writers, because two different things happen:

- `publish` is the loop. Event only, no audit entry: a poller noticing that a
  threshold was crossed is an observation, and an audit chain that filled with
  observations would stop being a record of what people did.
- `record_action` is a person. Audit entry **and** event, in one transaction,
  exactly as every other human action in this platform is recorded - because
  "somebody forced an evaluation at 03:04" is an act, and acts are audited.
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

SOURCE_SYSTEM = "detection"
SYSTEM_ACTOR_ID = "detection"

STANCE_TOPIC = "detection.stance.changed"
ANOMALY_OPENED_TOPIC = "detection.anomaly.opened"
ANOMALY_CLEARED_TOPIC = "detection.anomaly.cleared"
SIGNAL_STALE_TOPIC = "detection.signal.stale"
BURN_TOPIC = "detection.slo.burn"
HEALTH_TOPIC = "detection.health.changed"
PROPOSAL_TOPIC = "detection.severity.proposed"
STORM_TOPIC = "detection.alert.storm"
POLL_DEGRADED_TOPIC = "system.detection.poll.degraded"
POLL_RECOVERED_TOPIC = "system.detection.poll.recovered"
EVALUATION_FORCED_TOPIC = "detection.evaluation.forced"

ALERT_TOPICS: tuple[str, ...] = ("alert.fired", "alert.resolved")


async def publish(
    uow: IdentityUnitOfWork,
    bus: EventBus,
    clock: Clock,
    *,
    tenant_id: str,
    app_id: str,
    topic: str,
    payload: Mapping[str, Any],
    incident_id: Optional[str] = None,
) -> None:
    """One machine observation on the tenant's timeline, inside the caller's
    transaction."""
    await bus.publish(
        uow,
        EventEnvelope(
            topic=topic,
            tenant_id=tenant_id,
            app_id=app_id,
            incident_id=incident_id,
            plane=Plane.SIGNAL,
            occurred_at=clock.now(),
            actor=Actor(type=ActorType.SYSTEM, id=SYSTEM_ACTOR_ID),
            source=Source(system=SOURCE_SYSTEM, authority=Authority.OBSERVED),
            payload=dict(payload),
        ),
    )


async def record_action(
    uow: IdentityUnitOfWork,
    bus: EventBus,
    clock: Clock,
    *,
    principal: Principal,
    app_id: str,
    topic: str,
    payload: Mapping[str, Any],
) -> None:
    """A person acted on detection. Audit entry and event, one commit."""
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
            plane=Plane.SIGNAL,
            occurred_at=clock.now(),
            actor=Actor(type=ActorType.HUMAN, id=principal.user_id),
            source=Source(system=SOURCE_SYSTEM, authority=Authority.AUTHORITATIVE),
            payload=dict(payload),
        ),
    )
