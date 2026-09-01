"""Governance repositories (REQ-060..REQ-065, REQ-013).

Tenant isolation lives here, exactly as it does in every other repository in this
codebase: the predicate is inside the class, and a cross-tenant read returns
None so the service above answers the same 404 it would for an id that exists
nowhere.

Two methods deliberately span tenants and both are work lists for background
jobs that serve the whole install: `due_for_reconciliation` (UNCERTAIN rows a
dead process left behind) and `expire_due` (approvals whose clock ran out). Every
row they return carries its own `tenant_id` and everything the jobs then write
goes back through the tenant-scoped methods - the same shape the escalation sweep
and the ITSM retry already have.

The duplicate-prevention control is the unique index on
`(tenant_id, idempotency_key)`, and `add` lets the `IntegrityError` surface as a
`DuplicateExecutionError` rather than pre-checking. A read-then-write would let
two concurrent submits both find nothing and both execute, which is the exact
failure the key exists to prevent.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping, Optional, Sequence

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from signalforge.contracts.canonical import ActionClass, ReconcileOutcome, RiskLevel
from signalforge.contracts.roles import Permission, Role
from signalforge.domain.governance.approvals import Approval, ApprovalState
from signalforge.domain.governance.comms import (
    Audience,
    Communication,
    CommunicationState,
    RedlineChange,
)
from signalforge.domain.governance.errors import DuplicateExecutionError
from signalforge.domain.governance.ledger import (
    ErrorCategory,
    ExecutionRecord,
    LedgerState,
)
from signalforge.domain.governance.nba import Disposition, PlanItem, ResponsePlan
from signalforge.domain.governance.policy import (
    ActionRule,
    Outcome,
    PolicyDocument,
    RiskClass,
)
from signalforge.domain.governance.ports import (
    ApprovalRepository,
    CommunicationRepository,
    ExecutionLedgerRepository,
    PolicyRepository,
    ResponsePlanRepository,
    ValidationRepository,
)
from signalforge.domain.governance.validation import (
    LayerCheck,
    LayerResult,
    LayerVerdict,
    RecoveryVerdict,
    StabilityWindow,
    ValidationCycle,
    ValidationLayer,
)
from signalforge.infrastructure.persistence.models import (
    ApprovalRow,
    CommunicationRow,
    ExecutionLedgerRow,
    GovernancePolicyRow,
    ResponsePlanRow,
    ValidationCycleRow,
)


def _roles(values: Sequence[str]) -> tuple[Role, ...]:
    """Role strings that no longer exist in the matrix are dropped rather than
    raising: a stored policy must stay readable across a matrix change, and a
    role that cannot be resolved cannot grant anything anyway."""
    out: list[Role] = []
    for value in values or ():
        try:
            out.append(Role(value))
        except ValueError:
            continue
    return tuple(out)


def _classes(values: Sequence[str]) -> frozenset[ActionClass]:
    out: set[ActionClass] = set()
    for value in values or ():
        try:
            out.add(ActionClass(value))
        except ValueError:
            continue
    return frozenset(out)


# --- policy --------------------------------------------------------------------


class SqlPolicyRepository(PolicyRepository):
    def __init__(self, session: AsyncSession):
        self._s = session

    async def add(self, document: PolicyDocument) -> PolicyDocument:
        self._s.add(
            GovernancePolicyRow(
                id=f"{document.tenant_id}:{document.app_id}:{document.version}",
                tenant_id=document.tenant_id,
                app_id=document.app_id,
                version=document.version,
                created_at=document.created_at,
                author_id=document.author_id,
                author_label=document.author_label,
                note=document.note,
                approval_ttl_s=document.approval_ttl_s,
                stability_window_s=document.stability_window_s,
                stability_samples=document.stability_samples,
                max_action_retries=document.max_action_retries,
                revalidation_cycles=document.revalidation_cycles_before_escalation,
                blocked_classes=sorted(c.value for c in document.blocked_classes),
                require_approval_classes=sorted(
                    c.value for c in document.require_approval_classes
                ),
                approver_roles=[r.value for r in document.approver_roles],
                action_rules={
                    action_id: rule.as_dict()
                    for action_id, rule in document.action_rules.items()
                },
                checksum=document.checksum,
                source=document.source,
            )
        )
        await self._s.flush()
        return document

    async def latest(self, tenant_id: str, app_id: str) -> Optional[PolicyDocument]:
        row = (
            await self._s.execute(
                select(GovernancePolicyRow)
                .where(
                    GovernancePolicyRow.tenant_id == tenant_id,
                    GovernancePolicyRow.app_id == app_id,
                )
                .order_by(GovernancePolicyRow.version.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        return _policy_of(row) if row is not None else None

    async def get(
        self, tenant_id: str, app_id: str, version: int
    ) -> Optional[PolicyDocument]:
        row = (
            await self._s.execute(
                select(GovernancePolicyRow).where(
                    GovernancePolicyRow.tenant_id == tenant_id,
                    GovernancePolicyRow.app_id == app_id,
                    GovernancePolicyRow.version == int(version),
                )
            )
        ).scalar_one_or_none()
        return _policy_of(row) if row is not None else None

    async def history(
        self, tenant_id: str, app_id: str, *, limit: int = 50
    ) -> Sequence[PolicyDocument]:
        rows = (
            (
                await self._s.execute(
                    select(GovernancePolicyRow)
                    .where(
                        GovernancePolicyRow.tenant_id == tenant_id,
                        GovernancePolicyRow.app_id == app_id,
                    )
                    .order_by(GovernancePolicyRow.version.desc())
                    .limit(int(limit))
                )
            )
            .scalars()
            .all()
        )
        return [_policy_of(row) for row in rows]


def _policy_of(row: GovernancePolicyRow) -> PolicyDocument:
    return PolicyDocument(
        tenant_id=row.tenant_id,
        app_id=row.app_id,
        version=row.version,
        created_at=row.created_at,
        author_id=row.author_id,
        author_label=row.author_label,
        note=row.note,
        approval_ttl_s=row.approval_ttl_s,
        stability_window_s=row.stability_window_s,
        stability_samples=row.stability_samples,
        max_action_retries=row.max_action_retries,
        revalidation_cycles_before_escalation=row.revalidation_cycles,
        blocked_classes=_classes(row.blocked_classes or []),
        require_approval_classes=_classes(row.require_approval_classes or []),
        approver_roles=_roles(row.approver_roles or []),
        action_rules={
            action_id: _rule_of(action_id, payload)
            for action_id, payload in (row.action_rules or {}).items()
        },
        source=row.source or "",
    )


def _rule_of(action_id: str, payload: Mapping[str, Any]) -> ActionRule:
    override = payload.get("risk_override")
    return ActionRule(
        action_id=action_id,
        blocked=bool(payload.get("blocked")),
        require_approval=bool(payload.get("require_approval")),
        auto_execute=bool(payload.get("auto_execute")),
        risk_override=RiskLevel(override) if override else None,
        approver_roles=_roles(payload.get("approver_roles") or []),
        approval_ttl_s=payload.get("approval_ttl_s"),
        note=payload.get("note") or "",
    )


# --- execution ledger ----------------------------------------------------------


class SqlExecutionLedgerRepository(ExecutionLedgerRepository):
    def __init__(self, session: AsyncSession):
        self._s = session

    async def add(self, record: ExecutionRecord) -> ExecutionRecord:
        self._s.add(ExecutionLedgerRow(id=record.id, **_execution_values(record)))
        try:
            await self._s.flush()
        except IntegrityError as exc:
            # The unique index did its job: somebody submitted this exact request
            # twice. The service turns this into "here is the execution you
            # already have" rather than a second mutation.
            raise DuplicateExecutionError(
                f"an execution with idempotency key {record.idempotency_key} "
                "already exists in this tenant; the request was not sent again"
            ) from exc
        return record

    async def update(self, record: ExecutionRecord) -> ExecutionRecord:
        row = await self._row(record.tenant_id, record.id)
        if row is None:
            return record
        for key, value in _execution_values(record).items():
            setattr(row, key, value)
        await self._s.flush()
        return record

    async def get(
        self, tenant_id: str, execution_id: str
    ) -> Optional[ExecutionRecord]:
        row = await self._row(tenant_id, execution_id)
        return _execution_of(row) if row is not None else None

    async def find_by_key(
        self, tenant_id: str, idempotency_key: str
    ) -> Optional[ExecutionRecord]:
        row = (
            await self._s.execute(
                select(ExecutionLedgerRow).where(
                    ExecutionLedgerRow.tenant_id == tenant_id,
                    ExecutionLedgerRow.idempotency_key == idempotency_key,
                )
            )
        ).scalar_one_or_none()
        return _execution_of(row) if row is not None else None

    async def list(
        self,
        tenant_id: str,
        incident_id: str,
        *,
        states: Optional[Sequence[LedgerState]] = None,
        limit: int = 200,
    ) -> Sequence[ExecutionRecord]:
        query = select(ExecutionLedgerRow).where(
            ExecutionLedgerRow.tenant_id == tenant_id,
            ExecutionLedgerRow.incident_id == incident_id,
        )
        if states:
            query = query.where(
                ExecutionLedgerRow.state.in_([s.value for s in states])
            )
        rows = (
            (
                await self._s.execute(
                    query.order_by(ExecutionLedgerRow.requested_at.desc()).limit(
                        int(limit)
                    )
                )
            )
            .scalars()
            .all()
        )
        return [_execution_of(row) for row in rows]

    async def open_for_action(
        self, tenant_id: str, incident_id: str, action_id: str
    ) -> Sequence[ExecutionRecord]:
        open_states = [s.value for s in LedgerState if not s.terminal]
        rows = (
            (
                await self._s.execute(
                    select(ExecutionLedgerRow)
                    .where(
                        ExecutionLedgerRow.tenant_id == tenant_id,
                        ExecutionLedgerRow.incident_id == incident_id,
                        ExecutionLedgerRow.action_id == action_id,
                        ExecutionLedgerRow.state.in_(open_states),
                    )
                    .order_by(ExecutionLedgerRow.requested_at.desc())
                )
            )
            .scalars()
            .all()
        )
        return [_execution_of(row) for row in rows]

    async def due_for_reconciliation(
        self, *, older_than: datetime, limit: int = 50
    ) -> Sequence[ExecutionRecord]:
        rows = (
            (
                await self._s.execute(
                    select(ExecutionLedgerRow)
                    .where(
                        ExecutionLedgerRow.state == LedgerState.UNCERTAIN.value,
                        ExecutionLedgerRow.requested_at <= older_than,
                    )
                    .order_by(ExecutionLedgerRow.requested_at)
                    .limit(int(limit))
                )
            )
            .scalars()
            .all()
        )
        return [_execution_of(row) for row in rows]

    async def _row(
        self, tenant_id: str, execution_id: str
    ) -> Optional[ExecutionLedgerRow]:
        return (
            await self._s.execute(
                select(ExecutionLedgerRow).where(
                    ExecutionLedgerRow.tenant_id == tenant_id,
                    ExecutionLedgerRow.id == execution_id,
                )
            )
        ).scalar_one_or_none()


def _execution_values(record: ExecutionRecord) -> dict[str, Any]:
    return {
        "tenant_id": record.tenant_id,
        "incident_id": record.incident_id,
        "app_id": record.app_id,
        "action_id": record.action_id,
        "action_class": record.action_class.value,
        "risk_class": record.risk_class.value,
        "state": record.state.value,
        "idempotency_key": record.idempotency_key,
        "attempt": record.attempt,
        "requested_by": record.requested_by,
        "requested_at": record.requested_at,
        "args": dict(record.args),
        "args_digest": record.args_digest,
        "decision": record.decision.value,
        "decision_reason": record.decision_reason,
        "rules_hit": [dict(hit) for hit in record.rules_hit],
        "policy_version": record.policy_version,
        "policy_ref": record.policy_ref,
        "floor_version": record.floor_version,
        "decision_fingerprint": record.decision_fingerprint,
        "approval_id": record.approval_id,
        "binding_id": record.binding_id,
        "adapter_execution_id": record.adapter_execution_id,
        "started_at": record.started_at,
        "finished_at": record.finished_at,
        "error": record.error,
        "error_category": record.error_category.value,
        "retry_blocked": record.retry_blocked,
        "reconcile_outcome": (
            record.reconcile_outcome.value if record.reconcile_outcome else None
        ),
        "reconciled_at": record.reconciled_at,
        "reconcile_detail": dict(record.reconcile_detail),
        "compensates_execution_id": record.compensates_execution_id,
        "compensated_by_execution_id": record.compensated_by_execution_id,
        "supersedes_execution_id": record.supersedes_execution_id,
        "takeover_by": record.takeover_by,
        "takeover_note": record.takeover_note,
        "result_ref": record.result_ref,
        "detail": dict(record.detail),
    }


def _execution_of(row: ExecutionLedgerRow) -> ExecutionRecord:
    return ExecutionRecord(
        id=row.id,
        tenant_id=row.tenant_id,
        incident_id=row.incident_id,
        app_id=row.app_id,
        action_id=row.action_id,
        action_class=ActionClass(row.action_class),
        risk_class=RiskClass(row.risk_class),
        state=LedgerState(row.state),
        idempotency_key=row.idempotency_key,
        attempt=row.attempt,
        requested_by=row.requested_by,
        requested_at=row.requested_at,
        args=dict(row.args or {}),
        args_digest=row.args_digest or "",
        decision=Outcome(row.decision),
        decision_reason=row.decision_reason or "",
        rules_hit=tuple(dict(hit) for hit in (row.rules_hit or [])),
        policy_version=row.policy_version,
        policy_ref=row.policy_ref or "",
        floor_version=row.floor_version or "",
        decision_fingerprint=row.decision_fingerprint or "",
        approval_id=row.approval_id,
        binding_id=row.binding_id or "",
        adapter_execution_id=row.adapter_execution_id or "",
        started_at=row.started_at,
        finished_at=row.finished_at,
        error=row.error or "",
        error_category=ErrorCategory(row.error_category or "none"),
        retry_blocked=bool(row.retry_blocked),
        reconcile_outcome=(
            ReconcileOutcome(row.reconcile_outcome) if row.reconcile_outcome else None
        ),
        reconciled_at=row.reconciled_at,
        reconcile_detail=dict(row.reconcile_detail or {}),
        compensates_execution_id=row.compensates_execution_id,
        compensated_by_execution_id=row.compensated_by_execution_id,
        supersedes_execution_id=row.supersedes_execution_id,
        takeover_by=row.takeover_by or "",
        takeover_note=row.takeover_note or "",
        result_ref=row.result_ref or "",
        detail=dict(row.detail or {}),
    )


# --- approvals -----------------------------------------------------------------


class SqlApprovalRepository(ApprovalRepository):
    def __init__(self, session: AsyncSession):
        self._s = session

    async def add(self, approval: Approval) -> Approval:
        self._s.add(ApprovalRow(id=approval.id, **_approval_values(approval)))
        await self._s.flush()
        return approval

    async def update(self, approval: Approval) -> Approval:
        row = await self._row(approval.tenant_id, approval.id)
        if row is None:
            return approval
        for key, value in _approval_values(approval).items():
            setattr(row, key, value)
        await self._s.flush()
        return approval

    async def get(self, tenant_id: str, approval_id: str) -> Optional[Approval]:
        row = await self._row(tenant_id, approval_id)
        return _approval_of(row) if row is not None else None

    async def for_execution(
        self, tenant_id: str, execution_id: str
    ) -> Sequence[Approval]:
        rows = (
            (
                await self._s.execute(
                    select(ApprovalRow)
                    .where(
                        ApprovalRow.tenant_id == tenant_id,
                        ApprovalRow.execution_id == execution_id,
                    )
                    .order_by(ApprovalRow.requested_at.desc())
                )
            )
            .scalars()
            .all()
        )
        return [_approval_of(row) for row in rows]

    async def list(
        self, tenant_id: str, incident_id: str, *, limit: int = 100
    ) -> Sequence[Approval]:
        rows = (
            (
                await self._s.execute(
                    select(ApprovalRow)
                    .where(
                        ApprovalRow.tenant_id == tenant_id,
                        ApprovalRow.incident_id == incident_id,
                    )
                    .order_by(ApprovalRow.requested_at.desc())
                    .limit(int(limit))
                )
            )
            .scalars()
            .all()
        )
        return [_approval_of(row) for row in rows]

    async def expire_due(
        self, *, now: datetime, limit: int = 100
    ) -> Sequence[Approval]:
        rows = (
            (
                await self._s.execute(
                    select(ApprovalRow)
                    .where(
                        ApprovalRow.state == ApprovalState.GRANTED.value,
                        ApprovalRow.expires_at.is_not(None),
                        ApprovalRow.expires_at <= now,
                    )
                    .limit(int(limit))
                )
            )
            .scalars()
            .all()
        )
        expired: list[Approval] = []
        for row in rows:
            approval = _approval_of(row).expired(at=now)
            row.state = approval.state.value
            row.revoked_at = approval.revoked_at
            expired.append(approval)
        if rows:
            await self._s.flush()
        return expired

    async def _row(self, tenant_id: str, approval_id: str) -> Optional[ApprovalRow]:
        return (
            await self._s.execute(
                select(ApprovalRow).where(
                    ApprovalRow.tenant_id == tenant_id,
                    ApprovalRow.id == approval_id,
                )
            )
        ).scalar_one_or_none()


def _approval_values(approval: Approval) -> dict[str, Any]:
    return {
        "tenant_id": approval.tenant_id,
        "incident_id": approval.incident_id,
        "app_id": approval.app_id,
        "execution_id": approval.execution_id,
        "action_id": approval.action_id,
        "state": approval.state.value,
        "requested_by": approval.requested_by,
        "requested_at": approval.requested_at,
        "decision_fingerprint": approval.decision_fingerprint,
        "policy_version": approval.policy_version,
        "policy_ref": approval.policy_ref,
        "required_roles": [r.value for r in approval.required_roles],
        "approver_user_id": approval.approver_user_id,
        "approver_label": approval.approver_label,
        "approver_roles": [r.value for r in approval.approver_roles],
        "permission": approval.permission.value if approval.permission else None,
        "issued_at": approval.issued_at,
        "expires_at": approval.expires_at,
        "used_at": approval.used_at,
        "revoked_at": approval.revoked_at,
        "note": approval.note,
        "detail": dict(approval.detail),
    }


def _approval_of(row: ApprovalRow) -> Approval:
    return Approval(
        id=row.id,
        tenant_id=row.tenant_id,
        incident_id=row.incident_id,
        app_id=row.app_id,
        execution_id=row.execution_id,
        action_id=row.action_id,
        state=ApprovalState(row.state),
        requested_by=row.requested_by,
        requested_at=row.requested_at,
        decision_fingerprint=row.decision_fingerprint,
        policy_version=row.policy_version,
        policy_ref=row.policy_ref or "",
        required_roles=_roles(row.required_roles or []),
        approver_user_id=row.approver_user_id or "",
        approver_label=row.approver_label or "",
        approver_roles=_roles(row.approver_roles or []),
        permission=Permission(row.permission) if row.permission else None,
        issued_at=row.issued_at,
        expires_at=row.expires_at,
        used_at=row.used_at,
        revoked_at=row.revoked_at,
        note=row.note or "",
        detail=dict(row.detail or {}),
    )


# --- response plans ------------------------------------------------------------


class SqlResponsePlanRepository(ResponsePlanRepository):
    def __init__(self, session: AsyncSession):
        self._s = session

    async def add(self, plan: ResponsePlan) -> ResponsePlan:
        self._s.add(
            ResponsePlanRow(
                id=plan.id,
                tenant_id=plan.tenant_id,
                incident_id=plan.incident_id,
                version=plan.version,
                previous_version=plan.previous_version,
                created_at=plan.created_at,
                created_by=plan.created_by,
                items=[item.as_dict() for item in plan.items],
                changed_assumptions=list(plan.changed_assumptions),
                evidence_refs=list(plan.evidence_refs),
                note=plan.note,
                detail=dict(plan.detail),
            )
        )
        await self._s.flush()
        return plan

    async def latest(
        self, tenant_id: str, incident_id: str
    ) -> Optional[ResponsePlan]:
        row = (
            await self._s.execute(
                select(ResponsePlanRow)
                .where(
                    ResponsePlanRow.tenant_id == tenant_id,
                    ResponsePlanRow.incident_id == incident_id,
                )
                .order_by(ResponsePlanRow.version.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        return _plan_of(row) if row is not None else None

    async def list(
        self, tenant_id: str, incident_id: str, *, limit: int = 20
    ) -> Sequence[ResponsePlan]:
        rows = (
            (
                await self._s.execute(
                    select(ResponsePlanRow)
                    .where(
                        ResponsePlanRow.tenant_id == tenant_id,
                        ResponsePlanRow.incident_id == incident_id,
                    )
                    .order_by(ResponsePlanRow.version.desc())
                    .limit(int(limit))
                )
            )
            .scalars()
            .all()
        )
        return [_plan_of(row) for row in rows]


def _plan_of(row: ResponsePlanRow) -> ResponsePlan:
    return ResponsePlan(
        id=row.id,
        tenant_id=row.tenant_id,
        incident_id=row.incident_id,
        version=row.version,
        created_at=row.created_at,
        created_by=row.created_by,
        items=tuple(
            PlanItem(
                action_id=item.get("action_id", ""),
                name=item.get("name", ""),
                disposition=Disposition(item.get("disposition", "kept")),
                score=float(item.get("score") or 0.0),
                reason=item.get("reason", ""),
                changed_assumption=item.get("changed_assumption", ""),
                evidence_refs=tuple(item.get("evidence_refs") or ()),
            )
            for item in (row.items or [])
        ),
        changed_assumptions=tuple(row.changed_assumptions or ()),
        evidence_refs=tuple(row.evidence_refs or ()),
        previous_version=row.previous_version,
        note=row.note or "",
        detail=dict(row.detail or {}),
    )


# --- communications ------------------------------------------------------------


class SqlCommunicationRepository(CommunicationRepository):
    def __init__(self, session: AsyncSession):
        self._s = session

    async def add(self, communication: Communication) -> Communication:
        self._s.add(
            CommunicationRow(id=communication.id, **_comm_values(communication))
        )
        await self._s.flush()
        return communication

    async def update(self, communication: Communication) -> Communication:
        row = await self._row(communication.tenant_id, communication.id)
        if row is None:
            return communication
        for key, value in _comm_values(communication).items():
            setattr(row, key, value)
        await self._s.flush()
        return communication

    async def get(
        self, tenant_id: str, communication_id: str
    ) -> Optional[Communication]:
        row = await self._row(tenant_id, communication_id)
        return _comm_of(row) if row is not None else None

    async def list(
        self,
        tenant_id: str,
        incident_id: str,
        *,
        audience: Optional[Audience] = None,
        limit: int = 100,
    ) -> Sequence[Communication]:
        query = select(CommunicationRow).where(
            CommunicationRow.tenant_id == tenant_id,
            CommunicationRow.incident_id == incident_id,
        )
        if audience is not None:
            query = query.where(CommunicationRow.audience == audience.value)
        rows = (
            (
                await self._s.execute(
                    query.order_by(CommunicationRow.created_at.desc()).limit(int(limit))
                )
            )
            .scalars()
            .all()
        )
        return [_comm_of(row) for row in rows]

    async def issued_grounded_on(
        self, tenant_id: str, incident_id: str, node_ids: Sequence[str]
    ) -> Sequence[Communication]:
        wanted = set(node_ids)
        if not wanted:
            return []
        rows = (
            (
                await self._s.execute(
                    select(CommunicationRow).where(
                        CommunicationRow.tenant_id == tenant_id,
                        CommunicationRow.incident_id == incident_id,
                        CommunicationRow.state == CommunicationState.ISSUED.value,
                    )
                )
            )
            .scalars()
            .all()
        )
        return [
            _comm_of(row)
            for row in rows
            if wanted.intersection(row.grounded_node_ids or [])
        ]

    async def _row(
        self, tenant_id: str, communication_id: str
    ) -> Optional[CommunicationRow]:
        return (
            await self._s.execute(
                select(CommunicationRow).where(
                    CommunicationRow.tenant_id == tenant_id,
                    CommunicationRow.id == communication_id,
                )
            )
        ).scalar_one_or_none()


def _comm_values(c: Communication) -> dict[str, Any]:
    return {
        "tenant_id": c.tenant_id,
        "incident_id": c.incident_id,
        "app_id": c.app_id,
        "audience": c.audience.value,
        "state": c.state.value,
        "subject": c.subject,
        "body": c.body,
        "version": c.version,
        "created_at": c.created_at,
        "created_by": c.created_by,
        "grounded_node_ids": list(c.grounded_node_ids),
        "evidence_refs": list(c.evidence_refs),
        "model": c.model,
        "model_class": c.model_class,
        "degraded": c.degraded,
        "approved_by": c.approved_by,
        "approved_by_label": c.approved_by_label,
        "approved_permission": (
            c.approved_permission.value if c.approved_permission else None
        ),
        "approved_at": c.approved_at,
        "issued_by": c.issued_by,
        "issued_at": c.issued_at,
        "contradicted_at": c.contradicted_at,
        "contradiction_reason": c.contradiction_reason,
        "contradicting_node_ids": list(c.contradicting_node_ids),
        "corrects_id": c.corrects_id,
        "corrected_by_id": c.corrected_by_id,
        "redline": [change.as_dict() for change in c.redline],
        "withdrawn_at": c.withdrawn_at,
        "note": c.note,
        "detail": dict(c.detail),
    }


def _comm_of(row: CommunicationRow) -> Communication:
    return Communication(
        id=row.id,
        tenant_id=row.tenant_id,
        incident_id=row.incident_id,
        app_id=row.app_id,
        audience=Audience(row.audience),
        state=CommunicationState(row.state),
        subject=row.subject,
        body=row.body,
        version=row.version,
        created_at=row.created_at,
        created_by=row.created_by,
        grounded_node_ids=tuple(row.grounded_node_ids or ()),
        evidence_refs=tuple(row.evidence_refs or ()),
        model=row.model or "",
        model_class=row.model_class or "",
        degraded=bool(row.degraded),
        approved_by=row.approved_by or "",
        approved_by_label=row.approved_by_label or "",
        approved_permission=(
            Permission(row.approved_permission) if row.approved_permission else None
        ),
        approved_at=row.approved_at,
        issued_by=row.issued_by or "",
        issued_at=row.issued_at,
        contradicted_at=row.contradicted_at,
        contradiction_reason=row.contradiction_reason or "",
        contradicting_node_ids=tuple(row.contradicting_node_ids or ()),
        corrects_id=row.corrects_id,
        corrected_by_id=row.corrected_by_id,
        redline=tuple(
            RedlineChange(
                old=change.get("old", ""),
                new=change.get("new", ""),
                reason=change.get("reason", ""),
                evidence_refs=tuple(change.get("evidence_refs") or ()),
            )
            for change in (row.redline or [])
        ),
        withdrawn_at=row.withdrawn_at,
        note=row.note or "",
        detail=dict(row.detail or {}),
    )


# --- validation ----------------------------------------------------------------


class SqlValidationRepository(ValidationRepository):
    def __init__(self, session: AsyncSession):
        self._s = session

    async def add(self, cycle: ValidationCycle) -> ValidationCycle:
        self._s.add(ValidationCycleRow(id=cycle.id, **_cycle_values(cycle)))
        await self._s.flush()
        return cycle

    async def update(self, cycle: ValidationCycle) -> ValidationCycle:
        row = await self._row(cycle.tenant_id, cycle.id)
        if row is None:
            return cycle
        for key, value in _cycle_values(cycle).items():
            setattr(row, key, value)
        await self._s.flush()
        return cycle

    async def get(
        self, tenant_id: str, validation_id: str
    ) -> Optional[ValidationCycle]:
        row = await self._row(tenant_id, validation_id)
        return _cycle_of(row) if row is not None else None

    async def latest(
        self, tenant_id: str, incident_id: str
    ) -> Optional[ValidationCycle]:
        row = (
            await self._s.execute(
                select(ValidationCycleRow)
                .where(
                    ValidationCycleRow.tenant_id == tenant_id,
                    ValidationCycleRow.incident_id == incident_id,
                )
                .order_by(ValidationCycleRow.cycle.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        return _cycle_of(row) if row is not None else None

    async def list(
        self, tenant_id: str, incident_id: str, *, limit: int = 50
    ) -> Sequence[ValidationCycle]:
        rows = (
            (
                await self._s.execute(
                    select(ValidationCycleRow)
                    .where(
                        ValidationCycleRow.tenant_id == tenant_id,
                        ValidationCycleRow.incident_id == incident_id,
                    )
                    .order_by(ValidationCycleRow.cycle.desc())
                    .limit(int(limit))
                )
            )
            .scalars()
            .all()
        )
        return [_cycle_of(row) for row in rows]

    async def failed_cycles(self, tenant_id: str, incident_id: str) -> int:
        rows = (
            (
                await self._s.execute(
                    select(ValidationCycleRow.verdict).where(
                        ValidationCycleRow.tenant_id == tenant_id,
                        ValidationCycleRow.incident_id == incident_id,
                        ValidationCycleRow.verdict
                        == RecoveryVerdict.NOT_RECOVERED.value,
                    )
                )
            )
            .scalars()
            .all()
        )
        return len(rows)

    async def _row(
        self, tenant_id: str, validation_id: str
    ) -> Optional[ValidationCycleRow]:
        return (
            await self._s.execute(
                select(ValidationCycleRow).where(
                    ValidationCycleRow.tenant_id == tenant_id,
                    ValidationCycleRow.id == validation_id,
                )
            )
        ).scalar_one_or_none()


def _cycle_values(cycle: ValidationCycle) -> dict[str, Any]:
    return {
        "tenant_id": cycle.tenant_id,
        "incident_id": cycle.incident_id,
        "app_id": cycle.app_id,
        "cycle": cycle.cycle,
        "started_at": cycle.started_at,
        "finished_at": cycle.finished_at,
        "requested_by": cycle.requested_by,
        "verdict": cycle.verdict.value,
        "layers": [result.as_dict() for result in cycle.layers],
        "window": _window_values(cycle.window),
        "escalated": cycle.escalated,
        "returned_to_active": cycle.returned_to_active,
        "evidence_node_ids": list(cycle.evidence_node_ids),
        "note": cycle.note,
        "detail": dict(cycle.detail),
    }


def _window_values(window: Optional[StabilityWindow]) -> dict[str, Any]:
    if window is None:
        return {}
    return {
        "required_s": window.required_s,
        "required_samples": window.required_samples,
        "started_at": window.started_at.isoformat() if window.started_at else None,
        "consecutive_passes": window.consecutive_passes,
        "last_sample_at": (
            window.last_sample_at.isoformat() if window.last_sample_at else None
        ),
        "regressions": window.regressions,
        "last_regression_at": (
            window.last_regression_at.isoformat()
            if window.last_regression_at
            else None
        ),
        "last_regression_reason": window.last_regression_reason,
    }


def _window_of(payload: Mapping[str, Any]) -> Optional[StabilityWindow]:
    if not payload:
        return None
    return StabilityWindow(
        required_s=int(payload.get("required_s") or 0),
        required_samples=int(payload.get("required_samples") or 1),
        started_at=_dt(payload.get("started_at")),
        consecutive_passes=int(payload.get("consecutive_passes") or 0),
        last_sample_at=_dt(payload.get("last_sample_at")),
        regressions=int(payload.get("regressions") or 0),
        last_regression_at=_dt(payload.get("last_regression_at")),
        last_regression_reason=payload.get("last_regression_reason") or "",
    )


def _dt(value: Optional[str]) -> Optional[datetime]:
    return datetime.fromisoformat(value) if value else None


def _cycle_of(row: ValidationCycleRow) -> ValidationCycle:
    return ValidationCycle(
        id=row.id,
        tenant_id=row.tenant_id,
        incident_id=row.incident_id,
        app_id=row.app_id,
        cycle=row.cycle,
        started_at=row.started_at,
        requested_by=row.requested_by,
        layers=tuple(_layer_of(payload) for payload in (row.layers or [])),
        window=_window_of(row.window or {}),
        verdict=RecoveryVerdict(row.verdict),
        finished_at=row.finished_at,
        escalated=bool(row.escalated),
        returned_to_active=bool(row.returned_to_active),
        evidence_node_ids=tuple(row.evidence_node_ids or ()),
        note=row.note or "",
        detail=dict(row.detail or {}),
    )


def _layer_of(payload: Mapping[str, Any]) -> LayerResult:
    return LayerResult(
        layer=ValidationLayer(payload.get("layer", "technical")),
        verdict=LayerVerdict(payload.get("verdict", "unknown")),
        summary=payload.get("summary", ""),
        checks=tuple(
            LayerCheck(
                check_id=check.get("check_id", ""),
                label=check.get("label", ""),
                verdict=LayerVerdict(check.get("verdict", "unknown")),
                source=check.get("source", "unknown"),
                observed=check.get("observed", ""),
                expected=check.get("expected", ""),
                result_ref=check.get("result_ref", ""),
                detail=check.get("detail", ""),
            )
            for check in (payload.get("checks") or [])
        ),
    )
