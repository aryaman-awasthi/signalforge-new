"""SQL unit of work for the event backbone: one AsyncSession shared by the event
store, the offsets, the idempotency ledger, dead letters, seals and the audit
writer, so a delivery commits everything or nothing."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from signalforge.application.events.uow import EventUnitOfWork
from signalforge.domain.identity.ports import Clock
from signalforge.infrastructure.persistence.event_store import (
    SqlConsumerOffsetStore,
    SqlDeadLetterStore,
    SqlEventStore,
    SqlProcessedLedger,
    SqlRetentionStore,
    SqlSealStore,
)
from signalforge.infrastructure.persistence.agent_runs import SqlAgentRunStore
from signalforge.infrastructure.persistence.incidents import SqlChangeWindowStore
from signalforge.infrastructure.security.audit import AuditChainWriter, SqlAuditReader


class SqlEventUnitOfWork(EventUnitOfWork):
    """recording_clock stamps everything durable and operational (recorded_at,
    offset timestamps) and is always wall time; clock is the platform clock a
    drill may have made virtual, and only dates the audit entries."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        clock: Clock,
        recording_clock: Clock | None = None,
    ):
        self._session_factory = session_factory
        self._clock = clock
        self._recording_clock = recording_clock or clock

    async def __aenter__(self) -> "SqlEventUnitOfWork":
        self._session = self._session_factory()
        self.events = SqlEventStore(self._session, clock=self._recording_clock)
        self.offsets = SqlConsumerOffsetStore(self._session, clock=self._recording_clock)
        self.processed = SqlProcessedLedger(self._session)
        self.dead_letters = SqlDeadLetterStore(self._session)
        self.seals = SqlSealStore(self._session)
        self.retention = SqlRetentionStore(self._session)
        self.agent_runs = SqlAgentRunStore(self._session)
        self.changes = SqlChangeWindowStore(self._session)
        self.audit = AuditChainWriter(self._session, clock=self._clock)
        self.audit_log = SqlAuditReader(self._session)
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if exc_type is not None:
            await self._session.rollback()
        await self._session.close()

    async def commit(self) -> None:
        await self._session.commit()

    async def rollback(self) -> None:
        await self._session.rollback()
