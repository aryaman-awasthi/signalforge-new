"""War-room errors (REQ-045).

Separate types rather than one, because the API maps them to genuinely different
answers and the person reading the response needs to know which of the three it
was.

`NotYourPageError` subclasses `PermissionDeniedError` on purpose, so it renders
as the same bare 403 every other authorization refusal does. The caller is told
nothing about whose page it is - a name would be the leak - and the reason goes
where this platform already puts refusal evidence: the tenant's audit chain and
the incident's own timeline, which the person who *was* paged can see.
"""

from __future__ import annotations

from signalforge.domain.identity.errors import PermissionDeniedError
from signalforge.domain.shared.errors import (
    ConflictError,
    DomainValidationError,
    NotFoundError,
)


class WarRoomValidationError(DomainValidationError):
    """A war room, page or task was asked for in a shape that cannot exist."""


class WarRoomNotFoundError(NotFoundError):
    """No war room for this incident, in this tenant."""


class PageNotFoundError(NotFoundError):
    """No such page in this tenant. Identical whether it belongs to another
    tenant or to nobody (REQ-013)."""


class TaskNotFoundError(NotFoundError):
    pass


class NotYourPageError(PermissionDeniedError):
    """Somebody tried to acknowledge, or complete, on another person's behalf.

    Acknowledgement is the one act in this platform that a role cannot confer:
    it means "I have this", and only the person who has it can say so. The frozen
    roles matrix gives the SME no permission at all, so the control that replaces
    one here is that the page names its recipient and the session names itself,
    and the two have to agree.
    """


class PageAlreadySettledError(ConflictError):
    """The page is acknowledged, failed or superseded, and none of those can be
    moved out of by a machine. Escalating an acknowledged page would page
    somebody for work that already has an owner."""


class EscalationExhaustedError(ConflictError):
    """Every step and every rotation member has been paged.

    Deliberately an error rather than a silent stop: an escalation that runs out
    of people is the moment somebody has to be told that nobody answered, and a
    quiet no-op is how that becomes nobody's problem.
    """
