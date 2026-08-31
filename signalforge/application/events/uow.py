"""Unit of work over the event-backbone ports. Everything on it shares one
transaction, which is what makes the outbox an outbox: the event and the state
change that produced it are one commit or neither."""

from __future__ import annotations

from abc import ABC, abstractmethod

from signalforge.domain.events.ports import (
    ConsumerOffsetStore,
    DeadLetterStore,
    EventStore,
    ProcessedLedger,
    RetentionStore,
    SealStore,
)
from signalforge.domain.identity.ports import AuditWriter
from signalforge.domain.observability.agent_runs import AgentRunStore


class EventUnitOfWork(ABC):
    events: EventStore
    offsets: ConsumerOffsetStore
    processed: ProcessedLedger
    dead_letters: DeadLetterStore
    seals: SealStore
    retention: RetentionStore
    agent_runs: AgentRunStore
    audit: AuditWriter

    @abstractmethod
    async def __aenter__(self) -> "EventUnitOfWork": ...

    @abstractmethod
    async def __aexit__(self, exc_type, exc, tb) -> None: ...

    @abstractmethod
    async def commit(self) -> None: ...

    @abstractmethod
    async def rollback(self) -> None: ...
