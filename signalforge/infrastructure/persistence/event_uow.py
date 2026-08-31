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
    SqlSealStore,
)
from signalforge.infrastructure.security.audit import AuditChainWriter


class SqlEventUnitOfWork(EventUnitOfWork):
    def __init__(self, session_factory: async_sessionmaker[AsyncSession], *, clock: Clock):
        self._session_factory = session_factory
        self._clock = clock

    async def __aenter__(self) -> "SqlEventUnitOfWork":
        self._session = self._session_factory()
        self.events = SqlEventStore(self._session, clock=self._clock)
        self.offsets = SqlConsumerOffsetStore(self._session, clock=self._clock)
        self.processed = SqlProcessedLedger(self._session)
        self.dead_letters = SqlDeadLetterStore(self._session)
        self.seals = SqlSealStore(self._session)
        self.audit = AuditChainWriter(self._session, clock=self._clock)
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if exc_type is not None:
            await self._session.rollback()
        await self._session.close()

    async def commit(self) -> None:
        await self._session.commit()

    async def rollback(self) -> None:
        await self._session.rollback()
