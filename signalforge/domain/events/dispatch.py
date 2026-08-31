"""Delivery policy: what the dispatcher does when a handler fails, and which
topics a consumer group is subscribed to. Pure decisions, no IO, so the failure
behaviour is testable without a database or a clock.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional, Sequence

MAX_ATTEMPTS_CAP = 10
"""A handler that has failed ten times is poisoned, not unlucky. Configuration
may lower this; it can never raise it, so a misconfiguration cannot turn the
dispatcher into an infinite retry loop against a downstream system."""


class DeliveryOutcome(str, Enum):
    RETRY = "retry"
    DEAD_LETTER = "dead_letter"


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 3
    base_backoff_s: float = 0.05
    max_backoff_s: float = 5.0

    def __post_init__(self) -> None:
        if not 1 <= self.max_attempts <= MAX_ATTEMPTS_CAP:
            raise ValueError(f"max_attempts must be between 1 and {MAX_ATTEMPTS_CAP}")
        if self.base_backoff_s < 0 or self.max_backoff_s < self.base_backoff_s:
            raise ValueError("backoff bounds must be non-negative and ordered")

    def decide(self, attempts: int) -> DeliveryOutcome:
        """attempts is how many times the handler has now failed for this event."""
        return (
            DeliveryOutcome.RETRY
            if attempts < self.max_attempts
            else DeliveryOutcome.DEAD_LETTER
        )

    def backoff_s(self, attempts: int) -> float:
        """Exponential, capped. attempts is the number of failures so far."""
        if attempts < 1:
            return 0.0
        return min(self.base_backoff_s * (2 ** (attempts - 1)), self.max_backoff_s)


def topic_matches(topic: str, patterns: Optional[Sequence[str]]) -> bool:
    """No patterns means the group takes every topic. A pattern is either an
    exact topic or a namespace wildcard such as "system.*"."""
    if not patterns:
        return True
    for pattern in patterns:
        if pattern.endswith(".*"):
            if topic.startswith(pattern[:-1]):
                return True
        elif topic == pattern:
            return True
    return False
