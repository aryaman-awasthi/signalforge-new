"""Event-backbone test rig: a temp-file SQLite database, a wall clock used for
recorded_at, a separate scenario clock used for occurred_at, and factories for
both units of work (identity, so the outbox can be proven transactional with
state, and events, which is what the dispatcher runs on)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from signalforge.application.events.bus import EventBus
from signalforge.application.events.dispatcher import Dispatcher
from signalforge.application.events.uow import EventUnitOfWork
from signalforge.contracts.envelope import (
    Actor,
    ActorType,
    EventEnvelope,
    Plane,
    Source,
)
from signalforge.domain.events.ports import EventHandler
from signalforge.infrastructure.persistence.db import make_session_factory
from signalforge.infrastructure.persistence.event_uow import SqlEventUnitOfWork
from signalforge.infrastructure.persistence.models import Base
from signalforge.infrastructure.persistence.uow import SqlIdentityUnitOfWork
from signalforge.tests.helpers import FakeClock

WALL_START = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)
SCENARIO_START = datetime(2026, 1, 15, 3, 0, tzinfo=timezone.utc)


def make_event(
    *,
    topic: str = "app.thing.happened",
    tenant_id: str = "tenant-a",
    incident_id: Optional[str] = None,
    app_id: Optional[str] = None,
    plane: Plane = Plane.SIGNAL,
    occurred_at: Optional[datetime] = None,
    payload: Optional[dict[str, Any]] = None,
    causation_id: Optional[uuid.UUID] = None,
    correlation_id: Optional[str] = None,
    event_id: Optional[uuid.UUID] = None,
) -> EventEnvelope:
    fields: dict[str, Any] = {}
    if event_id is not None:
        fields["event_id"] = event_id
    return EventEnvelope(
        topic=topic,
        tenant_id=tenant_id,
        app_id=app_id,
        incident_id=incident_id,
        plane=plane,
        occurred_at=occurred_at or SCENARIO_START,
        actor=Actor(type=ActorType.SYSTEM, id="test-emitter"),
        source=Source(system="test-emitter"),
        causation_id=causation_id,
        correlation_id=correlation_id,
        payload=payload if payload is not None else {"n": 1},
        **fields,
    )


class RecordingHandler(EventHandler):
    """Counts every raw invocation (at-least-once evidence) and writes exactly
    one durable row per event through the unit of work (idempotency evidence)."""

    def __init__(self, name: str = "recorder", fail_topics: tuple[str, ...] = ()):
        self.name = name
        self.fail_topics = fail_topics
        self.invocations: list[str] = []

    async def handle(self, envelope: EventEnvelope, uow: EventUnitOfWork) -> None:
        self.invocations.append(envelope.topic)
        if envelope.topic in self.fail_topics:
            raise RuntimeError(f"poisoned handler cannot process {envelope.topic}")
        await uow.audit.append(
            tenant_id=envelope.tenant_id,
            topic="test.projection.applied",
            actor_type="system",
            actor_id=self.name,
            payload={
                "event_id": str(envelope.event_id),
                "seq": envelope.seq,
                "partition_key": envelope.partition_key,
                "source_topic": envelope.topic,
            },
        )


@dataclass
class BusCtx:
    uow_factory: Callable[[], EventUnitOfWork]
    identity_uow_factory: Callable[[], SqlIdentityUnitOfWork]
    bus: EventBus
    wall_clock: FakeClock
    scenario_clock: FakeClock
    db_path: Path
    session_factory: Any = field(default=None)

    async def publish(self, envelope: Optional[EventEnvelope] = None, **kwargs) -> EventEnvelope:
        async with self.uow_factory() as uow:
            stored = await self.bus.publish(uow, envelope or make_event(**kwargs))
            await uow.commit()
        return stored

    def dispatcher(self, **kwargs) -> Dispatcher:
        return Dispatcher(self.uow_factory, clock=self.wall_clock, **kwargs)

    def projection_rows(self, tenant_id: str = "tenant-a") -> list[dict]:
        import json
        import sqlite3

        conn = sqlite3.connect(self.db_path)
        try:
            rows = conn.execute(
                "select payload from audit_log where tenant_id = ? "
                "and topic = 'test.projection.applied' order by seq",
                (tenant_id,),
            ).fetchall()
        finally:
            conn.close()
        return [json.loads(r[0]) for r in rows]


@pytest.fixture
def bus(tmp_path):
    db_path = tmp_path / "events.db"
    sync_engine = create_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(sync_engine)
    sync_engine.dispose()

    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", poolclass=NullPool)
    session_factory = make_session_factory(engine)
    wall = FakeClock(WALL_START)
    scenario = FakeClock(SCENARIO_START)
    fernet_key = Fernet.generate_key().decode()

    return BusCtx(
        uow_factory=lambda: SqlEventUnitOfWork(session_factory, clock=wall),
        identity_uow_factory=lambda: SqlIdentityUnitOfWork(
            session_factory, fernet_key=fernet_key, clock=wall
        ),
        bus=EventBus(recording_clock=wall),
        wall_clock=wall,
        scenario_clock=scenario,
        db_path=db_path,
        session_factory=session_factory,
    )
