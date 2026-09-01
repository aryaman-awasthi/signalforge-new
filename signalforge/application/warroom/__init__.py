"""War-room use cases (REQ-045): opening the room, paging it, and the work it
produces.

Four services rather than one, split along the lines their failure modes fall on.
`WarRoomService` decides who should be there and must survive paging being down;
`PagingService` reaches those people and must survive a channel being down;
`BridgeService` reads and writes the conversation and must survive the bridge
being absent entirely; `IncidentTaskService` owns the board, which works whether
or not any of the other three do.
"""

from signalforge.application.warroom.bridge import BridgeService, HarvestSweep
from signalforge.application.warroom.paging import PagingService, PagingSweep
from signalforge.application.warroom.service import WarRoomService, WarRoomView
from signalforge.application.warroom.tasks import IncidentTaskService

__all__ = [
    "BridgeService",
    "HarvestSweep",
    "IncidentTaskService",
    "PagingService",
    "PagingSweep",
    "WarRoomService",
    "WarRoomView",
]
