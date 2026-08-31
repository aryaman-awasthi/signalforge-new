"""Domain errors shared by every bounded context.

These four are transport-independent categories the API maps once (api/errors.py);
context-specific errors subclass them so a new domain never needs a new handler.
"""

from __future__ import annotations


class DomainError(Exception):
    """Base for all domain errors."""


class DomainValidationError(DomainError):
    """An entity or value object was constructed with invalid data."""


class NotFoundError(DomainError):
    """Entity does not exist in this tenant's scope."""


class ConflictError(DomainError):
    """An invariant on uniqueness or state ordering was violated."""
