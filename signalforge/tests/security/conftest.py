"""Shared fixtures for the security suite (artifact 18)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from signalforge.domain.identity.ports import Clock
from signalforge.infrastructure.persistence.models import Base


class FakeClock(Clock):
    def __init__(self, start: datetime | None = None):
        self.current = start or datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)

    def now(self) -> datetime:
        return self.current

    def advance(self, **kwargs) -> None:
        self.current += timedelta(**kwargs)


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
async def db_session_factory():
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest.fixture
async def db_session(db_session_factory):
    async with db_session_factory() as session:
        yield session
