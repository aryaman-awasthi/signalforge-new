"""The war room: who is in it, who was paged, and what was committed (REQ-045).

Pure domain. Nothing here imports FastAPI, SQLAlchemy or a vendor SDK, and
nothing here holds a clock of its own - every time-dependent function takes the
moment it is reasoning about, which is what lets a drill run the same code at
100x speed with no drill-aware branch anywhere in it.
"""

from signalforge.domain.warroom.blast import (
    BlastRadius,
    MembershipGap,
    RoleRequirement,
    downstream_services,
    required_roles,
)
from signalforge.domain.warroom.brief import build_brief
from signalforge.domain.warroom.commitments import Commitment, harvest
from signalforge.domain.warroom.entities import (
    BriefSource,
    IncidentTask,
    JoinBrief,
    MemberSource,
    RoomMember,
    TaskSource,
    TaskStatus,
    WarRoom,
)
from signalforge.domain.warroom.errors import (
    EscalationExhaustedError,
    NotYourPageError,
    PageAlreadySettledError,
    PageNotFoundError,
    TaskNotFoundError,
    WarRoomNotFoundError,
    WarRoomValidationError,
)
from signalforge.domain.warroom.paging import (
    DEFAULT_ACK_TIMEOUT_S,
    Page,
    PageDelivery,
    PageState,
    channels_for,
    first_timeout_s,
    next_target,
)

__all__ = [
    "BlastRadius",
    "BriefSource",
    "Commitment",
    "DEFAULT_ACK_TIMEOUT_S",
    "EscalationExhaustedError",
    "IncidentTask",
    "JoinBrief",
    "MemberSource",
    "MembershipGap",
    "NotYourPageError",
    "Page",
    "PageAlreadySettledError",
    "PageDelivery",
    "PageNotFoundError",
    "PageState",
    "RoleRequirement",
    "RoomMember",
    "TaskNotFoundError",
    "TaskSource",
    "TaskStatus",
    "WarRoom",
    "WarRoomNotFoundError",
    "WarRoomValidationError",
    "build_brief",
    "channels_for",
    "downstream_services",
    "first_timeout_s",
    "harvest",
    "next_target",
    "required_roles",
]
