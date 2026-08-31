"""On-call rotation and escalation-policy value objects (REQ-014).

Frozen dataclasses: editing means replacing the whole value through the org
setup use cases, which keeps validation in one place. Timers are seconds and
bounded so a typo cannot silence an escalation for a year.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional

from signalforge.domain.identity.errors import DomainValidationError

MAX_STEP_TIMEOUT_S = 86_400
MAX_STEPS = 10
MAX_HANDOFF_INTERVAL_H = 744  # one month of hourly shifts


@dataclass(frozen=True)
class OnCallRotation:
    id: str
    tenant_id: str
    name: str
    user_ids: tuple[str, ...]
    app_id: Optional[str] = None
    handoff_interval_hours: int = 168

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise DomainValidationError("rotation name must not be empty")
        if not self.user_ids:
            raise DomainValidationError("a rotation needs at least one member")
        if len(set(self.user_ids)) != len(self.user_ids):
            raise DomainValidationError("rotation members must be unique")
        if not 1 <= self.handoff_interval_hours <= MAX_HANDOFF_INTERVAL_H:
            raise DomainValidationError(
                f"handoff interval must be 1..{MAX_HANDOFF_INTERVAL_H} hours"
            )

    def on_call(self, shifts_elapsed: int) -> str:
        return self.user_ids[shifts_elapsed % len(self.user_ids)]

    def next_in_rotation(self, user_id: str) -> str:
        if user_id not in self.user_ids:
            raise DomainValidationError("user is not a member of this rotation")
        index = self.user_ids.index(user_id)
        return self.user_ids[(index + 1) % len(self.user_ids)]


class EscalationTargetType(str, Enum):
    USER = "user"
    ROTATION = "rotation"


@dataclass(frozen=True)
class EscalationStep:
    target_type: EscalationTargetType
    target_id: str
    timeout_s: int

    def __post_init__(self) -> None:
        if not self.target_id:
            raise DomainValidationError("escalation step needs a target")
        if not 1 <= self.timeout_s <= MAX_STEP_TIMEOUT_S:
            raise DomainValidationError(
                f"step timeout must be 1..{MAX_STEP_TIMEOUT_S} seconds"
            )


@dataclass(frozen=True)
class EscalationPolicy:
    id: str
    tenant_id: str
    name: str
    steps: tuple[EscalationStep, ...]
    app_id: Optional[str] = None

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise DomainValidationError("policy name must not be empty")
        if not self.steps:
            raise DomainValidationError("a policy needs at least one step")
        if len(self.steps) > MAX_STEPS:
            raise DomainValidationError(f"a policy may have at most {MAX_STEPS} steps")

    def page_offsets_s(self) -> tuple[int, ...]:
        """When each step is paged, relative to the escalation starting: step 0
        immediately, each later step when every earlier ack timer has elapsed."""
        offsets = [0]
        for step in self.steps[:-1]:
            offsets.append(offsets[-1] + step.timeout_s)
        return tuple(offsets)
