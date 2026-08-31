"""Event-backbone ports. Infrastructure implements these; the application layer
sees only these abstractions (CLAUDE.md rule 1).

Tenant isolation (REQ-013) is enforced inside the implementations exactly as it
is for identity, with one deliberate, documented exception: read_for_dispatch is
the platform's own worker path and spans tenants, because one dispatcher serves
the whole install. It hands the handler a full envelope carrying tenant_id, and
handlers write through the tenant-scoped repositories, so the isolation boundary
moves to the handler's own writes rather than disappearing.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from typing import TYPE_CHECKING, Optional, Sequence

from signalforge.contracts.envelope import EventEnvelope, Plane
from signalforge.domain.events.chain import ChainLink
from signalforge.domain.events.entities import (
    DeadLetter,
    DeadLetterState,
    EventSeal,
    RetentionWatermark,
)

if TYPE_CHECKING:  # pragma: no cover - typing only, avoids an import cycle
    from signalforge.application.events.uow import EventUnitOfWork


class EventStore(ABC):
    """Append-only. Nothing is ever updated or deleted here except by the
    retention sweeper, which may only remove signal-plane events (REQ-023)."""

    @abstractmethod
    async def append(self, envelope: EventEnvelope) -> EventEnvelope:
        """Assign seq, recorded_at and the chain hashes and store the envelope.
        Re-appending a known event_id returns the stored event unchanged, so a
        producer retry cannot duplicate history."""

    @abstractmethod
    async def get(self, tenant_id: str, event_id: str) -> Optional[EventEnvelope]: ...

    @abstractmethod
    async def read(
        self,
        *,
        tenant_id: str,
        after_seq: int = 0,
        until_seq: Optional[int] = None,
        topics: Optional[Sequence[str]] = None,
        incident_id: Optional[str] = None,
        plane: Optional[Plane] = None,
        limit: int = 200,
    ) -> Sequence[EventEnvelope]: ...

    @abstractmethod
    async def read_for_dispatch(
        self, *, after_seq: int, limit: int
    ) -> Sequence[EventEnvelope]:
        """Platform worker path: every tenant, strict seq order."""

    @abstractmethod
    async def causal_chain(self, *, tenant_id: str, event_id: str) -> Sequence[EventEnvelope]:
        """The root cause event, everything it caused transitively, in seq order."""

    @abstractmethod
    async def chain_links(
        self,
        *,
        tenant_id: str,
        plane: Plane,
        from_seq: int = 0,
        to_seq: Optional[int] = None,
        incident_id: Optional[str] = None,
    ) -> Sequence[ChainLink]: ...

    @abstractmethod
    async def max_seq(self) -> int: ...

    @abstractmethod
    async def tenants(self) -> Sequence[str]:
        """Every tenant with at least one recorded event - the retention sweep's
        work list, and the only cross-tenant read besides dispatch."""

    @abstractmethod
    async def prune(
        self,
        *,
        tenant_id: str,
        plane: Plane,
        recorded_before: datetime,
        protect_from_seq: Optional[int] = None,
    ) -> tuple[int, Optional[ChainLink], int]:
        """Remove aged events from one (tenant, plane) chain.

        protect_from_seq is the first sealed seq: nothing at or after it may be
        removed, because a seal promised that range would still verify. Returns
        (pruned count, the last pruned link to anchor the survivors, the number
        of otherwise-eligible events a seal protected).
        """


class RetentionStore(ABC):
    @abstractmethod
    async def get(self, tenant_id: str, plane: Plane) -> Optional[RetentionWatermark]: ...

    @abstractmethod
    async def upsert(self, watermark: RetentionWatermark) -> None: ...

    @abstractmethod
    async def list_for_tenant(self, tenant_id: str) -> Sequence[RetentionWatermark]: ...


class ConsumerOffsetStore(ABC):
    @abstractmethod
    async def get_all(self, group: str) -> dict[int, int]:
        """lane -> last delivered seq, for every lane this group has touched."""

    @abstractmethod
    async def set(self, group: str, lane: int, seq: int) -> None: ...

    @abstractmethod
    async def list_all(self) -> Sequence[tuple[str, int, int]]:
        """(group, lane, last_seq) for every stored offset - the lag view."""


class ProcessedLedger(ABC):
    """Makes at-least-once delivery harmless: a handler that already committed
    for this (group, event_id) is not run again (REQ-021)."""

    @abstractmethod
    async def seen(self, group: str, event_id: str) -> bool: ...

    @abstractmethod
    async def mark(self, group: str, event_id: str, at: datetime) -> None: ...


class DeadLetterStore(ABC):
    @abstractmethod
    async def add(self, dead_letter: DeadLetter) -> DeadLetter: ...

    @abstractmethod
    async def get(self, dead_letter_id: str, tenant_id: Optional[str] = None) -> Optional[DeadLetter]: ...

    @abstractmethod
    async def list(
        self,
        *,
        tenant_id: Optional[str] = None,
        group: Optional[str] = None,
        state: Optional[DeadLetterState] = None,
        limit: int = 100,
    ) -> Sequence[DeadLetter]: ...

    @abstractmethod
    async def update(self, dead_letter: DeadLetter) -> None: ...

    @abstractmethod
    async def take_requeued(self, groups: Sequence[str], limit: int) -> Sequence[DeadLetter]:
        """Operator-driven redelivery queue drained by each dispatcher sweep."""

    @abstractmethod
    async def count_by_state(self, tenant_id: Optional[str] = None) -> dict[str, int]: ...


class SealStore(ABC):
    @abstractmethod
    async def add(self, seal: EventSeal) -> EventSeal: ...

    @abstractmethod
    async def get(self, tenant_id: str, seal_id: str) -> Optional[EventSeal]: ...

    @abstractmethod
    async def list(
        self, *, tenant_id: str, incident_id: Optional[str] = None
    ) -> Sequence[EventSeal]: ...

    @abstractmethod
    async def latest(self, tenant_id: str) -> Optional[EventSeal]:
        """The tenant's most recent seal of any scope - what the next seal chains onto."""

    @abstractmethod
    async def latest_for_scope(
        self, tenant_id: str, incident_id: Optional[str]
    ) -> Optional[EventSeal]:
        """The most recent seal covering this same scope key - where the next
        seal of that scope resumes, so ranges neither overlap nor leave gaps."""


class EventHandler(ABC):
    """A consumer group's business logic.

    The handler is given the dispatcher's unit of work. Writing through it means
    the handler's state, the processed-ledger row and the offset advance commit
    together, so a crash mid-delivery redelivers the event and the handler runs
    again against unchanged state. Handlers with effects outside the database
    get at-least-once and must carry their own idempotency key.
    """

    name: str

    @abstractmethod
    async def handle(self, envelope: EventEnvelope, uow: "EventUnitOfWork") -> None: ...
