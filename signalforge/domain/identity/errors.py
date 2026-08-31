"""Identity domain errors. Pure domain: no framework or transport concerns."""

from __future__ import annotations


class DomainError(Exception):
    """Base for all identity domain errors."""


class DomainValidationError(DomainError):
    """An entity or value object was constructed with invalid data."""


class AuthenticationError(DomainError):
    """Credentials or token could not be verified. Message must stay generic."""


class PermissionDeniedError(DomainError):
    """RBAC denied the action. Carries the decision for the audit event."""

    def __init__(self, message: str, decision=None):
        super().__init__(message)
        self.decision = decision


class RateLimitedError(DomainError):
    """Too many attempts; retry_after_s tells the caller when to try again."""

    def __init__(self, message: str, retry_after_s: int):
        super().__init__(message)
        self.retry_after_s = retry_after_s


class NotFoundError(DomainError):
    """Entity does not exist in this tenant's scope."""


class ConflictError(DomainError):
    """Uniqueness violated (duplicate email, slug, open invite)."""
