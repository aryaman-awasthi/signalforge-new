"""The execution ledger (REQ-061, REQ-062).

One row per attempt to change somebody's production estate, and the row is the
product. Everything else in Sprint 6 exists to put an honest one here.

## The state that matters

`UNCERTAIN` is not an error state. It is the honest answer to "we sent the
request and stopped waiting", and the entire retry design hangs off refusing to
round it to either neighbour:

- rounding it **down** to FAILED invites a retry, and a retried rollback that
  actually landed the first time is two rollbacks;
- rounding it **up** to SUCCEEDED declares a mitigation that may never have been
  applied, and the war room stops looking.

So `UNCERTAIN` sets `retry_blocked` and the only move out of it is
`RECONCILING` - going and *asking* the target system, through the adapter, what
actually happened. That is the reconcile-before-retry primitive the frozen SPI
reserved in Sprint 0 (`ActionsAdapter.get_execution`).

## Three verdicts, three different next moves

| Verdict | What it means | What may follow |
|---|---|---|
| `COMPLETED_LATE` | it did land, after we stopped waiting | nothing - the change is applied |
| `NOT_APPLIED` | it never landed | a retry is safe, as a new attempt |
| `PARTIAL` | some of it landed | compensate, continue, or a person takes over |

## Idempotency

The key is derived, not generated: the same tenant, incident, action, arguments
and attempt number always produce the same key. That is what makes a duplicate
submit - a double-clicked button, a retried HTTP request, a redelivered event -
find the existing row instead of sending a second mutation. A *deliberate* retry
after `NOT_APPLIED` increments the attempt number and therefore gets a new key,
which is correct: it is provably a new attempt at something that provably did
not happen.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import Enum
from typing import Any, Mapping, Optional

from signalforge.contracts.canonical import ActionClass, ReconcileOutcome
from signalforge.domain.governance.errors import (
    GovernanceValidationError,
    RetryBlockedError,
)
from signalforge.domain.governance.policy import Outcome, RiskClass

MAX_ERROR_CHARS = 400
MAX_ARG_CHARS = 500


class LedgerState(str, Enum):
    """Every state an execution can be in, and no more.

    A superset of the adapter's `ExecutionState`: the adapter reports what
    happened at the target, and the ledger records what happened *here*, which
    includes waiting for a person and being deliberately abandoned.
    """

    PENDING = "pending"
    APPROVAL_REQUIRED = "approval_required"
    APPROVED = "approved"
    BLOCKED = "blocked"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    UNCERTAIN = "uncertain"
    RECONCILING = "reconciling"
    COMPLETED_LATE = "completed_late"
    NOT_APPLIED = "not_applied"
    PARTIAL = "partial"
    COMPENSATING = "compensating"
    COMPENSATED = "compensated"
    CANCELLED = "cancelled"
    TAKEN_OVER = "taken_over"

    @property
    def terminal(self) -> bool:
        """Nothing further will happen to this row on its own."""
        return self in _TERMINAL

    @property
    def applied(self) -> bool:
        """The change is known to have reached the target."""
        return self in (LedgerState.SUCCEEDED, LedgerState.COMPLETED_LATE)

    @property
    def unresolved(self) -> bool:
        """A person owes this row a decision before anything else may run."""
        return self in (LedgerState.UNCERTAIN, LedgerState.PARTIAL)


_TERMINAL: frozenset[LedgerState] = frozenset(
    {
        LedgerState.SUCCEEDED,
        LedgerState.FAILED,
        LedgerState.COMPLETED_LATE,
        LedgerState.NOT_APPLIED,
        LedgerState.COMPENSATED,
        LedgerState.CANCELLED,
        LedgerState.BLOCKED,
        LedgerState.TAKEN_OVER,
    }
)


TRANSITIONS: Mapping[LedgerState, frozenset[LedgerState]] = {
    LedgerState.PENDING: frozenset(
        {
            LedgerState.APPROVAL_REQUIRED,
            LedgerState.APPROVED,
            LedgerState.BLOCKED,
            LedgerState.RUNNING,
            LedgerState.CANCELLED,
        }
    ),
    LedgerState.APPROVAL_REQUIRED: frozenset(
        {LedgerState.APPROVED, LedgerState.BLOCKED, LedgerState.CANCELLED}
    ),
    LedgerState.APPROVED: frozenset(
        {LedgerState.RUNNING, LedgerState.BLOCKED, LedgerState.CANCELLED}
    ),
    LedgerState.RUNNING: frozenset(
        {
            LedgerState.SUCCEEDED,
            LedgerState.FAILED,
            LedgerState.UNCERTAIN,
            LedgerState.PARTIAL,
        }
    ),
    # The only door out of UNCERTAIN, and the reason the whole design works.
    LedgerState.UNCERTAIN: frozenset(
        {LedgerState.RECONCILING, LedgerState.TAKEN_OVER, LedgerState.CANCELLED}
    ),
    LedgerState.RECONCILING: frozenset(
        {
            LedgerState.COMPLETED_LATE,
            LedgerState.NOT_APPLIED,
            LedgerState.PARTIAL,
            LedgerState.UNCERTAIN,
        }
    ),
    LedgerState.PARTIAL: frozenset(
        {
            LedgerState.COMPENSATING,
            LedgerState.TAKEN_OVER,
            LedgerState.RECONCILING,
            LedgerState.CANCELLED,
        }
    ),
    LedgerState.COMPENSATING: frozenset(
        {LedgerState.COMPENSATED, LedgerState.FAILED, LedgerState.TAKEN_OVER}
    ),
    LedgerState.SUCCEEDED: frozenset({LedgerState.COMPENSATING}),
    LedgerState.COMPLETED_LATE: frozenset({LedgerState.COMPENSATING}),
    LedgerState.FAILED: frozenset(),
    LedgerState.NOT_APPLIED: frozenset(),
    LedgerState.COMPENSATED: frozenset(),
    LedgerState.CANCELLED: frozenset(),
    LedgerState.BLOCKED: frozenset(),
    LedgerState.TAKEN_OVER: frozenset(),
}
"""`UNCERTAIN -> RUNNING` is absent, and its absence is the control. There is no
path from "we do not know" to "try again" that does not pass through asking."""


RECONCILE_STATES: Mapping[ReconcileOutcome, LedgerState] = {
    ReconcileOutcome.COMPLETED_LATE: LedgerState.COMPLETED_LATE,
    ReconcileOutcome.NOT_APPLIED: LedgerState.NOT_APPLIED,
    ReconcileOutcome.PARTIAL: LedgerState.PARTIAL,
}


class ErrorCategory(str, Enum):
    """Why an attempt ended badly, in the categories an operator acts on."""

    NONE = "none"
    TIMEOUT = "timeout"
    UNAVAILABLE = "unavailable"
    REJECTED = "rejected"
    PRECONDITION = "precondition"
    PLATFORM = "platform"


SECRET_ARG_HINTS: tuple[str, ...] = (
    "password",
    "passwd",
    "secret",
    "token",
    "api_key",
    "apikey",
    "credential",
    "authorization",
    "auth",
    "private_key",
    "session",
    "cookie",
)
"""Argument names whose *values* never reach the ledger.

Matched as substrings on the lowercased key, so `db_password` and `AUTH_TOKEN`
are both caught. The key is kept and the value becomes a marker: an auditor must
be able to see that a credential was passed without the ledger becoming a place
credentials are stored (CLAUDE.md rule 4)."""

REDACTED = "***"


def redact_args(args: Mapping[str, Any]) -> dict[str, Any]:
    """The safe, normalised representation the ledger stores.

    Long values are truncated as well as redacted: an action argument carrying a
    100KB config blob would turn the ledger into a second copy of somebody's
    production configuration, with its own retention problem.
    """
    safe: dict[str, Any] = {}
    for key, value in args.items():
        name = str(key)
        if any(hint in name.lower() for hint in SECRET_ARG_HINTS):
            safe[name] = REDACTED
            continue
        if isinstance(value, Mapping):
            safe[name] = redact_args(value)
        elif isinstance(value, (list, tuple)):
            safe[name] = [
                redact_args(item) if isinstance(item, Mapping) else _clip(item)
                for item in value[:20]
            ]
        else:
            safe[name] = _clip(value)
    return safe


def _clip(value: Any) -> Any:
    if isinstance(value, str) and len(value) > MAX_ARG_CHARS:
        return value[:MAX_ARG_CHARS] + "…"
    return value


def idempotency_key(
    *,
    tenant_id: str,
    incident_id: str,
    action_id: str,
    args_digest: str,
    attempt: int,
) -> str:
    """Derived, never generated.

    The same request produces the same key across processes, restarts and
    retries, which is what makes a duplicate submit *find* the first execution
    rather than start a second one. The attempt number is in the key so that a
    deliberate retry after a proven `NOT_APPLIED` is a genuinely new operation at
    the target, with its own row and its own outcome.
    """
    payload = "|".join(
        [tenant_id, incident_id, action_id, args_digest, str(int(attempt))]
    )
    return "sf-" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:40]


@dataclass(frozen=True)
class ExecutionRecord:
    """One attempt, with everything a replay needs and nothing it does not.

    Immutable: every move returns a new record and the repository writes it,
    which is what keeps `started_at`, the policy version and the decision that
    authorised the attempt from being quietly rewritten later.
    """

    id: str
    tenant_id: str
    incident_id: str
    app_id: str
    action_id: str
    action_class: ActionClass
    risk_class: RiskClass
    state: LedgerState
    idempotency_key: str
    attempt: int
    requested_by: str
    requested_at: datetime
    args: Mapping[str, Any] = field(default_factory=dict)
    """Already redacted by `redact_args` before it reaches here."""
    args_digest: str = ""
    decision: Outcome = Outcome.BLOCK
    decision_reason: str = ""
    rules_hit: tuple[Mapping[str, Any], ...] = ()
    policy_version: int = 0
    policy_ref: str = ""
    floor_version: str = ""
    decision_fingerprint: str = ""
    approval_id: Optional[str] = None
    binding_id: str = ""
    adapter_execution_id: str = ""
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    error: str = ""
    error_category: ErrorCategory = ErrorCategory.NONE
    retry_blocked: bool = False
    reconcile_outcome: Optional[ReconcileOutcome] = None
    reconciled_at: Optional[datetime] = None
    reconcile_detail: Mapping[str, Any] = field(default_factory=dict)
    compensates_execution_id: Optional[str] = None
    compensated_by_execution_id: Optional[str] = None
    supersedes_execution_id: Optional[str] = None
    """The attempt this one is a retry of. `NOT_APPLIED -> retry` is the only
    way it gets set, so the chain reads as a history rather than a guess."""
    takeover_by: str = ""
    takeover_note: str = ""
    result_ref: str = ""
    detail: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.tenant_id or not self.incident_id:
            raise GovernanceValidationError(
                "an execution belongs to exactly one incident in one tenant"
            )
        if not self.idempotency_key:
            raise GovernanceValidationError(
                "an execution needs an idempotency key; without one a duplicate "
                "submit is a second mutation"
            )
        if self.attempt < 1:
            raise GovernanceValidationError("attempts start at 1")
        object.__setattr__(self, "error", str(self.error or "")[:MAX_ERROR_CHARS])
        # The invariant the whole sprint rests on, asserted on the object rather
        # than trusted to every caller that constructs one.
        if self.state is LedgerState.UNCERTAIN and not self.retry_blocked:
            object.__setattr__(self, "retry_blocked", True)

    # -- moves ---------------------------------------------------------------

    def moved_to(self, target: LedgerState, *, at: datetime, **changes: Any) -> "ExecutionRecord":
        if target not in TRANSITIONS[self.state]:
            raise GovernanceValidationError(
                f"an execution that is {self.state.value} cannot become "
                f"{target.value}; legal moves are "
                f"{sorted(s.value for s in TRANSITIONS[self.state])}"
            )
        finished = changes.pop("finished_at", None)
        if finished is None and target.terminal:
            finished = at
        return replace(
            self,
            state=target,
            finished_at=finished if finished is not None else self.finished_at,
            **changes,
        )

    def started(self, *, at: datetime, adapter_execution_id: str = "") -> "ExecutionRecord":
        return self.moved_to(
            LedgerState.RUNNING,
            at=at,
            started_at=at,
            adapter_execution_id=adapter_execution_id or self.adapter_execution_id,
        )

    def uncertain(self, *, at: datetime, error: str) -> "ExecutionRecord":
        """The timeout landing. Retry is blocked by construction, not by policy."""
        return self.moved_to(
            LedgerState.UNCERTAIN,
            at=at,
            error=error,
            error_category=ErrorCategory.TIMEOUT,
            retry_blocked=True,
            finished_at=None,
        )

    def reconciled(
        self,
        outcome: ReconcileOutcome,
        *,
        at: datetime,
        detail: Optional[Mapping[str, Any]] = None,
    ) -> "ExecutionRecord":
        """The verdict from asking the target system what actually happened.

        `retry_blocked` clears only for `NOT_APPLIED`. `COMPLETED_LATE` means the
        change is applied and repeating it would apply it twice; `PARTIAL` means
        somebody has to decide, and neither of those is a retry.
        """
        return self.moved_to(
            RECONCILE_STATES[outcome],
            at=at,
            reconcile_outcome=outcome,
            reconciled_at=at,
            reconcile_detail=dict(detail or {}),
            retry_blocked=outcome is not ReconcileOutcome.NOT_APPLIED,
        )

    def assert_retryable(self) -> None:
        """The gate every retry passes. Raises rather than returning a verdict,
        because a caller who forgot to check must fail closed."""
        if self.retry_blocked:
            raise RetryBlockedError(
                f"execution {self.id} is {self.state.value} and its outcome at the "
                "target is not known. Reconcile it first: retrying an operation "
                "that may already have been applied is how a rollback happens "
                "twice."
            )
        if self.state.applied:
            raise RetryBlockedError(
                f"execution {self.id} already completed ({self.state.value}); "
                "repeating it would apply the change a second time"
            )

    def retry_of(
        self, *, execution_id: str, at: datetime, requested_by: str, key: str
    ) -> "ExecutionRecord":
        """A fresh attempt at something that provably did not happen."""
        self.assert_retryable()
        return replace(
            self,
            id=execution_id,
            state=LedgerState.PENDING,
            attempt=self.attempt + 1,
            idempotency_key=key,
            requested_by=requested_by,
            requested_at=at,
            started_at=None,
            finished_at=None,
            error="",
            error_category=ErrorCategory.NONE,
            retry_blocked=False,
            reconcile_outcome=None,
            reconciled_at=None,
            reconcile_detail={},
            adapter_execution_id="",
            approval_id=None,
            supersedes_execution_id=self.id,
            takeover_by="",
            takeover_note="",
        )

    @property
    def open(self) -> bool:
        return not self.state.terminal

    @property
    def needs_reconciliation(self) -> bool:
        return self.state is LedgerState.UNCERTAIN

    def as_payload(self) -> dict[str, Any]:
        """What the event and the audit entry carry. Arguments are already
        redacted; the digest travels so a reviewer can prove two rows were the
        same request without the arguments being restated."""
        return {
            "execution_id": self.id,
            "incident_id": self.incident_id,
            "app_id": self.app_id,
            "action_id": self.action_id,
            "action_class": self.action_class.value,
            "risk_class": self.risk_class.value,
            "state": self.state.value,
            "attempt": self.attempt,
            "idempotency_key": self.idempotency_key,
            "decision": self.decision.value,
            "policy_version": self.policy_version,
            "policy_ref": self.policy_ref,
            "floor_version": self.floor_version,
            "rules_hit": [dict(hit) for hit in self.rules_hit],
            "approval_id": self.approval_id,
            "retry_blocked": self.retry_blocked,
            "reconcile_outcome": (
                self.reconcile_outcome.value if self.reconcile_outcome else None
            ),
            "error_category": self.error_category.value,
            "args_digest": self.args_digest,
            "requested_by": self.requested_by,
        }
