"""Identity domain errors. Pure domain: no framework or transport concerns.

The four generic categories live in domain/shared/errors.py so every bounded
context raises the same ones; they are re-exported here because identity was
their first home and the API error map imports them from this module.
"""

from __future__ import annotations

from signalforge.domain.shared.errors import (
    ConflictError,
    DomainError,
    DomainValidationError,
    NotFoundError,
)

__all__ = [
    "AuthenticationError",
    "ConflictError",
    "DomainError",
    "DomainValidationError",
    "NotFoundError",
    "PermissionDeniedError",
    "RateLimitedError",
]


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
