"""The incident lifecycle state machine (REQ-043).

Six states, and the whole point of the file is which of the moves between them a
machine may make.

| State | What it means | Who can move into it |
|---|---|---|
| `steady` | nothing is breaching | detection |
| `watch` | something is being observed - the quiet "AI is checking" banner | detection |
| `proposed` | corroborated evidence and a severity the matrix computed | detection |
| `active` | a person declared an incident | **a human, only** |
| `recovering` | a person says mitigation is applied and it is being validated | **a human, only** |
| `closed` | a person signed it off | **a human, only** |

The first three are *stances*: detection owns them, they are derived from
measurements every five seconds, and no incident exists. The last three are an
incident, and an incident begins because somebody pressed Declare.

Two rules are permanent and are enforced here rather than in a service, so that
no second caller can reach the same state by another door:

1. **A machine may never declare, change a severity, or close.** Detection can
   raise a stance as far as `proposed` and no further. The severity proposal
   value object already refuses to be constructed without `requires_human`
   (domain/detection/severity.py); this is the other half of the same floor.
2. **Closed is terminal.** Nothing reopens a closed incident - not a person and
   not a regression. Closure is the record that a human signed off; a system
   that can un-sign it is a system whose record means nothing. A regression
   after closure is a *new* declaration, which is a new decision by a new
   person at a new time, and that is exactly what the record should say.

The one move a machine *is* allowed to make inside an incident is
`recovering -> active`: a regression during validation escalates, and escalation
is the safe direction. It cannot create an incident, cannot relax one, and
cannot end one (REQ-065 builds the revalidation loop on top of it in S6).
"""

from __future__ import annotations

from enum import Enum
from typing import Mapping, Optional

from signalforge.contracts.envelope import ActorType
from signalforge.domain.incidents.errors import (
    HumanOnlyTransitionError,
    IllegalTransitionError,
)


class LifecycleState(str, Enum):
    STEADY = "steady"
    WATCH = "watch"
    PROPOSED = "proposed"
    ACTIVE = "active"
    RECOVERING = "recovering"
    CLOSED = "closed"

    @property
    def rank(self) -> int:
        return {
            "steady": 0,
            "watch": 1,
            "proposed": 2,
            "active": 3,
            "recovering": 4,
            "closed": 5,
        }[self.value]

    @property
    def declared(self) -> bool:
        """An incident exists (or existed). The first three states are stances."""
        return self in (
            LifecycleState.ACTIVE,
            LifecycleState.RECOVERING,
            LifecycleState.CLOSED,
        )

    @property
    def open(self) -> bool:
        """An incident somebody is still running."""
        return self in (LifecycleState.ACTIVE, LifecycleState.RECOVERING)

    @property
    def terminal(self) -> bool:
        return self is LifecycleState.CLOSED


STANCES: tuple[LifecycleState, ...] = (
    LifecycleState.STEADY,
    LifecycleState.WATCH,
    LifecycleState.PROPOSED,
)
"""The three detection owns. An application with no incident is in one of them,
and which one is recomputed from measurements rather than stored as a decision."""


TRANSITIONS: Mapping[LifecycleState, frozenset[LifecycleState]] = {
    # Detection moves freely among the stances: a breach can clear as easily as
    # it opened, and a stance that could only escalate would be a ratchet.
    LifecycleState.STEADY: frozenset(
        {LifecycleState.WATCH, LifecycleState.PROPOSED, LifecycleState.ACTIVE}
    ),
    LifecycleState.WATCH: frozenset(
        {LifecycleState.STEADY, LifecycleState.PROPOSED, LifecycleState.ACTIVE}
    ),
    LifecycleState.PROPOSED: frozenset(
        {LifecycleState.STEADY, LifecycleState.WATCH, LifecycleState.ACTIVE}
    ),
    LifecycleState.ACTIVE: frozenset(
        {LifecycleState.RECOVERING, LifecycleState.CLOSED}
    ),
    LifecycleState.RECOVERING: frozenset(
        {LifecycleState.ACTIVE, LifecycleState.CLOSED}
    ),
    LifecycleState.CLOSED: frozenset(),
}
"""Legal moves regardless of who is asking. `HUMAN_ONLY` narrows them further.

`active -> steady` is deliberately absent. An incident does not stop being an
incident because the metrics recovered; it is closed by a person, and closing is
what returns the application to a stance."""


HUMAN_ONLY: frozenset[tuple[LifecycleState, LifecycleState]] = frozenset(
    {
        # Declaring, from any stance.
        (LifecycleState.STEADY, LifecycleState.ACTIVE),
        (LifecycleState.WATCH, LifecycleState.ACTIVE),
        (LifecycleState.PROPOSED, LifecycleState.ACTIVE),
        # Saying mitigation is applied.
        (LifecycleState.ACTIVE, LifecycleState.RECOVERING),
        # Closing, from either open state.
        (LifecycleState.ACTIVE, LifecycleState.CLOSED),
        (LifecycleState.RECOVERING, LifecycleState.CLOSED),
    }
)
"""Every move into, through and out of an incident, except the one escalation a
machine may perform (`recovering -> active`)."""


DECLARING: frozenset[tuple[LifecycleState, LifecycleState]] = frozenset(
    (stance, LifecycleState.ACTIVE) for stance in STANCES
)


def is_human_only(current: LifecycleState, target: LifecycleState) -> bool:
    return (current, target) in HUMAN_ONLY


def assert_transition(
    current: LifecycleState,
    target: LifecycleState,
    *,
    by: ActorType,
    what: str = "this incident",
) -> None:
    """The floor. Raises rather than returning a verdict, because every caller
    of a lifecycle move must fail closed."""
    if current.terminal:
        # Checked before "already in that state", because closing a closed
        # incident is the case a person actually hits and "it is already closed"
        # would leave them wondering whether trying again would work.
        raise IllegalTransitionError(
            f"{what} was closed and closure is final; a regression after closure "
            "is a new declaration by a person, not a reopening"
        )
    if current is target:
        raise IllegalTransitionError(f"{what} is already {current.value}")
    if target not in TRANSITIONS[current]:
        raise IllegalTransitionError(
            f"{what} is {current.value} and cannot become {target.value}; "
            f"legal moves are {sorted(s.value for s in TRANSITIONS[current])}"
        )
    if is_human_only(current, target) and by is not ActorType.HUMAN:
        raise HumanOnlyTransitionError(
            f"{current.value} -> {target.value} is a human act: only a person "
            f"declares an incident, changes its severity or closes it (REQ-043). "
            f"A {by.value} actor may not."
        )


def assert_severity_change(state: LifecycleState, *, by: ActorType) -> None:
    """Changing a severity is the third reserved act.

    Only on an open incident, and only by a person. A closed incident's severity
    is part of what was signed off, and re-grading it afterwards would rewrite
    the record rather than correct it.
    """
    if by is not ActorType.HUMAN:
        raise HumanOnlyTransitionError(
            f"severity is changed by a person, never by a {by.value} actor "
            "(REQ-043); detection proposes and stops there"
        )
    if not state.open:
        raise IllegalTransitionError(
            f"an incident that is {state.value} has no severity to change; "
            "severity is settled while the incident is being run"
        )


STANCE_STATES: Mapping[str, LifecycleState] = {
    "steady": LifecycleState.STEADY,
    "watch": LifecycleState.WATCH,
    "proposed": LifecycleState.PROPOSED,
}


def state_for_stance(stance: str) -> LifecycleState:
    """A detection stance read as a lifecycle state.

    Unknown stances read `steady` rather than raising: the lifecycle view is a
    screen, and a detector that grew a fourth stance must not be able to make an
    application unreadable.
    """
    return STANCE_STATES.get(str(stance or "").lower(), LifecycleState.STEADY)


def parse_state(raw: str, *, allowed: Optional[tuple[LifecycleState, ...]] = None) -> LifecycleState:
    try:
        state = LifecycleState(str(raw).strip().lower())
    except ValueError as exc:
        raise IllegalTransitionError(
            f"{raw!r} is not a lifecycle state; one of "
            f"{[s.value for s in LifecycleState]}"
        ) from exc
    if allowed is not None and state not in allowed:
        raise IllegalTransitionError(
            f"{state.value} is not a state this route may set; "
            f"one of {[s.value for s in allowed]}"
        )
    return state
