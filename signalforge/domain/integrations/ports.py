"""Ports the shipped adapters need (CLAUDE.md rule 1).

Tenant isolation (REQ-013) is a property of the implementations, exactly as it
is for the identity repositories: every method that reads a tenant's rows takes
`tenant_id` and filters on it inside the repository, so an adapter that forgot
to scope a query cannot exist.

`IntegrationUnitOfWork` is deliberately a second unit of work rather than four
more repositories on the identity one. What these stores hold is the *integrated
system's* data - tickets in a simulated ITSM, messages in a simulated bridge -
not SignalForge's own state, and an adapter call is not a use case: it must not
be able to commit half of an identity transaction it never opened.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional, Sequence

from signalforge.contracts.canonical import NotificationChannel
from signalforge.domain.integrations.entities import (
    IndexedDocument,
    NotificationDelivery,
    StoredMessage,
    StoredTicket,
)


class TicketStore(ABC):
    @abstractmethod
    async def get(
        self, tenant_id: str, app_id: str, ticket_id: str
    ) -> Optional[StoredTicket]: ...

    @abstractmethod
    async def upsert(self, ticket: StoredTicket) -> None: ...

    @abstractmethod
    async def count(self, tenant_id: str) -> int:
        """How many tickets this tenant has, used to mint the next reference."""

    @abstractmethod
    async def list(
        self, tenant_id: str, *, app_id: Optional[str] = None, limit: int = 100
    ) -> Sequence[StoredTicket]: ...


class MessageStore(ABC):
    @abstractmethod
    async def add(self, message: StoredMessage) -> bool:
        """Store a message. False when its id already exists - a replayed script
        or a redelivered post is not a second message."""

    @abstractmethod
    async def read(
        self,
        tenant_id: str,
        channel: str,
        *,
        since,
        until,
        limit: int,
    ) -> Sequence[StoredMessage]: ...

    @abstractmethod
    async def channel_epoch(self, tenant_id: str, channel: str) -> Optional[object]:
        """When this channel was first used, or None if it never has been. The
        script player schedules from it, so it must survive a restart."""


class NotificationStore(ABC):
    @abstractmethod
    async def get(
        self, tenant_id: str, notification_id: str
    ) -> Optional[NotificationDelivery]: ...

    @abstractmethod
    async def upsert(self, delivery: NotificationDelivery) -> None: ...

    @abstractmethod
    async def list_for_recipient(
        self,
        tenant_id: str,
        recipient: str,
        *,
        channels: Sequence[NotificationChannel] = (),
        open_only: bool = False,
        limit: int = 50,
    ) -> Sequence[NotificationDelivery]: ...


class KnowledgeStore(ABC):
    @abstractmethod
    async def upsert(self, document: IndexedDocument) -> None: ...

    @abstractmethod
    async def get(self, tenant_id: str, doc_id: str) -> Optional[IndexedDocument]: ...

    @abstractmethod
    async def all(
        self, tenant_id: str, *, app_id: Optional[str] = None
    ) -> Sequence[IndexedDocument]:
        """The corpus BM25 ranks against. Tenant-scoped: one tenant's runbooks
        can never score against another's."""


class IntegrationUnitOfWork(ABC):
    tickets: TicketStore
    messages: MessageStore
    notifications: NotificationStore
    knowledge: KnowledgeStore

    @abstractmethod
    async def __aenter__(self) -> "IntegrationUnitOfWork": ...

    @abstractmethod
    async def __aexit__(self, exc_type, exc, tb) -> None: ...

    @abstractmethod
    async def commit(self) -> None: ...


class EmbeddingProvider(ABC):
    """Vectors for semantic ranking, when a deployment has one.

    kb-index ranks with BM25 whether or not this exists (REQ-035): lexical
    ranking is the floor, embeddings are the improvement. The provider is a port
    rather than a direct call into the LLM adapter so that a model outage
    degrades the *ranking* and never the search - see `model` for what a failure
    is allowed to do.
    """

    model: str

    @abstractmethod
    async def embed(self, texts: Sequence[str]) -> Sequence[Sequence[float]]:
        """Raise on failure. The caller falls back to BM25 and says so."""


class NotificationSink(ABC):
    """Live delivery to whoever is connected right now (the /ws fan-out).

    Returns how many subscribers actually received it, which is the difference
    between a notification that was *sent* and one that is merely *queued* -
    and the reason the in-app adapter can report either honestly.
    """

    @abstractmethod
    def deliver(
        self,
        tenant_id: str,
        recipient: str,
        channel: NotificationChannel,
        message: dict,
    ) -> int: ...
