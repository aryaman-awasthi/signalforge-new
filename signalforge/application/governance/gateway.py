"""The Tool Gateway (REQ-061, REQ-062).

**One door.** Every consequential call to an integrated system passes through
this class, and no action-specific code path may reach `ConnectorInvoker`
directly. That is not a style preference: eleven things have to happen on every
execution, in order, and any code path that skipped one of them would be a path
by which production changed without a decision behind it.

    1  validate the arguments against the application's own schema
    2  authenticate the actor            (the route's credential resolver)
    3  authorize the actor               (the route's permission guard)
    4  evaluate policy                   -> ALLOW | APPROVAL_REQUIRED | BLOCK
    5  record the decision in the ledger with its rules_hit
    6  validate the approval, if one is required
    7  claim the idempotency key         (unique index, not a read-then-write)
    8  invoke the adapter                (through the invoker, never around it)
    9  record the outcome                (timeout is UNCERTAIN, never failed)
    10 reconcile before any retry
    11 audit every one of the above

Steps 2 and 3 are the router's guards rather than this class's code, and step 3
is *repeated* here for the approver at execution time - which is the point of
CTL-AUD-03: a permission removed between approving and executing withdraws the
authority the approval carried.

## The three refusals worth naming

**Auto-execution of a high-risk action** never reaches an adapter. `request`
with `intent=AUTO` on a rollback produces a BLOCK, a ledger row in `blocked`, an
audit entry and an event - a recorded refusal rather than an exception somebody's
client could swallow.

**A duplicate submit** does not execute twice. The idempotency key is derived
from the request, the unique index on it is the control, and a second submit
returns the first execution with `duplicate=True` rather than sending anything.

**A timeout is not a failure.** It lands `UNCERTAIN` with `retry_blocked`, and
the only way out is `reconcile`, which asks the target system what actually
happened through `ActionsAdapter.get_execution` - the reconcile-before-retry
primitive the frozen SPI reserved in Sprint 0.

## What an agent may do here

Nothing that this module does not make a person do. There is no code path in
which a model's output becomes an `intent`, an approval, or an execution: the
LLM writes the rationale on the NBA card and nothing else. `requested_by` is
always a `Principal.user_id`.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Optional, Sequence

import jsonschema

from signalforge.application.adapters.invoker import ConnectorInvoker
from signalforge.application.events.bus import EventBus
from signalforge.application.governance.common import (
    APPROVAL_DENIED_TOPIC,
    APPROVAL_GRANTED_TOPIC,
    APPROVAL_REJECTED_TOPIC,
    APPROVAL_REQUESTED_TOPIC,
    APPROVAL_REVOKED_TOPIC,
    EXECUTION_BLOCKED_TOPIC,
    EXECUTION_CANCELLED_TOPIC,
    EXECUTION_COMPENSATING_TOPIC,
    EXECUTION_DECIDED_TOPIC,
    EXECUTION_DUPLICATE_TOPIC,
    EXECUTION_FAILED_TOPIC,
    EXECUTION_RECONCILED_TOPIC,
    EXECUTION_REQUESTED_TOPIC,
    EXECUTION_RETRIED_TOPIC,
    EXECUTION_RETRY_BLOCKED_TOPIC,
    EXECUTION_STARTED_TOPIC,
    EXECUTION_SUCCEEDED_TOPIC,
    EXECUTION_TAKEOVER_TOPIC,
    EXECUTION_UNCERTAIN_TOPIC,
    publish_system,
    record,
)
from signalforge.application.governance.policy_service import PolicyService
from signalforge.application.identity.principal import Principal
from signalforge.application.identity.uow import IdentityUnitOfWork
from signalforge.contracts.canonical import (
    ActionExecution,
    ExecutionState as AdapterState,
    ReconcileOutcome,
)
from signalforge.contracts.envelope import AdapterKind
from signalforge.contracts.errors import (
    AdapterError,
    AdapterTimeoutError,
    AdapterUnavailableError,
)
from signalforge.contracts.roles import ROLE_PERMISSIONS, Permission
from signalforge.domain.adapters.bindings import ConnectorBinding
from signalforge.domain.governance.approvals import Approval, ApprovalState
from signalforge.domain.governance.errors import (
    ApprovalInvalidError,
    ApprovalRequiredError,
    ExecutionBlockedError,
    ExecutionNotFoundError,
    GovernanceValidationError,
    RetryBlockedError,
)
from signalforge.domain.governance.ledger import (
    ErrorCategory,
    ExecutionRecord,
    LedgerState,
    idempotency_key,
    redact_args,
)
from signalforge.domain.governance.policy import (
    Outcome,
    PolicyDecision,
    PolicyEngine,
    RuleHit,
    RuleSource,
    args_hash,
)
from signalforge.domain.identity.ports import Clock
from signalforge.domain.incidents.errors import IncidentNotFoundError
from signalforge.domain.onboarding.profile import ExecutionMode, MappedAction

logger = logging.getLogger("signalforge.governance")

EXECUTE = "execute"
GET_EXECUTION = "get_execution"

MAX_ARGS = 40


@dataclass(frozen=True)
class GatewayResult:
    """What a request produced, in the shape the cockpit and the tests read."""

    execution: ExecutionRecord
    decision: PolicyDecision
    approval: Optional[Approval] = None
    duplicate: bool = False
    """True when the idempotency key already existed. The external mutation was
    not sent again, and `execution` is the row that already had it."""
    adapter_result: Optional[ActionExecution] = None


class ToolGateway:
    def __init__(
        self,
        uow_factory: Callable[[], IdentityUnitOfWork],
        bus: EventBus,
        clock: Clock,
        policies: PolicyService,
        registry,
        invoker: ConnectorInvoker,
        metrics=None,
    ):
        self._uow_factory = uow_factory
        self._bus = bus
        self._clock = clock
        self._policies = policies
        self._registry = registry
        self._invoker = invoker
        self._metrics = metrics

    # -- 1. request ----------------------------------------------------------

    async def request(
        self,
        principal: Principal,
        incident_id: str,
        *,
        action_id: str,
        args: Optional[Mapping[str, Any]] = None,
        intent: Optional[ExecutionMode] = None,
        note: str = "",
        run: bool = True,
    ) -> GatewayResult:
        """Ask for an action. The single entry point.

        `run=True` means "execute it now if policy allows"; an APPROVAL_REQUIRED
        decision never executes regardless, it creates the approval request and
        stops. There is no combination of arguments that runs a high-risk action
        without an approval, and that is checked in `execute` as well as here.
        """
        arguments = dict(args or {})
        now = self._clock.now()

        async with self._uow_factory() as uow:
            incident = await self._require_incident(uow, principal.tenant_id, incident_id)
            action = await self._require_action(
                uow, principal.tenant_id, incident.app_id, action_id
            )
        _validate_args(action, arguments)

        policy = await self._policies.current(principal.tenant_id, incident.app_id)
        decision = PolicyEngine.decide(action, policy, intent=intent)

        digest = args_hash(arguments)
        key = idempotency_key(
            tenant_id=principal.tenant_id,
            incident_id=incident_id,
            action_id=action_id,
            args_digest=digest,
            attempt=1,
        )

        # The duplicate check *before* the decision is recorded, so a resubmit
        # does not mint a second ledger row for the same request.
        async with self._uow_factory() as uow:
            existing = await uow.executions.find_by_key(principal.tenant_id, key)
        if existing is not None:
            return await self._duplicate(principal, incident, existing, decision)

        record_row = ExecutionRecord(
            id=str(uuid.uuid4()),
            tenant_id=principal.tenant_id,
            incident_id=incident_id,
            app_id=incident.app_id,
            action_id=action_id,
            action_class=action.action_class,
            risk_class=decision.risk_class,
            state=LedgerState.PENDING,
            idempotency_key=key,
            attempt=1,
            requested_by=principal.user_id,
            requested_at=now,
            args=redact_args(arguments),
            args_digest=digest,
            decision=decision.outcome,
            decision_reason=decision.reason,
            rules_hit=tuple(hit.as_dict() for hit in decision.rules_hit),
            policy_version=decision.policy_version,
            policy_ref=decision.policy_ref,
            floor_version=decision.floor_version,
            decision_fingerprint=decision.fingerprint(args_hash=digest),
            detail={"note": note[:400]} if note else {},
        )

        async with self._uow_factory() as uow:
            await uow.executions.add(record_row)
            await record(
                uow,
                self._bus,
                self._clock,
                principal=principal,
                app_id=incident.app_id,
                incident_id=incident_id,
                topic=EXECUTION_REQUESTED_TOPIC,
                payload={**record_row.as_payload(), "note": note[:400]},
            )
            await record(
                uow,
                self._bus,
                self._clock,
                principal=principal,
                app_id=incident.app_id,
                incident_id=incident_id,
                topic=EXECUTION_DECIDED_TOPIC,
                payload={
                    **record_row.as_payload(),
                    "decision_reason": decision.reason,
                    "approver_roles": [r.value for r in decision.approver_roles],
                    "approval_ttl_s": decision.approval_ttl_s,
                    "intent": intent.value if intent else "manual",
                },
            )
            await uow.commit()
        self._count_decision(decision)

        if decision.blocked:
            return await self._block(principal, incident, record_row, decision, intent)
        if decision.needs_approval:
            return await self._raise_approval(principal, incident, record_row, decision)
        if not run:
            return GatewayResult(execution=record_row, decision=decision)
        return await self.execute(principal, incident_id, record_row.id, arguments)

    # -- 2. approvals --------------------------------------------------------

    async def approve(
        self,
        principal: Principal,
        incident_id: str,
        execution_id: str,
        *,
        approval_id: Optional[str] = None,
        note: str = "",
    ) -> GatewayResult:
        """A person holding `approve_high_risk` says yes.

        The route already checked the permission; the domain checks separation of
        duties (the requester cannot approve) and that the approver's roles are
        among the ones this action's policy names. Both live in
        `Approval.granted` rather than here, so a second caller cannot reach the
        same grant by another door.
        """
        now = self._clock.now()
        async with self._uow_factory() as uow:
            incident = await self._require_incident(uow, principal.tenant_id, incident_id)
            execution = await self._require_execution(
                uow, principal.tenant_id, execution_id, incident_id
            )
            approval = await self._open_approval(
                uow, principal.tenant_id, execution_id, approval_id
            )
            policy = await self._policies.current(principal.tenant_id, incident.app_id)
            granted = approval.granted(
                at=now,
                ttl_s=policy.ttl_for(execution.action_id),
                approver_user_id=principal.user_id,
                approver_label=principal.display_name or "",
                approver_roles=tuple(binding.role for binding in principal.bindings),
                permission=Permission.APPROVE_HIGH_RISK,
                note=note,
            )
            await uow.approvals.update(granted)
            moved = execution.moved_to(
                LedgerState.APPROVED, at=now, approval_id=granted.id
            )
            await uow.executions.update(moved)
            await record(
                uow,
                self._bus,
                self._clock,
                principal=principal,
                app_id=incident.app_id,
                incident_id=incident_id,
                topic=APPROVAL_GRANTED_TOPIC,
                payload={
                    **granted.as_payload(),
                    "ttl_s": granted.seconds_left(now),
                    "note": note[:400],
                },
            )
            await uow.commit()
        if self._metrics is not None:
            self._metrics.increment(
                "signalforge_approvals_total",
                {"outcome": "granted", "action": execution.action_id},
            )
        return GatewayResult(
            execution=moved,
            decision=self.decision_of(moved),
            approval=granted,
        )

    async def deny(
        self,
        principal: Principal,
        incident_id: str,
        execution_id: str,
        *,
        note: str = "",
    ) -> GatewayResult:
        now = self._clock.now()
        async with self._uow_factory() as uow:
            incident = await self._require_incident(uow, principal.tenant_id, incident_id)
            execution = await self._require_execution(
                uow, principal.tenant_id, execution_id, incident_id
            )
            approval = await self._open_approval(
                uow, principal.tenant_id, execution_id, None
            )
            denied = approval.denied(at=now, by=principal.user_id, note=note)
            await uow.approvals.update(denied)
            blocked = execution.moved_to(
                LedgerState.BLOCKED,
                at=now,
                decision_reason=f"approval denied: {note or 'no reason given'}",
            )
            await uow.executions.update(blocked)
            await record(
                uow,
                self._bus,
                self._clock,
                principal=principal,
                app_id=incident.app_id,
                incident_id=incident_id,
                topic=APPROVAL_DENIED_TOPIC,
                payload={**denied.as_payload(), "note": note[:400]},
            )
            await uow.commit()
        return GatewayResult(
            execution=blocked, decision=self.decision_of(blocked), approval=denied
        )

    async def revoke(
        self,
        principal: Principal,
        incident_id: str,
        execution_id: str,
        *,
        note: str = "",
    ) -> GatewayResult:
        """Withdraw an approval that has not been spent. Somebody changed their
        mind, and saying so has to be as easy as saying yes was."""
        now = self._clock.now()
        async with self._uow_factory() as uow:
            incident = await self._require_incident(uow, principal.tenant_id, incident_id)
            execution = await self._require_execution(
                uow, principal.tenant_id, execution_id, incident_id
            )
            approvals = await uow.approvals.for_execution(
                principal.tenant_id, execution_id
            )
            live = next(
                (a for a in approvals if a.state is ApprovalState.GRANTED), None
            )
            if live is None:
                raise ApprovalInvalidError(
                    f"execution {execution_id} has no granted approval to revoke"
                )
            revoked = live.revoked(at=now, by=principal.user_id, note=note)
            await uow.approvals.update(revoked)
            blocked = execution.moved_to(
                LedgerState.BLOCKED, at=now, decision_reason="approval revoked"
            )
            await uow.executions.update(blocked)
            await record(
                uow,
                self._bus,
                self._clock,
                principal=principal,
                app_id=incident.app_id,
                incident_id=incident_id,
                topic=APPROVAL_REVOKED_TOPIC,
                payload={**revoked.as_payload(), "note": note[:400]},
            )
            await uow.commit()
        return GatewayResult(
            execution=blocked, decision=self.decision_of(blocked), approval=revoked
        )

    # -- 3. execute ----------------------------------------------------------

    async def execute(
        self,
        principal: Principal,
        incident_id: str,
        execution_id: str,
        args: Optional[Mapping[str, Any]] = None,
    ) -> GatewayResult:
        """Send it. Everything is re-checked here, and nothing is trusted from
        the request that created the row.

        The re-checks are the whole reason this is a separate method: minutes
        pass between requesting a rollback and somebody approving it, and in
        those minutes a policy can change, a role can be removed, an approval can
        expire and the incident can move on.
        """
        now = self._clock.now()
        arguments = dict(args or {})

        async with self._uow_factory() as uow:
            incident = await self._require_incident(uow, principal.tenant_id, incident_id)
            execution = await self._require_execution(
                uow, principal.tenant_id, execution_id, incident_id
            )
            action = await self._require_action(
                uow, principal.tenant_id, incident.app_id, execution.action_id
            )

        if execution.state.applied:
            # Already done. Returning the row rather than raising, because a
            # client that retried a network hiccup should see the outcome.
            return GatewayResult(
                execution=execution,
                decision=self.decision_of(execution),
                duplicate=True,
            )
        if execution.state not in (LedgerState.PENDING, LedgerState.APPROVED):
            raise ExecutionBlockedError(
                f"execution {execution_id} is {execution.state.value} and cannot "
                "be sent; only a pending or approved execution runs"
            )

        # Re-evaluate policy against the version in force *now*.
        policy = await self._policies.current(principal.tenant_id, incident.app_id)
        decision = PolicyEngine.decide(action, policy)
        if decision.blocked:
            # The policy moved under an execution that was already authorised.
            # Recorded before it is refused, because "this was allowed at 03:02
            # and blocked at 03:14" is the interesting half.
            await self._store_block(
                principal, incident, execution, decision, reason=decision.reason
            )
            raise ExecutionBlockedError(decision.reason)

        approval: Optional[Approval] = None
        if decision.needs_approval:
            approval = await self._validated_approval(
                principal, incident, execution, decision, now=now
            )

        digest = execution.args_digest
        if arguments and args_hash(arguments) != digest:
            raise GovernanceValidationError(
                "the arguments do not match the ones this execution was "
                "authorised for; request a new execution instead"
            )

        binding = await self._actions_binding(principal.tenant_id, incident.app_id)
        if binding is None:
            raise ExecutionBlockedError(
                f"{incident.app_id} has no enabled actions connector on a "
                "remediation credential; nothing can be executed until one is "
                "bound"
            )

        running = execution.started(at=now)
        async with self._uow_factory() as uow:
            await uow.executions.update(running)
            await record(
                uow,
                self._bus,
                self._clock,
                principal=principal,
                app_id=incident.app_id,
                incident_id=incident_id,
                topic=EXECUTION_STARTED_TOPIC,
                payload={**running.as_payload(), "binding_id": binding.id},
            )
            if approval is not None:
                await uow.approvals.update(approval.used(at=now))
            await uow.commit()

        result, finished = await self._invoke(
            principal, incident, running, binding, arguments
        )
        return GatewayResult(
            execution=finished,
            decision=decision,
            approval=approval,
            adapter_result=result,
        )

    # -- 4. reconcile, retry, compensate, cancel, take over -------------------

    async def reconcile(
        self, principal: Principal, incident_id: str, execution_id: str
    ) -> GatewayResult:
        """Ask the target system what actually happened.

        The only door out of UNCERTAIN. It calls `get_execution` with the same
        idempotency key the mutation was sent under, which is the whole reason
        that key is derived rather than generated.
        """
        now = self._clock.now()
        async with self._uow_factory() as uow:
            incident = await self._require_incident(uow, principal.tenant_id, incident_id)
            execution = await self._require_execution(
                uow, principal.tenant_id, execution_id, incident_id
            )
        if execution.state not in (LedgerState.UNCERTAIN, LedgerState.PARTIAL):
            raise ExecutionBlockedError(
                f"execution {execution_id} is {execution.state.value}; only an "
                "uncertain or partial outcome needs reconciling"
            )
        binding = await self._actions_binding(principal.tenant_id, incident.app_id)
        if binding is None:
            raise ExecutionBlockedError(
                "the actions connector this execution used is no longer bound, "
                "so its outcome cannot be established automatically. Use human "
                "takeover and record what you find."
            )

        reconciling = execution.moved_to(LedgerState.RECONCILING, at=now)
        async with self._uow_factory() as uow:
            await uow.executions.update(reconciling)
            await uow.commit()

        try:
            found: Optional[ActionExecution] = await self._invoker.invoke(
                principal.tenant_id,
                binding.id,
                GET_EXECUTION,
                execution.idempotency_key,
            )
        except AdapterError as exc:
            # Still unknown. Back to UNCERTAIN rather than guessing - a failed
            # reconciliation is not evidence that nothing was applied.
            back = reconciling.moved_to(
                LedgerState.UNCERTAIN,
                at=now,
                error=f"reconciliation failed: {exc}",
                error_category=ErrorCategory.UNAVAILABLE,
                retry_blocked=True,
            )
            async with self._uow_factory() as uow:
                await uow.executions.update(back)
                await publish_system(
                    uow,
                    self._bus,
                    self._clock,
                    tenant_id=principal.tenant_id,
                    app_id=incident.app_id,
                    incident_id=incident_id,
                    topic=EXECUTION_UNCERTAIN_TOPIC,
                    payload={
                        **back.as_payload(),
                        "reconciliation_attempted": True,
                        "error": str(exc)[:300],
                    },
                )
                await uow.commit()
            return GatewayResult(execution=back, decision=self.decision_of(back))

        outcome = _reconcile_outcome(found)
        resolved = reconciling.reconciled(
            outcome,
            at=now,
            detail={
                "adapter_state": found.state.value if found else "absent",
                "adapter_execution_id": found.execution_id if found else "",
                "queried_key": execution.idempotency_key,
            },
        )
        if found is not None:
            resolved = _with_adapter_id(resolved, found.execution_id)
        async with self._uow_factory() as uow:
            await uow.executions.update(resolved)
            await record(
                uow,
                self._bus,
                self._clock,
                principal=principal,
                app_id=incident.app_id,
                incident_id=incident_id,
                topic=EXECUTION_RECONCILED_TOPIC,
                payload={
                    **resolved.as_payload(),
                    "outcome": outcome.value,
                    "source": binding.adapter_id,
                    "retry_allowed": not resolved.retry_blocked,
                    "queried_key": execution.idempotency_key,
                },
            )
            await uow.commit()
        if self._metrics is not None:
            self._metrics.increment(
                "signalforge_execution_reconciliations_total",
                {"outcome": outcome.value},
            )
        return GatewayResult(execution=resolved, decision=self.decision_of(resolved))

    async def retry(
        self, principal: Principal, incident_id: str, execution_id: str
    ) -> GatewayResult:
        """A fresh attempt at something that provably did not happen.

        `assert_retryable` is inside `retry_of`, so a caller that skipped the
        check cannot get past it. The refusal is audited before it is raised:
        somebody trying to retry an unreconciled rollback is exactly the moment
        this control exists for.
        """
        now = self._clock.now()
        async with self._uow_factory() as uow:
            incident = await self._require_incident(uow, principal.tenant_id, incident_id)
            execution = await self._require_execution(
                uow, principal.tenant_id, execution_id, incident_id
            )
        try:
            execution.assert_retryable()
        except RetryBlockedError as exc:
            async with self._uow_factory() as uow:
                await record(
                    uow,
                    self._bus,
                    self._clock,
                    principal=principal,
                    app_id=incident.app_id,
                    incident_id=incident_id,
                    topic=EXECUTION_RETRY_BLOCKED_TOPIC,
                    payload={
                        **execution.as_payload(),
                        "refused_reason": str(exc),
                        "next_step": "reconcile",
                    },
                )
                await uow.commit()
            if self._metrics is not None:
                self._metrics.increment(
                    "signalforge_execution_retries_blocked_total",
                    {"state": execution.state.value},
                )
            raise

        key = idempotency_key(
            tenant_id=principal.tenant_id,
            incident_id=incident_id,
            action_id=execution.action_id,
            args_digest=execution.args_digest,
            attempt=execution.attempt + 1,
        )
        policy = await self._policies.current(principal.tenant_id, incident.app_id)
        async with self._uow_factory() as uow:
            action = await self._require_action(
                uow, principal.tenant_id, incident.app_id, execution.action_id
            )
        decision = PolicyEngine.decide(action, policy)
        if decision.blocked:
            raise ExecutionBlockedError(decision.reason)
        if execution.attempt >= policy.max_action_retries + 1:
            raise ExecutionBlockedError(
                f"{execution.action_id} has already been attempted "
                f"{execution.attempt} time(s) and this application's policy "
                f"allows {policy.max_action_retries} retry(ies). A person takes "
                "over from here."
            )

        fresh = execution.retry_of(
            execution_id=str(uuid.uuid4()),
            at=now,
            requested_by=principal.user_id,
            key=key,
        )
        fresh = _with_decision(fresh, decision, digest=execution.args_digest)
        async with self._uow_factory() as uow:
            await uow.executions.add(fresh)
            await record(
                uow,
                self._bus,
                self._clock,
                principal=principal,
                app_id=incident.app_id,
                incident_id=incident_id,
                topic=EXECUTION_RETRIED_TOPIC,
                payload={
                    **fresh.as_payload(),
                    "supersedes": execution.id,
                    "previous_outcome": (
                        execution.reconcile_outcome.value
                        if execution.reconcile_outcome
                        else execution.state.value
                    ),
                },
            )
            await uow.commit()
        if decision.needs_approval:
            return await self._raise_approval(principal, incident, fresh, decision)
        return await self.execute(principal, incident_id, fresh.id)

    async def compensate(
        self,
        principal: Principal,
        incident_id: str,
        execution_id: str,
        *,
        note: str = "",
    ) -> GatewayResult:
        """Undo what landed, through the action's declared reverse action.

        A compensation is a full execution in its own right - it goes through the
        same policy decision and the same approval gate, because undoing a
        failover is as consequential as doing one. It is linked to what it
        compensates rather than replacing it: both rows stay.
        """
        now = self._clock.now()
        async with self._uow_factory() as uow:
            incident = await self._require_incident(uow, principal.tenant_id, incident_id)
            execution = await self._require_execution(
                uow, principal.tenant_id, execution_id, incident_id
            )
            action = await self._require_action(
                uow, principal.tenant_id, incident.app_id, execution.action_id
            )
        if not action.reversible or not action.reverse_action_id:
            raise ExecutionBlockedError(
                f"{execution.action_id} declares no reverse action, so the "
                "platform cannot compensate it. A person takes over."
            )
        if execution.state not in (
            LedgerState.PARTIAL,
            LedgerState.SUCCEEDED,
            LedgerState.COMPLETED_LATE,
        ):
            raise ExecutionBlockedError(
                f"there is nothing to compensate: execution {execution_id} is "
                f"{execution.state.value}"
            )

        compensating = execution.moved_to(
            LedgerState.COMPENSATING, at=now, takeover_note=note[:400]
        )
        async with self._uow_factory() as uow:
            await uow.executions.update(compensating)
            await record(
                uow,
                self._bus,
                self._clock,
                principal=principal,
                app_id=incident.app_id,
                incident_id=incident_id,
                topic=EXECUTION_COMPENSATING_TOPIC,
                payload={
                    **compensating.as_payload(),
                    "reverse_action_id": action.reverse_action_id,
                    "note": note[:400],
                },
            )
            await uow.commit()

        result = await self.request(
            principal,
            incident_id,
            action_id=action.reverse_action_id,
            args={},
            note=f"compensating {execution.id}",
        )
        linked = _with_compensation(result.execution, compensates=execution.id)
        async with self._uow_factory() as uow:
            await uow.executions.update(linked)
            source = await uow.executions.get(principal.tenant_id, execution.id)
            if source is not None:
                await uow.executions.update(
                    _with_compensated_by(source, linked.id)
                )
            await uow.commit()
        return GatewayResult(
            execution=linked,
            decision=result.decision,
            approval=result.approval,
        )

    async def cancel(
        self,
        principal: Principal,
        incident_id: str,
        execution_id: str,
        *,
        note: str = "",
    ) -> GatewayResult:
        now = self._clock.now()
        async with self._uow_factory() as uow:
            incident = await self._require_incident(uow, principal.tenant_id, incident_id)
            execution = await self._require_execution(
                uow, principal.tenant_id, execution_id, incident_id
            )
            cancelled = execution.moved_to(
                LedgerState.CANCELLED, at=now, takeover_note=note[:400]
            )
            await uow.executions.update(cancelled)
            await record(
                uow,
                self._bus,
                self._clock,
                principal=principal,
                app_id=incident.app_id,
                incident_id=incident_id,
                topic=EXECUTION_CANCELLED_TOPIC,
                payload={**cancelled.as_payload(), "note": note[:400]},
            )
            await uow.commit()
        return GatewayResult(
            execution=cancelled, decision=self.decision_of(cancelled)
        )

    async def take_over(
        self,
        principal: Principal,
        incident_id: str,
        execution_id: str,
        *,
        note: str,
    ) -> GatewayResult:
        """A person is doing the rest by hand.

        The honest terminal state for an execution the platform cannot resolve -
        an adapter that is gone, a partial change somebody is fixing at a console.
        It requires a note, because "a human took over" with no record of what
        they did is worse than no state at all.
        """
        if not (note or "").strip():
            raise GovernanceValidationError(
                "human takeover records what the person is doing; a note is "
                "required"
            )
        now = self._clock.now()
        async with self._uow_factory() as uow:
            incident = await self._require_incident(uow, principal.tenant_id, incident_id)
            execution = await self._require_execution(
                uow, principal.tenant_id, execution_id, incident_id
            )
            taken = execution.moved_to(
                LedgerState.TAKEN_OVER,
                at=now,
                takeover_by=principal.user_id,
                takeover_note=note[:400],
                retry_blocked=True,
            )
            await uow.executions.update(taken)
            await record(
                uow,
                self._bus,
                self._clock,
                principal=principal,
                app_id=incident.app_id,
                incident_id=incident_id,
                topic=EXECUTION_TAKEOVER_TOPIC,
                payload={**taken.as_payload(), "note": note[:400]},
            )
            await uow.commit()
        return GatewayResult(execution=taken, decision=self.decision_of(taken))

    # -- reads ---------------------------------------------------------------

    async def ledger(
        self, principal: Principal, incident_id: str, *, limit: int = 200
    ) -> tuple[Sequence[ExecutionRecord], Sequence[Approval]]:
        async with self._uow_factory() as uow:
            await self._require_incident(uow, principal.tenant_id, incident_id)
            executions = list(
                await uow.executions.list(
                    principal.tenant_id, incident_id, limit=limit
                )
            )
            approvals = list(
                await uow.approvals.list(principal.tenant_id, incident_id, limit=limit)
            )
        return executions, approvals

    async def get(
        self, principal: Principal, incident_id: str, execution_id: str
    ) -> tuple[ExecutionRecord, Sequence[Approval]]:
        async with self._uow_factory() as uow:
            await self._require_incident(uow, principal.tenant_id, incident_id)
            execution = await self._require_execution(
                uow, principal.tenant_id, execution_id, incident_id
            )
            approvals = list(
                await uow.approvals.for_execution(principal.tenant_id, execution_id)
            )
        return execution, approvals

    # -- background sweeps ---------------------------------------------------

    async def expire_approvals(self) -> int:
        """Housekeeping across tenants. Enforcement is `assert_valid_now`; this
        only makes the board honest about what has already lapsed."""
        now = self._clock.now()
        async with self._uow_factory() as uow:
            expired = await uow.approvals.expire_due(now=now, limit=200)
            for approval in expired:
                await publish_system(
                    uow,
                    self._bus,
                    self._clock,
                    tenant_id=approval.tenant_id,
                    app_id=approval.app_id,
                    incident_id=approval.incident_id,
                    topic="execution.approval.expired",
                    payload=approval.as_payload(),
                )
            await uow.commit()
        return len(expired)

    # -- internals -----------------------------------------------------------

    async def _invoke(
        self,
        principal: Principal,
        incident,
        running: ExecutionRecord,
        binding: ConnectorBinding,
        arguments: Mapping[str, Any],
    ) -> tuple[Optional[ActionExecution], ExecutionRecord]:
        """The one place an adapter is called. Everything above is decision."""
        now = self._clock.now()
        try:
            result: ActionExecution = await self._invoker.invoke(
                principal.tenant_id,
                binding.id,
                EXECUTE,
                running.action_id,
                dict(arguments),
                running.idempotency_key,
            )
        except AdapterTimeoutError as exc:
            return None, await self._store_uncertain(
                principal, incident, running, error=str(exc)
            )
        except AdapterUnavailableError as exc:
            # Unavailable is *also* uncertain for a mutation: a connection reset
            # after the request left this process says nothing about whether the
            # target applied it.
            return None, await self._store_uncertain(
                principal, incident, running, error=str(exc)
            )
        except AdapterError as exc:
            return None, await self._store_failure(
                principal,
                incident,
                running,
                error=str(exc),
                category=ErrorCategory.REJECTED,
            )

        if result.state is AdapterState.UNCERTAIN:
            return result, await self._store_uncertain(
                principal,
                incident,
                running,
                error=result.error or "the target did not confirm the outcome",
                adapter_execution_id=result.execution_id,
            )
        if result.state is AdapterState.FAILED:
            return result, await self._store_failure(
                principal,
                incident,
                running,
                error=result.error or "the target rejected the action",
                category=ErrorCategory.REJECTED,
                adapter_execution_id=result.execution_id,
            )
        if result.state is AdapterState.SUCCEEDED:
            done = running.moved_to(
                LedgerState.SUCCEEDED,
                at=now,
                adapter_execution_id=result.execution_id,
                result_ref=f"action-execution:{result.execution_id}",
            )
            async with self._uow_factory() as uow:
                await uow.executions.update(done)
                await record(
                    uow,
                    self._bus,
                    self._clock,
                    principal=principal,
                    app_id=incident.app_id,
                    incident_id=incident.id,
                    topic=EXECUTION_SUCCEEDED_TOPIC,
                    payload={
                        **done.as_payload(),
                        "adapter_execution_id": result.execution_id,
                    },
                )
                await uow.commit()
            self._count_outcome("succeeded", done)
            return result, done
        # RUNNING or PENDING at the target: not finished, and not knowable here.
        return result, await self._store_uncertain(
            principal,
            incident,
            running,
            error=f"the target reports {result.state.value}; the outcome is not "
            "yet established",
            adapter_execution_id=result.execution_id,
        )

    async def _store_uncertain(
        self,
        principal: Principal,
        incident,
        running: ExecutionRecord,
        *,
        error: str,
        adapter_execution_id: str = "",
    ) -> ExecutionRecord:
        now = self._clock.now()
        uncertain = running.uncertain(at=now, error=error)
        if adapter_execution_id:
            uncertain = _with_adapter_id(uncertain, adapter_execution_id)
        async with self._uow_factory() as uow:
            await uow.executions.update(uncertain)
            await publish_system(
                uow,
                self._bus,
                self._clock,
                tenant_id=principal.tenant_id,
                app_id=incident.app_id,
                incident_id=incident.id,
                topic=EXECUTION_UNCERTAIN_TOPIC,
                payload={
                    **uncertain.as_payload(),
                    "error": error[:300],
                    "retry_blocked": True,
                    "next_step": "reconcile",
                    "why": "the target may have applied this after SignalForge "
                    "stopped waiting; retrying now could apply it twice",
                },
            )
            await uow.commit()
        self._count_outcome("uncertain", uncertain)
        logger.warning(
            "execution outcome is uncertain",
            extra={
                "execution_id": uncertain.id,
                "action_id": uncertain.action_id,
                "incident_id": incident.id,
            },
        )
        return uncertain

    async def _store_failure(
        self,
        principal: Principal,
        incident,
        running: ExecutionRecord,
        *,
        error: str,
        category: ErrorCategory,
        adapter_execution_id: str = "",
    ) -> ExecutionRecord:
        now = self._clock.now()
        failed = running.moved_to(
            LedgerState.FAILED, at=now, error=error, error_category=category
        )
        if adapter_execution_id:
            failed = _with_adapter_id(failed, adapter_execution_id)
        async with self._uow_factory() as uow:
            await uow.executions.update(failed)
            await publish_system(
                uow,
                self._bus,
                self._clock,
                tenant_id=principal.tenant_id,
                app_id=incident.app_id,
                incident_id=incident.id,
                topic=EXECUTION_FAILED_TOPIC,
                payload={**failed.as_payload(), "error": error[:300]},
            )
            await uow.commit()
        self._count_outcome("failed", failed)
        return failed

    async def _store_block(
        self,
        principal: Principal,
        incident,
        execution: ExecutionRecord,
        decision: PolicyDecision,
        *,
        reason: str,
    ) -> ExecutionRecord:
        now = self._clock.now()
        blocked = execution.moved_to(
            LedgerState.BLOCKED,
            at=now,
            decision=Outcome.BLOCK,
            decision_reason=reason,
            rules_hit=tuple(hit.as_dict() for hit in decision.rules_hit),
        )
        async with self._uow_factory() as uow:
            await uow.executions.update(blocked)
            await record(
                uow,
                self._bus,
                self._clock,
                principal=principal,
                app_id=incident.app_id,
                incident_id=incident.id,
                topic=EXECUTION_BLOCKED_TOPIC,
                payload={
                    **blocked.as_payload(),
                    "reason": reason,
                    "floor_rules": list(decision.floor_rules),
                },
            )
            await uow.commit()
        return blocked

    async def _block(
        self,
        principal: Principal,
        incident,
        execution: ExecutionRecord,
        decision: PolicyDecision,
        intent: Optional[ExecutionMode],
    ) -> GatewayResult:
        blocked = await self._store_block(
            principal, incident, execution, decision, reason=decision.reason
        )
        if self._metrics is not None:
            self._metrics.increment(
                "signalforge_execution_blocked_total",
                {
                    "risk_class": decision.risk_class.value,
                    "floor": "true" if decision.floor_rules else "false",
                    "intent": intent.value if intent else "manual",
                },
            )
        return GatewayResult(execution=blocked, decision=decision)

    async def _raise_approval(
        self,
        principal: Principal,
        incident,
        execution: ExecutionRecord,
        decision: PolicyDecision,
    ) -> GatewayResult:
        now = self._clock.now()
        approval = Approval(
            id=str(uuid.uuid4()),
            tenant_id=principal.tenant_id,
            incident_id=incident.id,
            app_id=incident.app_id,
            execution_id=execution.id,
            action_id=execution.action_id,
            state=ApprovalState.REQUESTED,
            requested_by=principal.user_id,
            requested_at=now,
            decision_fingerprint=execution.decision_fingerprint,
            policy_version=decision.policy_version,
            policy_ref=decision.policy_ref,
            required_roles=decision.approver_roles,
        )
        waiting = execution.moved_to(
            LedgerState.APPROVAL_REQUIRED, at=now, approval_id=approval.id
        )
        async with self._uow_factory() as uow:
            await uow.approvals.add(approval)
            await uow.executions.update(waiting)
            await record(
                uow,
                self._bus,
                self._clock,
                principal=principal,
                app_id=incident.app_id,
                incident_id=incident.id,
                topic=APPROVAL_REQUESTED_TOPIC,
                payload={
                    **approval.as_payload(),
                    "risk_class": execution.risk_class.value,
                    "approval_ttl_s": decision.approval_ttl_s,
                    "reason": decision.reason,
                    "rules_hit": [hit.as_dict() for hit in decision.rules_hit],
                },
            )
            await uow.commit()
        return GatewayResult(
            execution=waiting, decision=decision, approval=approval
        )

    async def _validated_approval(
        self,
        principal: Principal,
        incident,
        execution: ExecutionRecord,
        decision: PolicyDecision,
        *,
        now,
    ) -> Approval:
        """Every check that stands between an approval and a production change.

        A refusal is audited as `execution.approval.rejected` before it is
        raised. An expired approval being presented, or one whose approver has
        since lost the permission, is a security-relevant event whether or not
        the caller was acting in good faith.
        """
        async with self._uow_factory() as uow:
            approvals = await uow.approvals.for_execution(
                principal.tenant_id, execution.id
            )
            granted = next(
                (a for a in approvals if a.state is ApprovalState.GRANTED), None
            )
            if granted is None:
                raise ApprovalRequiredError(
                    f"{execution.action_id} is {decision.risk_class.value} and has "
                    "no granted approval; a person holding "
                    f"{', '.join(r.value for r in decision.approver_roles)} must "
                    "approve it first"
                )
            approver_permissions = await self._permissions_of(
                uow, principal.tenant_id, granted.approver_user_id, incident.app_id
            )
        try:
            granted.assert_valid_now(
                now=now,
                fingerprint=execution.decision_fingerprint,
                approver_permissions=approver_permissions,
                policy_version=decision.policy_version,
            )
        except ApprovalInvalidError as exc:
            async with self._uow_factory() as uow:
                await publish_system(
                    uow,
                    self._bus,
                    self._clock,
                    tenant_id=principal.tenant_id,
                    app_id=incident.app_id,
                    incident_id=incident.id,
                    topic=APPROVAL_REJECTED_TOPIC,
                    payload={
                        **granted.as_payload(),
                        "execution_id": execution.id,
                        "refused_reason": str(exc),
                        "current_policy_version": decision.policy_version,
                        "approver_still_permitted": (
                            Permission.APPROVE_HIGH_RISK in set(approver_permissions)
                        ),
                    },
                )
                await uow.commit()
            if self._metrics is not None:
                self._metrics.increment(
                    "signalforge_approvals_total", {"outcome": "rejected"}
                )
            raise
        return granted

    async def _duplicate(
        self,
        principal: Principal,
        incident,
        existing: ExecutionRecord,
        decision: PolicyDecision,
    ) -> GatewayResult:
        """The same request, again. Nothing is sent.

        An unresolved earlier attempt is *also* refused rather than returned as
        a benign duplicate: submitting the same rollback while the first one's
        outcome is unknown is precisely the situation reconcile-before-retry
        exists for.
        """
        async with self._uow_factory() as uow:
            await publish_system(
                uow,
                self._bus,
                self._clock,
                tenant_id=principal.tenant_id,
                app_id=incident.app_id,
                incident_id=incident.id,
                topic=EXECUTION_DUPLICATE_TOPIC,
                payload={
                    **existing.as_payload(),
                    "resubmitted_by": principal.user_id,
                    "sent_again": False,
                },
                actor_id=principal.user_id,
            )
            await uow.commit()
        if self._metrics is not None:
            self._metrics.increment(
                "signalforge_execution_duplicates_total",
                {"state": existing.state.value},
            )
        if existing.state.unresolved:
            raise RetryBlockedError(
                f"this exact request is already recorded as execution "
                f"{existing.id}, and its outcome at the target is "
                f"{existing.state.value}. It was not sent again. Reconcile that "
                "execution before submitting it once more."
            )
        return GatewayResult(
            execution=existing, decision=decision, duplicate=True
        )

    async def _actions_binding(
        self, tenant_id: str, app_id: str
    ) -> Optional[ConnectorBinding]:
        """The one enabled actions connector for this application.

        The credential-scope floor is *not* re-implemented here: `invoker.invoke`
        calls `assert_can_invoke` on every call, so a read-only credential is
        refused at the moment of use rather than at the moment of selection.
        """
        async with self._uow_factory() as uow:
            bindings = await uow.connectors.list(
                tenant_id, app_id=app_id, enabled_only=True
            )
        for binding in bindings:
            manifest = self._registry.find(binding.adapter_id)
            if (
                manifest is not None
                and manifest.kind is AdapterKind.ACTIONS
                and manifest.supports(EXECUTE)
            ):
                return binding
        return None

    @staticmethod
    async def _permissions_of(
        uow: IdentityUnitOfWork, tenant_id: str, user_id: str, app_id: str
    ) -> tuple[Permission, ...]:
        """What the approver holds *now*, on this application.

        Read from the repository rather than from a token or from the approval
        row, because the whole question is whether anything changed since the
        approval was recorded.
        """
        if not user_id:
            return ()
        bindings = await uow.bindings.list_for_user(tenant_id, user_id)
        applicable = [
            binding
            for binding in bindings
            if binding.app_id is None or binding.app_id == app_id
        ]
        held: set[Permission] = set()
        for binding in applicable:
            held.update(ROLE_PERMISSIONS[binding.role])
        return tuple(sorted(held, key=lambda p: p.value))

    @staticmethod
    async def _require_incident(uow: IdentityUnitOfWork, tenant_id: str, incident_id: str):
        incident = await uow.incidents.get(tenant_id, incident_id)
        if incident is None:
            raise IncidentNotFoundError(f"incident {incident_id} not found")
        return incident

    @staticmethod
    async def _require_action(
        uow: IdentityUnitOfWork, tenant_id: str, app_id: str, action_id: str
    ) -> MappedAction:
        profile = await uow.onboarding.get(tenant_id, app_id)
        action = profile.action(action_id) if profile is not None else None
        if action is None:
            raise ExecutionNotFoundError(
                f"{app_id} publishes no action {action_id!r}. Only actions from "
                "the application's own validated manifest can be executed."
            )
        return action

    @staticmethod
    async def _require_execution(
        uow: IdentityUnitOfWork,
        tenant_id: str,
        execution_id: str,
        incident_id: str,
    ) -> ExecutionRecord:
        execution = await uow.executions.get(tenant_id, execution_id)
        if execution is None or execution.incident_id != incident_id:
            # Identical whether it belongs to another tenant, another incident,
            # or to nobody (REQ-013).
            raise ExecutionNotFoundError(f"execution {execution_id} not found")
        return execution

    @staticmethod
    async def _open_approval(
        uow: IdentityUnitOfWork,
        tenant_id: str,
        execution_id: str,
        approval_id: Optional[str],
    ) -> Approval:
        approvals = await uow.approvals.for_execution(tenant_id, execution_id)
        if approval_id:
            found = next((a for a in approvals if a.id == approval_id), None)
            if found is None:
                raise ApprovalInvalidError(f"approval {approval_id} not found")
            return found
        pending = next(
            (a for a in approvals if a.state is ApprovalState.REQUESTED), None
        )
        if pending is None:
            raise ApprovalInvalidError(
                f"execution {execution_id} has no approval waiting for a decision"
            )
        return pending

    def decision_of(self, execution: ExecutionRecord) -> PolicyDecision:
        """The decision as recorded on the row.

        Reconstructed from stored fields rather than recomputed, so a read of a
        historical execution shows the rules that actually applied to it.
        """
        return PolicyDecision(
            outcome=execution.decision,
            action_id=execution.action_id,
            action_class=execution.action_class,
            risk_class=execution.risk_class,
            reason=execution.decision_reason,
            rules_hit=tuple(
                RuleHit(
                    rule_id=hit.get("rule_id", ""),
                    source=RuleSource(hit.get("source", "floor")),
                    effect=Outcome(hit.get("effect", "block")),
                    detail=hit.get("detail", ""),
                )
                for hit in execution.rules_hit
            ),
            policy_version=execution.policy_version,
            policy_ref=execution.policy_ref,
            floor_version=execution.floor_version or "",
        )

    def _count_decision(self, decision: PolicyDecision) -> None:
        if self._metrics is None:
            return
        self._metrics.increment(
            "signalforge_policy_decisions_total",
            {
                "outcome": decision.outcome.value,
                "risk_class": decision.risk_class.value,
            },
        )

    def _count_outcome(self, outcome: str, execution: ExecutionRecord) -> None:
        if self._metrics is None:
            return
        self._metrics.increment(
            "signalforge_executions_total",
            {"outcome": outcome, "risk_class": execution.risk_class.value},
        )


# --- pure helpers --------------------------------------------------------------


def _validate_args(action: MappedAction, args: Mapping[str, Any]) -> None:
    """Step 1 of the pipeline, against the application's own schema.

    An action that publishes no schema accepts no arguments. That is deliberate:
    an unvalidated argument reaching a rollback is the sort of thing that ends up
    in a post-incident review, and "the application did not tell us what this
    takes" is a reason to refuse rather than a reason to guess.
    """
    if len(args) > MAX_ARGS:
        raise GovernanceValidationError(
            f"an action call may carry at most {MAX_ARGS} arguments"
        )
    schema = dict(action.params_schema or {})
    if not schema:
        if args:
            raise GovernanceValidationError(
                f"{action.action_id} publishes no parameter schema, so it takes "
                f"no arguments; got {sorted(args)}"
            )
        return
    try:
        jsonschema.Draft202012Validator(schema).validate(dict(args))
    except jsonschema.ValidationError as exc:
        path = "/".join(str(part) for part in exc.absolute_path) or "<root>"
        raise GovernanceValidationError(
            f"{action.action_id} arguments are invalid at {path}: {exc.message}"
        ) from exc
    except jsonschema.SchemaError as exc:
        # The application published a broken schema. Refusing is the safe read:
        # the alternative is executing a remediation with unchecked arguments.
        raise GovernanceValidationError(
            f"{action.action_id} publishes an invalid parameter schema, so its "
            f"arguments cannot be checked: {exc.message}"
        ) from exc


def _reconcile_outcome(found: Optional[ActionExecution]) -> ReconcileOutcome:
    """The verdict, from what the target system says about the key.

    `None` - the key is unknown to the target - is the only verdict that permits
    a retry, and it is the only one that means the operation demonstrably never
    happened.
    """
    if found is None:
        return ReconcileOutcome.NOT_APPLIED
    if found.reconcile_outcome is not None:
        return found.reconcile_outcome
    if found.state is AdapterState.SUCCEEDED:
        return ReconcileOutcome.COMPLETED_LATE
    if found.state in (AdapterState.RUNNING, AdapterState.PENDING):
        # Still going. Partial rather than not-applied: something is happening at
        # the target, and a retry on top of it would be a second concurrent
        # change.
        return ReconcileOutcome.PARTIAL
    if found.state is AdapterState.UNCERTAIN:
        return ReconcileOutcome.PARTIAL
    if bool((found.output or {}).get("partial")):
        return ReconcileOutcome.PARTIAL
    return ReconcileOutcome.NOT_APPLIED


def _with_adapter_id(record_row: ExecutionRecord, adapter_execution_id: str):
    from dataclasses import replace

    return replace(record_row, adapter_execution_id=adapter_execution_id)


def _with_compensation(record_row: ExecutionRecord, *, compensates: str):
    from dataclasses import replace

    return replace(record_row, compensates_execution_id=compensates)


def _with_compensated_by(record_row: ExecutionRecord, compensation_id: str):
    from dataclasses import replace

    return replace(record_row, compensated_by_execution_id=compensation_id)


def _with_decision(
    record_row: ExecutionRecord, decision: PolicyDecision, *, digest: str
):
    from dataclasses import replace

    return replace(
        record_row,
        decision=decision.outcome,
        decision_reason=decision.reason,
        rules_hit=tuple(hit.as_dict() for hit in decision.rules_hit),
        policy_version=decision.policy_version,
        policy_ref=decision.policy_ref,
        floor_version=decision.floor_version,
        risk_class=decision.risk_class,
        decision_fingerprint=decision.fingerprint(args_hash=digest),
    )
