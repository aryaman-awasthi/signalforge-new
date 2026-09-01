"""Incident lifecycle ports (CLAUDE.md rule 1).

Tenant isolation (REQ-013) is enforced inside the implementations exactly as it
is everywhere else: every method that touches tenant data takes `tenant_id` and
applies the predicate in the repository, never in a handler, and a cross-tenant
read answers the way a read of nothing does.

There is one deliberate cross-tenant method, `IncidentRepository.needing_sync`,
and it is the same shape as the connector health loop's work list: one ITSM
retry job serves the install, every incident it returns carries its own
`tenant_id`, and everything the job writes goes back through the tenant-scoped
methods.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional, Sequence

from signalforge.domain.incidents.changes import ChangeWindow
from signalforge.domain.incidents.entities import Incident, IncidentTransition
from signalforge.domain.incidents.lifecycle import LifecycleState


class IncidentRepository(ABC):
    @abstractmethod
    async def add(self, incident: Incident) -> None: ...

    @abstractmethod
    async def get(self, tenant_id: str, incident_id: str) -> Optional[Incident]: ...

    @abstractmethod
    async def update(self, incident: Incident) -> None: ...

    @abstractmethod
    async def list(
        self,
        tenant_id: str,
        *,
        app_id: Optional[str] = None,
        states: Optional[Sequence[LifecycleState]] = None,
        limit: int = 50,
    ) -> Sequence[Incident]: ...

    @abstractmethod
    async def open_for_app(self, tenant_id: str, app_id: str) -> Optional[Incident]:
        """The one incident an application may have running.

        This is the duplicate guard: two incident commanders pressing Declare on
        the same application seconds apart is a normal thing to happen, and the
        second one must join the first rather than open a second war room.
        """

    @abstractmethod
    async def next_ordinal(self, tenant_id: str) -> int:
        """The tenant's next incident number, for a human-readable reference."""

    @abstractmethod
    async def needing_sync(self, limit: int = 50) -> Sequence[Incident]:
        """Cross-tenant: incidents whose ITSM link is pending, failed or
        uncertain. Documented at the port because it crosses the boundary every
        other method holds."""

    @abstractmethod
    async def count_open(self) -> dict[str, int]:
        """Cross-tenant count of open incidents by severity, for the scrape.
        Counts only - /metrics has no session and can name nobody."""


class IncidentTransitionRepository(ABC):
    """Append-only. Nothing here is ever updated or deleted: the history of who
    moved an incident and when is what a post-incident review reads."""

    @abstractmethod
    async def append(self, transition: IncidentTransition) -> None: ...

    @abstractmethod
    async def list(
        self, tenant_id: str, incident_id: str, *, limit: int = 200
    ) -> Sequence[IncidentTransition]: ...


class ChangeWindowStore(ABC):
    """The projection of deployment events, per (tenant, app, change).

    Reachable from two units of work on purpose. The bus consumer writes through
    the event unit of work, so the projection, the idempotency-ledger row and the
    consumer offset are one commit; the API reads through the identity one, so a
    screen never opens a transaction the dispatcher is using.
    """

    @abstractmethod
    async def get(
        self, tenant_id: str, app_id: str, change_id: str
    ) -> Optional[ChangeWindow]: ...

    @abstractmethod
    async def put(self, window: ChangeWindow) -> None:
        """Insert or replace by (tenant, app, change). The caller folds; this
        only stores, so redelivery cannot produce a second row."""

    @abstractmethod
    async def list(
        self, tenant_id: str, app_id: str, *, limit: int = 100
    ) -> Sequence[ChangeWindow]: ...

    @abstractmethod
    async def count_open(self) -> int:
        """Cross-tenant count of changes still in flight, for the scrape."""
