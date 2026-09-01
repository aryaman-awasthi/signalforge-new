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
from signalforge.domain.identity.ports import AuditReader, AuditWriter
from signalforge.domain.incidents.ports import ChangeWindowStore
from signalforge.domain.observability.agent_runs import AgentRunStore


class EventUnitOfWork(ABC):
    events: EventStore
    offsets: ConsumerOffsetStore
    processed: ProcessedLedger
    dead_letters: DeadLetterStore
    seals: SealStore
    retention: RetentionStore
    agent_runs: AgentRunStore
    changes: ChangeWindowStore
    """The change-window projection (S4-T02). On this unit of work because a
    consumer that folded a deployment event outside the dispatcher's transaction
    could advance its offset without having stored what it read."""
    audit: AuditWriter
    audit_log: AuditReader
    """The chain, read back (REQ-072). Beside the writer rather than behind a
    second unit of work, because an auditor reading a replay and the seal it is
    checked against must be looking at one consistent snapshot."""

    @abstractmethod
    async def __aenter__(self) -> "EventUnitOfWork": ...

    @abstractmethod
    async def __aexit__(self, exc_type, exc, tb) -> None: ...

    @abstractmethod
    async def commit(self) -> None: ...

    @abstractmethod
    async def rollback(self) -> None: ...
