"""Governance topics, and the two ways this area writes to the record.

Everything here lands on the **incident plane** with the incident id set, for
the reason the war room's events do: incident events are kept and sealed rather
than aged out, and the incident is the partition key, so one incident's
execution transitions are ordered among themselves.

Two writers rather than one, and the split is the honesty rule. A person
approving a rollback is `record` with `ActorType.HUMAN`. A reconciliation sweep
discovering that a timed-out rollback actually landed is `publish_system` with
`ActorType.SYSTEM`. A platform that recorded the second as though a person had
done it would be lying in the one record a post-incident review trusts - and in
this area, that record is what says who authorised a change to production.

Everything in this module audits by default. CLAUDE.md rule 4 makes the audit
chain permanent, and there is no `audit=False` on the human writer at all: an act
that changes somebody's estate, or authorises one, is in the chain whether or not
a caller remembered to ask.
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

SOURCE_SYSTEM = "governance"

# Policy (REQ-060)
POLICY_CREATED_TOPIC = "policy.version.created"
POLICY_FLOOR_REJECTED_TOPIC = "policy.floor.rejected"
"""The event the guide's "a control actively preventing an unsafe action" check
reads. Written when somebody tries to configure a high-risk action to
auto-execute, or otherwise contradict the floor."""
POLICY_DRY_RUN_TOPIC = "policy.dry_run.evaluated"
POLICY_MID_INCIDENT_TOPIC = "policy.edit.mid_incident"

# Tool Gateway and the ledger (REQ-061, REQ-062)
EXECUTION_REQUESTED_TOPIC = "execution.requested"
EXECUTION_DECIDED_TOPIC = "execution.decided"
EXECUTION_BLOCKED_TOPIC = "execution.blocked"
EXECUTION_STARTED_TOPIC = "execution.started"
EXECUTION_SUCCEEDED_TOPIC = "execution.succeeded"
EXECUTION_FAILED_TOPIC = "execution.failed"
EXECUTION_UNCERTAIN_TOPIC = "execution.uncertain"
EXECUTION_RECONCILED_TOPIC = "execution.reconciled"
EXECUTION_DUPLICATE_TOPIC = "execution.duplicate.prevented"
EXECUTION_RETRIED_TOPIC = "execution.retried"
EXECUTION_RETRY_BLOCKED_TOPIC = "execution.retry.blocked"
EXECUTION_COMPENSATING_TOPIC = "execution.compensating"
EXECUTION_CANCELLED_TOPIC = "execution.cancelled"
EXECUTION_TAKEOVER_TOPIC = "execution.takeover"
APPROVAL_REQUESTED_TOPIC = "execution.approval.requested"
APPROVAL_GRANTED_TOPIC = "execution.approval.granted"
APPROVAL_DENIED_TOPIC = "execution.approval.denied"
APPROVAL_REVOKED_TOPIC = "execution.approval.revoked"
APPROVAL_EXPIRED_TOPIC = "execution.approval.expired"
APPROVAL_REJECTED_TOPIC = "execution.approval.rejected"
"""An approval was presented at execution time and refused - expired, spent,
bound to a different request, or granted by somebody who has since lost the
permission. Separate from `denied`, which is a person saying no."""

# Next best action (REQ-063)
NBA_COMPUTED_TOPIC = "nba.recommended"
PLAN_REVISED_TOPIC = "plan.revised"

# Communications (REQ-064)
COMMS_DRAFTED_TOPIC = "comms.drafted"
COMMS_APPROVED_TOPIC = "comms.approved"
COMMS_ISSUED_TOPIC = "comms.issued"
COMMS_CONTRADICTED_TOPIC = "comms.contradicted"
COMMS_CORRECTION_TOPIC = "comms.correction.drafted"

# Layered validation (REQ-065)
VALIDATION_STARTED_TOPIC = "validation.started"
VALIDATION_COMPLETED_TOPIC = "validation.completed"
VALIDATION_FAILED_TOPIC = "validation.failed"
VALIDATION_ESCALATED_TOPIC = "validation.escalated"
CLOSURE_BLOCKED_TOPIC = "incident.closure.blocked"

GOVERNANCE_TOPIC_PATTERNS: tuple[str, ...] = (
    "policy.*",
    "execution.*",
    "nba.*",
    "plan.*",
    "comms.*",
    "validation.*",
)
"""What the cockpit socket subscribes to so an approval countdown and a ledger
transition arrive on the frame the platform was already sending."""


async def record(
    uow: IdentityUnitOfWork,
    bus: EventBus,
    clock: Clock,
    *,
    principal: Principal,
    app_id: str,
    incident_id: Optional[str],
    topic: str,
    payload: Mapping[str, Any],
) -> None:
    """A person did something consequential. Audit entry and incident-plane
    event, one commit, inside the caller's transaction."""
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
    incident_id: Optional[str],
    topic: str,
    payload: Mapping[str, Any],
    actor_id: Optional[str] = None,
) -> None:
    """What the platform did on its own: reconciled an uncertain outcome, expired
    an approval, refused a retry, ran a validation sweep.

    Authoritative rather than observed - SignalForge *is* the system of record
    for what it decided and when. Always audited: there is no parameter to turn
    it off, because the events this writer produces are precisely the ones a
    security review comes looking for.
    """
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
