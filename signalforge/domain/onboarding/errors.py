"""Onboarding domain errors.

They subclass the shared categories so api/errors.py maps them without a new
handler. `PolicyFloorError` is a 422 rather than a 403 on purpose: refusing to
let a high-risk action auto-execute is a rejected *instruction*, not a decision
about the caller - an org owner holding every permission is refused exactly as
firmly as anyone else, because the floor is not a permission (CLAUDE.md rule 4).
"""

from __future__ import annotations

from signalforge.domain.shared.errors import (
    ConflictError,
    DomainValidationError,
    NotFoundError,
)


class ApplicationNotFoundError(NotFoundError):
    """No application with that app_id in this tenant's scope."""


class DuplicateApplicationError(ConflictError):
    """This tenant already registered an application under that app_id."""


class OnboardingValidationError(DomainValidationError):
    """A wizard step was given something the domain will not store."""


class PolicyFloorError(DomainValidationError):
    """A permanent floor was contradicted.

    High-risk action classes can never be set to auto-execute, an approval TTL
    can never exceed the cap, and a high-risk action can never be left without
    an approver role that actually holds approve_high_risk.
    """
