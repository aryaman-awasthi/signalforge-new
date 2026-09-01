"""Incident lifecycle errors.

They subclass the shared categories so api/errors.py maps them without a new
handler. `HumanOnlyTransitionError` is a 422 rather than a 403 for the same
reason the policy floor is: refusing to let a machine declare an incident is a
rejected *instruction*, not a decision about a caller. There is no permission
that grants it, so there is nothing for a 403 to name.
"""

from __future__ import annotations

from signalforge.domain.shared.errors import (
    ConflictError,
    DomainValidationError,
    NotFoundError,
)


class IncidentNotFoundError(NotFoundError):
    """No incident with that id in this tenant's scope."""


class IncidentValidationError(DomainValidationError):
    """An incident was constructed or edited with something the domain refuses."""


class IllegalTransitionError(ConflictError):
    """The lifecycle does not allow this move from the state it is in.

    A conflict rather than a validation error: the request was well formed and
    would have been legal from another state, so the caller is being told about
    ordering, not about its own input.
    """


class HumanOnlyTransitionError(DomainValidationError):
    """A machine attempted a transition only a person may make.

    Declaring, changing a severity and closing are the three acts this platform
    reserves for people (REQ-043). The refusal lives in the domain so that every
    caller - a use case, an agent, a drill, a future automation - meets it.
    """


class IncidentAlreadyOpenError(ConflictError):
    """This application already has an incident that has not been closed."""
