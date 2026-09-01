"""Turning the platform's own topics into a readable history (REQ-072).

One table, read once. Every topic this platform publishes gets four things:

* a **headline** template, so a replay reads as sentences rather than as JSON;
* a **consequence**, so "somebody rolled back production" is not shown in the
  same weight as "somebody opened the brief";
* the payload keys that constitute its **justification** - the rules that were
  hit, the approval that was spent, the evidence that was cited, the person who
  decided;
* the payload keys that constitute its **effect** and its **references** - what
  changed and what can be opened to check.

A topic that is not in the table still replays. It gets its own topic as the
headline, `OBSERVATION` as its consequence, and whatever justification-shaped
keys its payload happens to carry. That is deliberate: a replay that silently
dropped an event it did not recognise would be the one thing an audit trail must
never do, and this way a new topic degrades to "less readable" rather than to
"invisible".
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Optional, Sequence

from signalforge.domain.audit.entries import AuditRecord, Consequence, ReplayEntry

JUSTIFICATION_KEYS: tuple[str, ...] = (
    "rules_hit",
    "floor_rule",
    "rejected_by",
    "reason",
    "note",
    "approval_id",
    "approver_user_id",
    "permission",
    "evidence",
    "evidence_node_ids",
    "evidence_refs",
    "grounded_node_ids",
    "closure_lock_reason",
    "contradiction_reason",
    "requires_human",
    "corroborated",
    "families",
    "detail",
    "rationale",
    "policy_version",
    "intent",
)
"""Payload keys that answer "why was this allowed to happen?".

Order matters: it is the order they are shown in, and it puts the controls
first, because "which rule permitted this" is the first question a reviewer
asks about a change to production.
"""

EFFECT_KEYS: tuple[str, ...] = (
    "state",
    "to_state",
    "new_state",
    "stance",
    "severity",
    "verdict",
    "outcome",
    "reconcile_outcome",
    "confidence",
    "band",
    "closure_blocked",
    "retry_blocked",
    "version",
)

REFERENCE_KEYS: tuple[str, ...] = (
    "result_ref",
    "execution_id",
    "idempotency_key",
    "incident_id",
    "communication_id",
    "validation_id",
    "seal_hash",
    "node_ids",
    "node_id",
    "page_id",
    "change_id",
    "action_id",
    "ticket_ref",
    "probe_id",
    "snapshot_id",
)


@dataclass(frozen=True)
class TopicMeaning:
    headline: str
    consequence: Consequence


CONSEQUENTIAL = Consequence.CONSEQUENTIAL
DECISION = Consequence.DECISION
OBSERVATION = Consequence.OBSERVATION
ADMIN = Consequence.ADMINISTRATIVE

MEANINGS: dict[str, TopicMeaning] = {
    # -- detection ------------------------------------------------------------
    "detection.stance.changed": TopicMeaning("Detection stance changed", DECISION),
    "detection.severity.proposed": TopicMeaning(
        "The platform proposed a severity - and declared nothing", DECISION
    ),
    "detection.anomaly.opened": TopicMeaning("A signal began breaching", OBSERVATION),
    "detection.anomaly.cleared": TopicMeaning("A signal stopped breaching", OBSERVATION),
    "detection.slo.burn": TopicMeaning("An error budget started burning", OBSERVATION),
    "detection.health.changed": TopicMeaning("The health score moved", OBSERVATION),
    "detection.alert.storm": TopicMeaning("An alert storm was clustered", OBSERVATION),
    "detection.signal.stale": TopicMeaning("A signal went stale", OBSERVATION),
    "detection.evaluation.forced": TopicMeaning(
        "Somebody forced an evaluation", DECISION
    ),
    # -- lifecycle ------------------------------------------------------------
    "incident.declared": TopicMeaning("A person declared the incident", CONSEQUENTIAL),
    "incident.severity.changed": TopicMeaning("The severity was changed", CONSEQUENTIAL),
    "incident.state.changed": TopicMeaning("The incident changed state", DECISION),
    "incident.closed": TopicMeaning("A person closed the incident", CONSEQUENTIAL),
    "incident.closure.blocked": TopicMeaning(
        "Closure was refused by the validation gate", CONSEQUENTIAL
    ),
    "incident.itsm.synced": TopicMeaning("The ticket was updated", ADMIN),
    "incident.itsm.sync.failed": TopicMeaning("The ticket update failed", OBSERVATION),
    "incident.itsm.sync.uncertain": TopicMeaning(
        "The ticket update outcome is unknown", OBSERVATION
    ),
    # -- war room -------------------------------------------------------------
    "warroom.opened": TopicMeaning("The war room opened", DECISION),
    "warroom.member.joined": TopicMeaning("Somebody joined the room", ADMIN),
    "warroom.membership.gap": TopicMeaning("A required role is missing", OBSERVATION),
    "warroom.page.sent": TopicMeaning("Somebody was paged", CONSEQUENTIAL),
    "warroom.page.acknowledged": TopicMeaning("A page was acknowledged", DECISION),
    "warroom.page.escalated": TopicMeaning(
        "An unanswered page escalated to the next person", CONSEQUENTIAL
    ),
    "warroom.page.escalation.exhausted": TopicMeaning(
        "The escalation chain ran out - nobody answered", CONSEQUENTIAL
    ),
    "warroom.page.failed": TopicMeaning("A page could not be delivered", OBSERVATION),
    "warroom.bridge.posted": TopicMeaning("Somebody spoke in the room", OBSERVATION),
    "warroom.task.created": TopicMeaning("A commitment became a task", DECISION),
    "warroom.task.updated": TopicMeaning("A task moved", ADMIN),
    "warroom.brief.read": TopicMeaning("A late joiner read the brief", ADMIN),
    "collaboration.message.received": TopicMeaning(
        "A message arrived from the bridge", OBSERVATION
    ),
    "notification.acknowledged": TopicMeaning(
        "A notification was acknowledged", DECISION
    ),
    # -- the brain ------------------------------------------------------------
    "brain.updated": TopicMeaning("The incident brain re-reasoned", DECISION),
    "brain.hypothesis.moved": TopicMeaning(
        "A hypothesis's confidence moved, with its arithmetic", DECISION
    ),
    "brain.contradiction.detected": TopicMeaning(
        "A human claim contradicts what was measured", DECISION
    ),
    "brain.guidance.raised": TopicMeaning("Historical guidance was raised", OBSERVATION),
    "brain.copilot.answered": TopicMeaning("The copilot answered a question", OBSERVATION),
    "brain.injection.observed": TopicMeaning(
        "Content tried to instruct the assistant - recorded, never followed",
        OBSERVATION,
    ),
    "topology.updated": TopicMeaning("A new topology version arrived", OBSERVATION),
    # -- policy ---------------------------------------------------------------
    "policy.version.created": TopicMeaning(
        "A new policy version was published", CONSEQUENTIAL
    ),
    "policy.floor.rejected": TopicMeaning(
        "The platform safety floor refused a policy change", CONSEQUENTIAL
    ),
    "policy.dry_run.evaluated": TopicMeaning("A policy was dry-run", ADMIN),
    "policy.edit.mid_incident": TopicMeaning(
        "A policy was edited during a live incident", CONSEQUENTIAL
    ),
    # -- the gateway ----------------------------------------------------------
    "execution.requested": TopicMeaning("An action was requested", CONSEQUENTIAL),
    "execution.decided": TopicMeaning("Policy decided on the action", DECISION),
    "execution.blocked": TopicMeaning("The action was blocked", CONSEQUENTIAL),
    "execution.approval.requested": TopicMeaning(
        "An approval was requested", CONSEQUENTIAL
    ),
    "execution.approval.granted": TopicMeaning(
        "A second person approved it", CONSEQUENTIAL
    ),
    "execution.approval.denied": TopicMeaning("A person refused it", CONSEQUENTIAL),
    "execution.approval.revoked": TopicMeaning("An approval was revoked", CONSEQUENTIAL),
    "execution.approval.expired": TopicMeaning("An approval expired unused", DECISION),
    "execution.approval.rejected": TopicMeaning(
        "An approval was presented and refused", CONSEQUENTIAL
    ),
    "execution.started": TopicMeaning("The gateway sent the action", CONSEQUENTIAL),
    "execution.succeeded": TopicMeaning("The action succeeded", CONSEQUENTIAL),
    "execution.failed": TopicMeaning("The action failed", CONSEQUENTIAL),
    "execution.uncertain": TopicMeaning(
        "The outcome is unknown - a timeout is not a failure", CONSEQUENTIAL
    ),
    "execution.retry.blocked": TopicMeaning(
        "A retry was refused until the outcome is reconciled", CONSEQUENTIAL
    ),
    "execution.retried": TopicMeaning("The action was retried", CONSEQUENTIAL),
    "execution.reconciled": TopicMeaning(
        "The application was asked what actually happened", CONSEQUENTIAL
    ),
    "execution.duplicate.prevented": TopicMeaning(
        "A duplicate submission was prevented", CONSEQUENTIAL
    ),
    "execution.compensating": TopicMeaning("A compensation was run", CONSEQUENTIAL),
    "execution.cancelled": TopicMeaning("The action was cancelled", DECISION),
    "execution.takeover": TopicMeaning("A person took over by hand", CONSEQUENTIAL),
    # -- next best action -----------------------------------------------------
    "nba.recommended": TopicMeaning("The platform recommended a next action", DECISION),
    "plan.revised": TopicMeaning("The plan was revised", DECISION),
    # -- communications -------------------------------------------------------
    "comms.drafted": TopicMeaning("A communication was drafted", DECISION),
    "comms.approved": TopicMeaning("A communication was approved", CONSEQUENTIAL),
    "comms.issued": TopicMeaning("A communication was issued", CONSEQUENTIAL),
    "comms.contradicted": TopicMeaning(
        "An issued communication was contradicted by later evidence", CONSEQUENTIAL
    ),
    "comms.correction.drafted": TopicMeaning("A correction was drafted", DECISION),
    # -- validation -----------------------------------------------------------
    "validation.started": TopicMeaning("Independent validation started", OBSERVATION),
    "validation.completed": TopicMeaning("Validation completed", DECISION),
    "validation.failed": TopicMeaning("Validation failed", CONSEQUENTIAL),
    "validation.escalated": TopicMeaning(
        "Validation failed repeatedly and escalated", CONSEQUENTIAL
    ),
    # -- the platform itself --------------------------------------------------
    "system.bus.sealed": TopicMeaning("A range of the record was sealed", CONSEQUENTIAL),
    "system.bus.dead_letter": TopicMeaning("An event went to the dead-letter queue", OBSERVATION),
    "system.bus.requeued": TopicMeaning("A dead letter was requeued", ADMIN),
    "system.ingest.rejected": TopicMeaning("An ingest batch was rejected", OBSERVATION),
    "system.app.policy.refused": TopicMeaning(
        "An application policy was refused", CONSEQUENTIAL
    ),
    "system.retention.pruned": TopicMeaning("Signal-plane data aged out", ADMIN),
}


def classify(record: AuditRecord, *, actor_labels: Mapping[str, str]) -> ReplayEntry:
    """One audit record, as a line a person can read and check."""
    meaning = MEANINGS.get(record.topic)
    headline = meaning.headline if meaning else _fallback_headline(record.topic)
    consequence = meaning.consequence if meaning else _fallback_consequence(record)
    payload = dict(record.payload or {})
    return ReplayEntry(
        seq=record.seq,
        event_id=record.event_id,
        topic=record.topic,
        at=record.occurred_at,
        headline=headline,
        actor_type=record.actor_type,
        actor_id=record.actor_id,
        actor_label=actor_labels.get(record.actor_id, _actor_fallback(record)),
        consequence=consequence,
        justification=_collect(payload, JUSTIFICATION_KEYS),
        effect=", ".join(_collect(payload, EFFECT_KEYS)),
        references=_collect(payload, REFERENCE_KEYS),
        payload=payload,
    )


def _fallback_headline(topic: str) -> str:
    """`comms.correction.drafted` -> `Comms correction drafted`."""
    words = topic.replace(".", " ").replace("_", " ").split()
    if not words:
        return topic
    return " ".join([words[0].capitalize(), *words[1:]])


def _fallback_consequence(record: AuditRecord) -> Consequence:
    """An unknown topic is an observation unless it is plainly about authority.

    Erring towards OBSERVATION rather than CONSEQUENTIAL, because a replay that
    marked everything consequential would be as useless as one that marked
    nothing.
    """
    if record.topic.startswith(("execution.", "policy.", "comms.")):
        return Consequence.CONSEQUENTIAL
    return Consequence.OBSERVATION


def _actor_fallback(record: AuditRecord) -> str:
    if record.actor_type == "human":
        return f"user {record.actor_id}"
    return record.actor_id or record.actor_type


def _collect(payload: Mapping[str, Any], keys: Sequence[str]) -> tuple[str, ...]:
    out: list[str] = []
    for key in keys:
        if key not in payload:
            continue
        rendered = _render(key, payload[key])
        if rendered:
            out.append(rendered)
    return tuple(out)


def _render(key: str, value: Any) -> Optional[str]:
    if value is None or value == "" or value == [] or value == {}:
        return None
    if key == "rules_hit" and isinstance(value, list):
        ids = [
            str(hit.get("rule_id"))
            for hit in value
            if isinstance(hit, dict) and hit.get("rule_id")
        ]
        return f"rules_hit={','.join(ids)}" if ids else None
    if isinstance(value, list):
        shown = ", ".join(str(item) for item in value[:5])
        more = "" if len(value) <= 5 else f" (+{len(value) - 5})"
        return f"{key}=[{shown}{more}]"
    if isinstance(value, dict):
        return f"{key}={{{', '.join(sorted(value))}}}"
    return f"{key}={value}"
