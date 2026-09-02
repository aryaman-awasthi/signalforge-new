"""Response shapes for governance and safe execution (S6).

Five things are published as explicit fields rather than left for a client to
infer, because inferring any of them wrongly is a production incident:

- **`outcome`** on a decision is `allow`, `approval_required` or `block`, and
  `rules_hit` is the ordered list of rules that produced it, each naming whether
  it came from the platform `floor` or from `app_policy`. A cockpit renders the
  reason; an auditor replays it.
- **`locked`** on a policy row is computed by the domain, not by the browser. A
  high-risk action's row is uneditable because the floor says so, and the API
  says which rows those are so the two cannot disagree.
- **`retry_blocked`** on an execution travels beside `state`. `uncertain` and
  `retry_blocked` are one fact with two names, and a client that showed a retry
  button next to an uncertain outcome would be offering to apply a rollback
  twice.
- **`degraded`** on anything a model wrote. A deterministic sentence and a
  model's sentence are different evidence, and a reader who cannot tell them
  apart cannot calibrate.
- **`closure_blocked` and `closure_lock_reason`** on validation. The reason is a
  sentence, not a code, because it is rendered under a greyed-out Close button
  at three in the morning.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field

from signalforge.application.governance.comms_service import CommsView
from signalforge.application.governance.gateway import GatewayResult
from signalforge.application.governance.nba_service import NbaView
from signalforge.application.governance.policy_service import (
    ActionPolicyView,
    DryRunResult,
    PolicyView,
)
from signalforge.application.governance.validation_service import ValidationView
from signalforge.domain.governance.approvals import Approval
from signalforge.domain.governance.comms import Communication
from signalforge.domain.governance.ledger import ExecutionRecord
from signalforge.domain.governance.nba import ResponsePlan
from signalforge.domain.governance.policy import PolicyDecision, PolicyDocument
from signalforge.domain.governance.validation import ValidationCycle


# --- requests -----------------------------------------------------------------


class PolicyIn(BaseModel):
    source: str = Field(
        min_length=1,
        max_length=64_000,
        description=(
            "The application's policy as YAML. It sits above the platform safety "
            "floor and may only make things stricter: a document that would let "
            "a high-risk action auto-execute, lower a declared risk, lengthen an "
            "approval beyond the cap or disable audit is refused with the floor "
            "rule that refused it."
        ),
    )
    note: str = Field(default="", max_length=400)
    confirm_active_incident: bool = Field(
        default=False,
        description=(
            "Required when an incident on this application is open. Editing the "
            "policy mid-incident changes the rules under a running response and "
            "invalidates approvals already granted under the current version."
        ),
    )


class DryRunIn(BaseModel):
    source: str = Field(min_length=1, max_length=64_000)


class ExecutionIn(BaseModel):
    action_id: str = Field(min_length=1, max_length=100)
    args: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Validated against the schema the application published for this "
            "action. Values whose names look like credentials are redacted "
            "before anything is stored."
        ),
    )
    intent: Optional[str] = Field(
        default=None,
        description=(
            "manual, approval or auto. Asking for `auto` on a high-risk action "
            "does not raise - it produces a recorded BLOCK naming the floor rule, "
            "because an attempt to auto-execute a rollback is something a "
            "security review should be able to find."
        ),
    )
    note: str = Field(default="", max_length=400)
    run: bool = Field(
        default=True,
        description="Execute immediately if policy allows. An approval-required "
        "decision never executes regardless of this flag.",
    )


class NoteIn(BaseModel):
    note: str = Field(default="", max_length=400)


class TakeoverIn(BaseModel):
    note: str = Field(
        min_length=1,
        max_length=400,
        description="What the person is doing by hand. Required: a takeover with "
        "no record of what was done is worse than no state at all.",
    )


class PlanIn(BaseModel):
    changed_assumptions: list[str] = Field(default_factory=list, max_length=10)
    note: str = Field(default="", max_length=400)


class CommunicationIn(BaseModel):
    audience: str = Field(
        description="responders, leadership, customer_support or customer. The "
        "audience decides which permission approves it."
    )
    subject: str = Field(default="", max_length=200)
    body: str = Field(default="", max_length=4_000)
    node_ids: list[str] = Field(
        default_factory=list,
        max_length=40,
        description="Incident-graph node ids this text is drawn from. Left empty, "
        "the platform grounds the draft on the incident's current facts and "
        "impacts. A draft citing nothing is refused.",
    )
    use_model: bool = Field(default=True)


class CorrectionIn(BaseModel):
    subject: str = Field(default="", max_length=200)
    body: str = Field(default="", max_length=4_000)
    node_ids: list[str] = Field(default_factory=list, max_length=40)
    reason: str = Field(default="", max_length=400)
    use_model: bool = Field(default=True)


class ValidationIn(BaseModel):
    note: str = Field(default="", max_length=400)


# --- policy -------------------------------------------------------------------


class RuleHitOut(BaseModel):
    rule_id: str
    source: str = Field(description="floor, app_policy or manifest")
    effect: str
    detail: str


class DecisionOut(BaseModel):
    outcome: str = Field(description="allow, approval_required or block")
    action_id: str
    action_class: str
    risk_class: str = Field(
        description="read_only, low_risk, medium_risk or high_risk - the "
        "platform's classification, not the application's declaration"
    )
    reason: str
    rules_hit: list[RuleHitOut]
    approver_roles: list[str]
    approval_ttl_s: int
    policy_version: int
    policy_ref: str
    floor_version: str
    reversible: bool
    reverse_action_id: Optional[str] = None


def decision_out(decision: PolicyDecision) -> DecisionOut:
    return DecisionOut(**decision.as_dict())


class ActionRowOut(BaseModel):
    action_id: str
    name: str
    action_class: str
    manifest_risk: str
    risk_override: Optional[str] = None
    execution_mode: str
    enabled: bool
    reversible: bool
    idempotent: bool
    decision: DecisionOut
    locked: bool = Field(
        description="True for the six high-risk classes. The row is the platform "
        "safety floor: it cannot auto-execute and always needs approval, and no "
        "policy edit can change that. Computed in the domain, never a UI hint."
    )
    locked_reason: str


def action_row_out(view: ActionPolicyView) -> ActionRowOut:
    action = view.action
    return ActionRowOut(
        action_id=action.action_id,
        name=action.name,
        action_class=action.action_class.value,
        manifest_risk=action.manifest_risk.value,
        risk_override=action.risk_override.value if action.risk_override else None,
        execution_mode=action.execution_mode.value,
        enabled=action.enabled,
        reversible=action.reversible,
        idempotent=action.idempotent,
        decision=decision_out(view.decision),
        locked=view.locked,
        locked_reason=view.locked_reason,
    )


class PolicyVersionOut(BaseModel):
    version: int
    created_at: datetime
    author_id: str
    author_label: str
    note: str
    checksum: str
    approval_ttl_s: int


def policy_version_out(policy: PolicyDocument) -> PolicyVersionOut:
    return PolicyVersionOut(
        version=policy.version,
        created_at=policy.created_at,
        author_id=policy.author_id,
        author_label=policy.author_label,
        note=policy.note,
        checksum=policy.checksum,
        approval_ttl_s=policy.approval_ttl_s,
    )


class PolicyOut(BaseModel):
    app_id: str
    version: int
    source: str
    rules: dict[str, Any]
    actions: list[ActionRowOut]
    versions: list[PolicyVersionOut]
    floor: dict[str, Any] = Field(
        description="The immutable platform rules, published so the editor can "
        "state in the floor's own words why a row is locked."
    )
    open_incidents: list[str] = Field(
        description="Open incidents on this application. A non-empty list means "
        "an edit needs confirm_active_incident."
    )


def policy_out(view: PolicyView) -> PolicyOut:
    return PolicyOut(
        app_id=view.policy.app_id,
        version=view.policy.version,
        source=view.source,
        rules=view.policy.as_rules_dict(),
        actions=[action_row_out(row) for row in view.actions],
        versions=[policy_version_out(v) for v in view.versions],
        floor=dict(view.floor),
        open_incidents=list(view.open_incidents),
    )


class DryRunOut(BaseModel):
    accepted: bool
    error: str = Field(
        description="Empty when accepted. When refused, the floor's own message."
    )
    floor_rule: str
    changes: list[dict[str, Any]]
    before: list[ActionRowOut]
    after: list[ActionRowOut]


def dry_run_out(result: DryRunResult) -> DryRunOut:
    return DryRunOut(
        accepted=result.accepted,
        error=result.error,
        floor_rule=result.floor_rule,
        changes=[dict(change) for change in result.changes],
        before=[action_row_out(row) for row in result.before],
        after=[action_row_out(row) for row in result.after],
    )


# --- the execution ledger -------------------------------------------------------


class ApprovalOut(BaseModel):
    approval_id: str
    execution_id: str
    action_id: str
    state: str
    requested_by: str
    requested_at: datetime
    approver_user_id: str
    approver_label: str
    approver_roles: list[str]
    permission: Optional[str] = None
    required_roles: list[str]
    issued_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None
    seconds_left: int = Field(
        description="Zero when expired or never granted. The countdown on the "
        "approval sheet - and an expired approval is refused at execution time "
        "whether or not any client noticed."
    )
    policy_version: int
    note: str


def approval_out(approval: Approval, *, now: datetime) -> ApprovalOut:
    return ApprovalOut(
        approval_id=approval.id,
        execution_id=approval.execution_id,
        action_id=approval.action_id,
        state=approval.state.value,
        requested_by=approval.requested_by,
        requested_at=approval.requested_at,
        approver_user_id=approval.approver_user_id,
        approver_label=approval.approver_label,
        approver_roles=[r.value for r in approval.approver_roles],
        permission=approval.permission.value if approval.permission else None,
        required_roles=[r.value for r in approval.required_roles],
        issued_at=approval.issued_at,
        expires_at=approval.expires_at,
        seconds_left=approval.seconds_left(now),
        policy_version=approval.policy_version,
        note=approval.note,
    )


class ExecutionOut(BaseModel):
    execution_id: str
    incident_id: str
    app_id: str
    action_id: str
    action_class: str
    risk_class: str
    state: str
    attempt: int
    idempotency_key: str
    requested_by: str
    requested_at: datetime
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    args: dict[str, Any] = Field(
        description="Already redacted: argument names that look like credentials "
        "carry *** rather than a value."
    )
    decision: str
    decision_reason: str
    rules_hit: list[RuleHitOut]
    policy_version: int
    policy_ref: str
    floor_version: str
    approval_id: Optional[str] = None
    retry_blocked: bool = Field(
        description="True whenever the outcome at the target is not established. "
        "A client must not offer retry while this is set: reconcile first."
    )
    reconcile_outcome: Optional[str] = None
    reconciled_at: Optional[datetime] = None
    error: str
    error_category: str
    supersedes_execution_id: Optional[str] = None
    compensates_execution_id: Optional[str] = None
    compensated_by_execution_id: Optional[str] = None
    takeover_by: str
    takeover_note: str
    result_ref: str
    terminal: bool


def execution_out(record: ExecutionRecord) -> ExecutionOut:
    return ExecutionOut(
        execution_id=record.id,
        incident_id=record.incident_id,
        app_id=record.app_id,
        action_id=record.action_id,
        action_class=record.action_class.value,
        risk_class=record.risk_class.value,
        state=record.state.value,
        attempt=record.attempt,
        idempotency_key=record.idempotency_key,
        requested_by=record.requested_by,
        requested_at=record.requested_at,
        started_at=record.started_at,
        finished_at=record.finished_at,
        args=dict(record.args),
        decision=record.decision.value,
        decision_reason=record.decision_reason,
        rules_hit=[RuleHitOut(**dict(hit)) for hit in record.rules_hit],
        policy_version=record.policy_version,
        policy_ref=record.policy_ref,
        floor_version=record.floor_version,
        approval_id=record.approval_id,
        retry_blocked=record.retry_blocked,
        reconcile_outcome=(
            record.reconcile_outcome.value if record.reconcile_outcome else None
        ),
        reconciled_at=record.reconciled_at,
        error=record.error,
        error_category=record.error_category.value,
        supersedes_execution_id=record.supersedes_execution_id,
        compensates_execution_id=record.compensates_execution_id,
        compensated_by_execution_id=record.compensated_by_execution_id,
        takeover_by=record.takeover_by,
        takeover_note=record.takeover_note,
        result_ref=record.result_ref,
        terminal=record.state.terminal,
    )


class GatewayOut(BaseModel):
    execution: ExecutionOut
    decision: DecisionOut
    approval: Optional[ApprovalOut] = None
    duplicate: bool = Field(
        description="True when this exact request already existed. The external "
        "mutation was not sent a second time."
    )


def gateway_out(result: GatewayResult, *, now: datetime) -> GatewayOut:
    return GatewayOut(
        execution=execution_out(result.execution),
        decision=decision_out(result.decision),
        approval=(
            approval_out(result.approval, now=now) if result.approval else None
        ),
        duplicate=result.duplicate,
    )


class LedgerOut(BaseModel):
    executions: list[ExecutionOut]
    approvals: list[ApprovalOut]


# --- next best action -----------------------------------------------------------


class CandidateOut(BaseModel):
    action_id: str
    name: str
    action_class: str
    risk_class: str
    score: float = Field(
        description="urgency x impact x information_gain - risk. Deterministic "
        "arithmetic; a model may explain it and can never change it."
    )
    urgency: float
    impact: float
    information_gain: float
    risk: float
    eligible: bool
    eligibility: str
    eligibility_reason: str
    policy: DecisionOut
    requires_approval: bool
    approver_roles: list[str]
    hypothesis_key: str
    hypothesis_label: str
    hypothesis_confidence: float
    evidence_refs: list[str]
    prerequisites: list[str]
    unmet_prerequisites: list[str]
    expected_validation_s: int
    rollback_action_id: Optional[str] = None
    rollback_summary: str
    reversible: bool
    params_schema: dict[str, Any] = Field(
        default_factory=dict,
        description="The application's own JSON schema for this action's "
        "arguments. Published so a caller can ask a person for what the action "
        "needs; the Tool Gateway validates the answer against it either way.",
    )
    rationale: str
    rationale_degraded: bool = Field(
        description="True when the sentence is the platform's deterministic "
        "explanation rather than a model's."
    )


class PlanItemOut(BaseModel):
    action_id: str
    name: str
    disposition: str = Field(description="kept, paused or added")
    score: float
    reason: str
    changed_assumption: str
    evidence_refs: list[str]


class PlanOut(BaseModel):
    plan_id: str
    incident_id: str
    version: int
    previous_version: Optional[int] = None
    created_at: datetime
    created_by: str
    items: list[PlanItemOut]
    kept: list[str]
    paused: list[str]
    added: list[str]
    changed_assumptions: list[str]
    evidence_refs: list[str]
    note: str


def plan_out(plan: ResponsePlan) -> PlanOut:
    payload = plan.as_dict()
    payload["items"] = [PlanItemOut(**item) for item in payload["items"]]
    return PlanOut(**payload)


class NbaOut(BaseModel):
    primary: Optional[CandidateOut] = None
    alternatives: list[CandidateOut]
    ineligible: list[CandidateOut] = Field(
        description="Actions that cannot be performed right now, with the reason. "
        "Returned rather than hidden, and never the primary recommendation."
    )
    fallback: Optional[CandidateOut] = None
    degraded: bool
    generated_at: Optional[datetime] = None
    policy_version: int
    plan: Optional[PlanOut] = None
    previous_plan: Optional[PlanOut] = None


def nba_out(view: NbaView) -> NbaOut:
    payload = view.recommendation.as_dict()
    return NbaOut(
        primary=CandidateOut(**payload["primary"]) if payload["primary"] else None,
        alternatives=[CandidateOut(**c) for c in payload["alternatives"]],
        ineligible=[CandidateOut(**c) for c in payload["ineligible"]],
        fallback=CandidateOut(**payload["fallback"]) if payload["fallback"] else None,
        degraded=payload["degraded"],
        generated_at=view.recommendation.generated_at,
        policy_version=view.policy.version,
        plan=plan_out(view.plan) if view.plan else None,
        previous_plan=plan_out(view.previous_plan) if view.previous_plan else None,
    )


# --- communications -------------------------------------------------------------


class RedlineOut(BaseModel):
    old: str
    new: str
    reason: str
    evidence_refs: list[str]


class CommunicationOut(BaseModel):
    communication_id: str
    incident_id: str
    audience: str
    state: str = Field(
        description="draft, approved, issued, contradicted, corrected or "
        "withdrawn. Nothing moves to issued without a person."
    )
    subject: str
    body: str
    version: int
    created_at: datetime
    created_by: str
    grounded_node_ids: list[str] = Field(
        description="The incident-graph nodes this text is drawn from. A "
        "communication citing nothing does not exist."
    )
    model: str
    degraded: bool
    requires_permission: Optional[str] = None
    approved_by: str
    approved_by_label: str
    approved_at: Optional[datetime] = None
    issued_by: str
    issued_at: Optional[datetime] = None
    contradicted_at: Optional[datetime] = None
    contradiction_reason: str
    contradicting_node_ids: list[str]
    corrects_id: Optional[str] = None
    corrected_by_id: Optional[str] = None
    redline: list[RedlineOut]


def communication_out(communication: Communication) -> CommunicationOut:
    return CommunicationOut(
        communication_id=communication.id,
        incident_id=communication.incident_id,
        audience=communication.audience.value,
        state=communication.state.value,
        subject=communication.subject,
        body=communication.body,
        version=communication.version,
        created_at=communication.created_at,
        created_by=communication.created_by,
        grounded_node_ids=list(communication.grounded_node_ids),
        model=communication.model,
        degraded=communication.degraded,
        requires_permission=(
            communication.required_permission.value
            if communication.required_permission
            else None
        ),
        approved_by=communication.approved_by,
        approved_by_label=communication.approved_by_label,
        approved_at=communication.approved_at,
        issued_by=communication.issued_by,
        issued_at=communication.issued_at,
        contradicted_at=communication.contradicted_at,
        contradiction_reason=communication.contradiction_reason,
        contradicting_node_ids=list(communication.contradicting_node_ids),
        corrects_id=communication.corrects_id,
        corrected_by_id=communication.corrected_by_id,
        redline=[RedlineOut(**change.as_dict()) for change in communication.redline],
    )


class CommsOut(BaseModel):
    communications: list[CommunicationOut]
    contradicted: list[str]
    corrections: list[str]


def comms_out(view: CommsView) -> CommsOut:
    return CommsOut(
        communications=[communication_out(c) for c in view.communications],
        contradicted=[c.id for c in view.contradicted],
        corrections=[c.id for c in view.corrections],
    )


# --- layered validation ----------------------------------------------------------


class LayerCheckOut(BaseModel):
    check_id: str
    label: str
    verdict: str = Field(description="pass, fail, unknown or not_applicable")
    source: str = Field(
        description="The system that was read. Never the Incident Brain: recovery "
        "is checked against the application, not against the platform's own "
        "reasoning about it."
    )
    observed: str
    expected: str
    result_ref: str
    detail: str


class LayerOut(BaseModel):
    layer: str
    verdict: str
    summary: str
    checks: list[LayerCheckOut]
    failing: list[str]


class WindowOut(BaseModel):
    required_s: int
    required_samples: int
    consecutive_passes: int
    started_at: Optional[datetime] = None
    elapsed_s: int
    remaining_s: int
    satisfied: bool
    regressions: int = Field(
        description="Any regression resets the window to zero rather than "
        "decrementing it: intermittent green is not recovery."
    )
    last_regression_at: Optional[datetime] = None
    last_regression_reason: str


class ValidationOut(BaseModel):
    validation_id: str
    incident_id: str
    app_id: str
    cycle: int
    started_at: datetime
    finished_at: Optional[datetime] = None
    requested_by: str
    verdict: str = Field(description="recovered, stabilising or not_recovered")
    layers: list[LayerOut]
    window: Optional[WindowOut] = None
    closure_blocked: bool
    closure_lock_reason: str
    escalated: bool
    returned_to_active: bool
    evidence_node_ids: list[str] = Field(
        description="Graph nodes this cycle wrote. Validation feeds the "
        "reasoning; it never reads from it."
    )
    note: str


def validation_out(cycle: ValidationCycle, *, now: datetime) -> ValidationOut:
    payload = cycle.as_dict(now)
    payload["layers"] = [LayerOut(**layer) for layer in payload["layers"]]
    payload["window"] = WindowOut(**payload["window"]) if payload["window"] else None
    return ValidationOut(**payload)


class ValidationViewOut(BaseModel):
    latest: Optional[ValidationOut] = None
    history: list[ValidationOut]
    failed_cycles: int
    closure_blocked: bool
    closure_lock_reason: str


def validation_view_out(view: ValidationView, *, now: datetime) -> ValidationViewOut:
    return ValidationViewOut(
        latest=validation_out(view.latest, now=now) if view.latest else None,
        history=[validation_out(cycle, now=now) for cycle in view.history],
        failed_cycles=view.failed_cycles,
        closure_blocked=view.closure_blocked,
        closure_lock_reason=view.closure_lock_reason,
    )
