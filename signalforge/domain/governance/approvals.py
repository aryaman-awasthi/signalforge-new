"""Approvals (REQ-061, CTL-AUD-03).

An approval is a *claim about a moment*: this person, holding this permission,
looking at this action with these arguments under these rules, said yes at this
time. Every one of those clauses is a field, and every one of them is re-checked
at execution time, because the gap between approving and executing is exactly
where the interesting failures live.

## What an approval is bound to

The `decision_fingerprint` - a hash over the action, its arguments, the risk
class the policy engine reached and the policy version in force. An approval for
"roll back payments to v11" is not an approval for "roll back payments to v9",
not an approval for the same rollback after the policy re-classified it, and not
an approval for a different action that happens to be in the same incident. This
is what "not reusable across unrelated actions" means mechanically rather than
by convention.

## What is re-checked at execution

Five things, and the reason each one exists:

| Check | The failure it prevents |
|---|---|
| not expired | an approval left open on a laptop over a weekend |
| not already spent | one approval, two executions |
| not revoked | somebody changed their mind and said so |
| fingerprint matches | the arguments changed after approval |
| approver still holds the permission | a role removed between approving and executing |

The last one is the one people forget, and it is why `assert_valid_now` takes
the approver's *current* permission rather than trusting the `permission` field
recorded at issue time. An approval is not a capability token; it is a record of
a decision by somebody who was entitled to make it, and if they stopped being
entitled, the record stops carrying authority.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Mapping, Optional, Sequence

from signalforge.contracts.roles import Permission, Role
from signalforge.domain.governance.errors import (
    ApprovalInvalidError,
    GovernanceValidationError,
)
from signalforge.domain.governance.policy import (
    APPROVE_HIGH_RISK_ROLES,
    MAX_APPROVAL_TTL_S,
    MIN_APPROVAL_TTL_S,
    PolicyFloorViolation,
)

MAX_NOTE_CHARS = 500


class ApprovalState(str, Enum):
    REQUESTED = "requested"
    """Somebody asked. Nothing is authorised yet, and the request is on the
    record so an unanswered one is visible rather than silent."""
    GRANTED = "granted"
    USED = "used"
    """Spent on exactly one execution. An approval is not a standing permission."""
    EXPIRED = "expired"
    REVOKED = "revoked"
    DENIED = "denied"

    @property
    def open(self) -> bool:
        return self in (ApprovalState.REQUESTED, ApprovalState.GRANTED)


@dataclass(frozen=True)
class Approval:
    """One decision by one person about one execution."""

    id: str
    tenant_id: str
    incident_id: str
    app_id: str
    execution_id: str
    action_id: str
    state: ApprovalState
    requested_by: str
    requested_at: datetime
    decision_fingerprint: str
    policy_version: int
    policy_ref: str = ""
    required_roles: tuple[Role, ...] = ()
    approver_user_id: str = ""
    approver_label: str = ""
    approver_roles: tuple[Role, ...] = ()
    permission: Optional[Permission] = None
    """The permission the approver actually exercised, recorded as evidence.
    Checked again at execution against what they hold *then*."""
    issued_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None
    used_at: Optional[datetime] = None
    revoked_at: Optional[datetime] = None
    note: str = ""
    detail: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.tenant_id or not self.incident_id or not self.execution_id:
            raise GovernanceValidationError(
                "an approval names a tenant, an incident and the execution it "
                "authorises"
            )
        if not self.decision_fingerprint:
            raise GovernanceValidationError(
                "an approval must be bound to the decision it approves; an "
                "unbound approval is a standing permission"
            )
        object.__setattr__(self, "note", str(self.note or "")[:MAX_NOTE_CHARS])
        if self.issued_at is not None and self.expires_at is not None:
            ttl = (self.expires_at - self.issued_at).total_seconds()
            if ttl > MAX_APPROVAL_TTL_S + 1:
                raise PolicyFloorViolation(
                    f"FLOOR-APPROVAL-TTL-CAP: an approval may not live longer "
                    f"than {MAX_APPROVAL_TTL_S}s; this one was given {int(ttl)}s"
                )
            if ttl < MIN_APPROVAL_TTL_S - 1:
                raise GovernanceValidationError(
                    f"an approval must live at least {MIN_APPROVAL_TTL_S}s"
                )

    # -- moves ---------------------------------------------------------------

    def granted(
        self,
        *,
        at: datetime,
        ttl_s: int,
        approver_user_id: str,
        approver_label: str,
        approver_roles: Sequence[Role],
        permission: Permission,
        note: str = "",
    ) -> "Approval":
        """A person said yes. The TTL is capped here as well as in the policy
        document, because this is the last place before the record exists."""
        if self.state is not ApprovalState.REQUESTED:
            raise ApprovalInvalidError(
                f"approval {self.id} is {self.state.value} and cannot be granted "
                "again"
            )
        if approver_user_id == self.requested_by:
            # Separation of duties. Requesting and approving are two acts by two
            # people, and an incident where one person can do both is an incident
            # with no control at all.
            raise ApprovalInvalidError(
                "the person who requested a high-risk action cannot approve it; "
                "approval is a second person's decision"
            )
        eligible = tuple(
            role for role in approver_roles if role in APPROVE_HIGH_RISK_ROLES
        )
        if self.required_roles:
            eligible = tuple(role for role in eligible if role in self.required_roles)
        if not eligible:
            raise ApprovalInvalidError(
                f"approving this action needs one of "
                f"{sorted(r.value for r in self.required_roles) or 'the roles that hold approve_high_risk'}; "
                "the approver holds none of them"
            )
        bounded = max(MIN_APPROVAL_TTL_S, min(int(ttl_s), MAX_APPROVAL_TTL_S))
        return replace(
            self,
            state=ApprovalState.GRANTED,
            approver_user_id=approver_user_id,
            approver_label=approver_label,
            approver_roles=eligible,
            permission=permission,
            issued_at=at,
            expires_at=at + timedelta(seconds=bounded),
            note=note or self.note,
        )

    def denied(self, *, at: datetime, by: str, note: str = "") -> "Approval":
        return replace(
            self,
            state=ApprovalState.DENIED,
            approver_user_id=by,
            revoked_at=at,
            note=note or self.note,
        )

    def revoked(self, *, at: datetime, by: str, note: str = "") -> "Approval":
        return replace(
            self,
            state=ApprovalState.REVOKED,
            revoked_at=at,
            note=note or self.note,
            detail={**dict(self.detail), "revoked_by": by},
        )

    def used(self, *, at: datetime) -> "Approval":
        return replace(self, state=ApprovalState.USED, used_at=at)

    def expired(self, *, at: datetime) -> "Approval":
        return replace(self, state=ApprovalState.EXPIRED, revoked_at=at)

    # -- reads ---------------------------------------------------------------

    def is_expired(self, now: datetime) -> bool:
        return self.expires_at is not None and now >= self.expires_at

    def seconds_left(self, now: datetime) -> int:
        if self.expires_at is None:
            return 0
        return max(0, int((self.expires_at - now).total_seconds()))

    def assert_valid_now(
        self,
        *,
        now: datetime,
        fingerprint: str,
        approver_permissions: Sequence[Permission],
        policy_version: int,
    ) -> None:
        """The gate at execution time. Raises; there is no "warn and proceed".

        Ordered so the message an operator sees names the thing they can fix:
        state first (grant it), then expiry (ask again), then the fingerprint
        (the request changed), then the permission (a role was removed), then
        the policy version (the rules moved under an approved action).
        """
        if self.state is ApprovalState.USED:
            raise ApprovalInvalidError(
                f"approval {self.id} has already been spent on an execution; one "
                "approval authorises one execution"
            )
        if self.state is not ApprovalState.GRANTED:
            raise ApprovalInvalidError(
                f"approval {self.id} is {self.state.value}, not granted"
            )
        if self.is_expired(now):
            raise ApprovalInvalidError(
                f"approval {self.id} expired at "
                f"{self.expires_at.isoformat() if self.expires_at else '?'}; an "
                "approval that outlives the situation it was granted for is a "
                "standing permission, so it is refused rather than extended"
            )
        if fingerprint != self.decision_fingerprint:
            raise ApprovalInvalidError(
                f"approval {self.id} was given for a different request - the "
                "action, its arguments or its risk classification have changed "
                "since it was approved. Approve the new request explicitly."
            )
        if Permission.APPROVE_HIGH_RISK not in set(approver_permissions):
            raise ApprovalInvalidError(
                f"the approver of {self.id} no longer holds approve_high_risk on "
                "this application; a permission removed after an approval "
                "withdraws the authority that approval carried"
            )
        if policy_version != self.policy_version:
            raise ApprovalInvalidError(
                f"approval {self.id} was granted under policy version "
                f"{self.policy_version} and this application is now on version "
                f"{policy_version}. A high-risk action approved under one set of "
                "rules is not silently re-interpreted under another: approve it "
                "again under the current policy."
            )

    def as_payload(self) -> dict[str, Any]:
        return {
            "approval_id": self.id,
            "execution_id": self.execution_id,
            "incident_id": self.incident_id,
            "action_id": self.action_id,
            "state": self.state.value,
            "requested_by": self.requested_by,
            "approver_user_id": self.approver_user_id,
            "approver_roles": [role.value for role in self.approver_roles],
            "permission": self.permission.value if self.permission else None,
            "required_roles": [role.value for role in self.required_roles],
            "issued_at": self.issued_at.isoformat() if self.issued_at else None,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "policy_version": self.policy_version,
            "policy_ref": self.policy_ref,
            "decision_fingerprint": self.decision_fingerprint,
        }
