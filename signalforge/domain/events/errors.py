"""Event-backbone domain errors, subclassing the shared categories so the one
API error map already knows their HTTP status."""

from __future__ import annotations

from signalforge.domain.shared.errors import (
    ConflictError,
    DomainError,
    DomainValidationError,
    NotFoundError,
)

__all__ = [
    "ChainBrokenError",
    "DeadLetterNotFoundError",
    "EventNotFoundError",
    "ReplayRangeError",
    "SealConflictError",
    "UnknownConsumerGroupError",
]


class EventNotFoundError(NotFoundError):
    """No event with that id in this tenant's scope."""


class DeadLetterNotFoundError(NotFoundError):
    """No dead letter with that id in this tenant's scope."""


class UnknownConsumerGroupError(NotFoundError):
    """A dead letter names a consumer group this dispatcher has not registered,
    so requeueing it here would silently do nothing."""


class SealConflictError(ConflictError):
    """The requested range is already sealed, or contains no events to seal."""


class ReplayRangeError(DomainValidationError):
    """Replay bounds are inconsistent (until before after_seq, limit out of range)."""


class ChainBrokenError(DomainError):
    """The stored hash chain does not verify: events were altered or removed
    outside the append path."""

    def __init__(self, message: str, broken_seqs: tuple[int, ...] = ()):
        super().__init__(message)
        self.broken_seqs = broken_seqs
