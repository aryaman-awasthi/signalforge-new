"""War-room ports (CLAUDE.md rule 1).

Tenant isolation (REQ-013) is enforced inside the implementations, as everywhere
else: every method takes `tenant_id`, the predicate lives in the repository, and
a cross-tenant read answers the way a read of nothing does.

Two deliberate cross-tenant methods exist, both of them work lists for the one
escalation job that serves the install, and both are documented here because
they cross the boundary every other method holds:
`PageRepository.overdue_across_tenants` and `WarRoomRepository.needing_harvest`.
Everything either job then writes goes back through the tenant-scoped methods.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Optional, Sequence

from signalforge.domain.warroom.entities import IncidentTask, RoomMember, WarRoom
from signalforge.domain.warroom.paging import Page


class WarRoomRepository(ABC):
    @abstractmethod
    async def add(self, room: WarRoom) -> None: ...

    @abstractmethod
    async def get(self, tenant_id: str, incident_id: str) -> Optional[WarRoom]:
        """One room per incident, keyed on the incident rather than on a room id.

        Declaring is idempotent in the way that matters here: two commanders
        pressing Declare seconds apart already resolve to one incident, and this
        keeps the room from being the place a second one appears.
        """

    @abstractmethod
    async def update(self, room: WarRoom) -> None: ...

    @abstractmethod
    async def needing_harvest(self, limit: int = 25) -> Sequence[WarRoom]:
        """Cross-tenant: open rooms with a bridge, for the commitment harvest."""


class RoomMemberRepository(ABC):
    @abstractmethod
    async def upsert(self, member: RoomMember) -> None:
        """Insert or replace by (tenant, incident, user). Idempotent, because a
        room is assembled on declare and then again on every escalation, and a
        person added twice must not appear twice."""

    @abstractmethod
    async def get(
        self, tenant_id: str, incident_id: str, user_id: str
    ) -> Optional[RoomMember]: ...

    @abstractmethod
    async def list(self, tenant_id: str, incident_id: str) -> Sequence[RoomMember]: ...


class PageRepository(ABC):
    @abstractmethod
    async def add(self, page: Page) -> None: ...

    @abstractmethod
    async def get(self, tenant_id: str, page_id: str) -> Optional[Page]: ...

    @abstractmethod
    async def update(self, page: Page) -> None: ...

    @abstractmethod
    async def list(
        self, tenant_id: str, incident_id: str, *, limit: int = 200
    ) -> Sequence[Page]:
        """The ack board, oldest first: the page that has been ringing longest is
        the one at the top."""

    @abstractmethod
    async def list_for_recipient(
        self, tenant_id: str, recipient: str, *, open_only: bool = True, limit: int = 50
    ) -> Sequence[Page]:
        """What is on one person's phone. Self-scoped by construction, which is
        what lets the mobile paging page carry no permission."""

    @abstractmethod
    async def chain(self, tenant_id: str, page_id: str) -> Sequence[Page]:
        """Every page in one escalation chain, first person asked first."""

    @abstractmethod
    async def overdue_across_tenants(
        self, now: datetime, limit: int = 100
    ) -> Sequence[Page]:
        """Cross-tenant: pages whose ack deadline has passed and which are still
        awaiting an answer. The escalation sweep's work list."""


class IncidentTaskRepository(ABC):
    @abstractmethod
    async def add(self, task: IncidentTask) -> None: ...

    @abstractmethod
    async def get(self, tenant_id: str, task_id: str) -> Optional[IncidentTask]: ...

    @abstractmethod
    async def update(self, task: IncidentTask) -> None: ...

    @abstractmethod
    async def list(
        self, tenant_id: str, incident_id: str, *, limit: int = 200
    ) -> Sequence[IncidentTask]: ...

    @abstractmethod
    async def find_by_key(
        self, tenant_id: str, incident_id: str, key: str
    ) -> Optional[IncidentTask]:
        """A harvested commitment's derived key. This is what makes harvesting
        idempotent: the same sentence read twice is one task, so a restart, a
        redelivery and two workers racing produce one board rather than three."""


class BridgeTranscript(ABC):
    """Reading and writing the collaboration channel a war room runs on.

    A port rather than a direct call to the invoker, because the war room's
    dependence on a bridge has to be optional: an install with no collaboration
    connector still declares incidents, still pages and still tracks tasks, and
    the transcript panel says there is no bridge instead of the room failing to
    open.
    """

    @abstractmethod
    async def post(
        self, tenant_id: str, binding_id: str, channel: str, text: str
    ) -> None: ...

    @abstractmethod
    async def fetch(
        self,
        tenant_id: str,
        binding_id: str,
        channel: str,
        *,
        since: datetime,
        limit: int = 200,
    ) -> Sequence[dict]: ...
