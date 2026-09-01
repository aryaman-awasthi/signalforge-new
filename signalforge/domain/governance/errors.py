"""Governance and execution errors (S6).

They subclass the shared categories so `api/errors.py` maps them without a new
handler, and the choice of category is itself part of the safety model:

- `PolicyFloorViolation` is a **422**, not a 403. Refusing to let a rollback
  auto-execute is a rejected *instruction*, not a decision about the caller: an
  org owner holding every permission is refused exactly as firmly as anyone
  else, because the floor is not a permission (CLAUDE.md rule 4).
- `RetryBlockedError` is a **409**. The request is well-formed and the caller is
  entitled to make it; the *state* forbids it until somebody reconciles. A 403
  would say "you may not", which is wrong - anybody may, once the world is known.
- `ApprovalInvalidError` is a **422** for the same reason as the floor: an
  expired approval is not an authorization failure of the person presenting it,
  it is a claim that no longer means what it meant.
- `ClosureBlockedError` is a **409**: the incident cannot close *yet*.
"""

from __future__ import annotations

from signalforge.domain.shared.errors import (
    ConflictError,
    DomainValidationError,
    NotFoundError,
)


class GovernanceValidationError(DomainValidationError):
    """A governance value object was given something it will not store."""


class PolicyFloorViolation(DomainValidationError):
    """A permanent platform floor was contradicted.

    High-risk classes can never auto-execute, a read-only classification can
    never be given to a mutating class, an approval TTL can never exceed the
    cap, and audit can never be switched off. Raised at *construction* of the
    policy document, so there is no half-valid policy that a later reader could
    act on.
    """


class PolicyNotFoundError(NotFoundError):
    """No policy document at that version for this application."""


class ActiveIncidentConfirmationRequired(ConflictError):
    """A policy edit landed while an incident on that application is open.

    Refused until the editor confirms explicitly. Changing the rules under a
    running response is sometimes right and must never be accidental.
    """


class ExecutionNotFoundError(NotFoundError):
    """No execution with that id in this tenant."""


class ExecutionBlockedError(ConflictError):
    """Policy, permission or approval refused this execution at this moment."""


class ApprovalRequiredError(ConflictError):
    """The action needs a human approval that does not exist yet."""


class ApprovalInvalidError(DomainValidationError):
    """An approval exists but does not authorise *this* execution now.

    Expired, revoked, already spent, issued against a different action or a
    different policy version, or granted by somebody who no longer holds the
    permission.
    """


class RetryBlockedError(ConflictError):
    """The outcome of the previous attempt is unknown.

    The single most important refusal in Sprint 6: a timeout is not a failure,
    and retrying one is how a rollback gets applied twice.
    """


class DuplicateExecutionError(ConflictError):
    """This exact action, with these exact arguments, already ran."""


class CommunicationNotFoundError(NotFoundError):
    """No communication with that id in this tenant."""


class CommunicationStateError(ConflictError):
    """A communication was asked to move somewhere its lifecycle forbids."""


class UngroundedCommunicationError(DomainValidationError):
    """A draft cited nothing, or cited a node that is not in this incident."""


class ClosureBlockedError(ConflictError):
    """A validation layer that gates closure has not passed."""


class ValidationNotFoundError(NotFoundError):
    """No validation cycle with that id in this tenant."""
