"""Support process for the crash-recovery acceptance test.

Runs a real dispatcher against a real database file and is hard-killed by the
parent test mid-consumption. The handler deliberately holds its transaction open
for a moment after writing, so the kill lands between "handler did its work" and
"the commit made it durable" - the exact window where a weaker bus loses events
or applies them twice.

Usage: python -m signalforge.tests.events.crash_runner <db_path> [handler_delay_s]
"""

from __future__ import annotations

import asyncio
import sys

from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from signalforge.application.events.dispatcher import Dispatcher
from signalforge.contracts.envelope import EventEnvelope
from signalforge.domain.events.ports import EventHandler
from signalforge.infrastructure.clock import SystemClock
from signalforge.infrastructure.persistence.db import make_session_factory
from signalforge.infrastructure.persistence.event_uow import SqlEventUnitOfWork

GROUP = "projector"
PROJECTION_TOPIC = "test.projection.applied"


class SlowProjectionHandler(EventHandler):
    """One durable row per event, written through the dispatcher's transaction."""

    name = "slow-projector"

    def __init__(self, delay_s: float = 0.0):
        self._delay_s = delay_s

    async def handle(self, envelope: EventEnvelope, uow) -> None:
        await uow.audit.append(
            tenant_id=envelope.tenant_id,
            topic=PROJECTION_TOPIC,
            actor_type="system",
            actor_id=self.name,
            payload={
                "event_id": str(envelope.event_id),
                "seq": envelope.seq,
                "partition_key": envelope.partition_key,
                "source_topic": envelope.topic,
            },
        )
        if self._delay_s:
            await asyncio.sleep(self._delay_s)


def make_dispatcher(db_path: str, delay_s: float = 0.0) -> Dispatcher:
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", poolclass=NullPool)
    session_factory = make_session_factory(engine)
    clock = SystemClock()
    dispatcher = Dispatcher(
        lambda: SqlEventUnitOfWork(session_factory, clock=clock),
        clock=clock,
        batch_size=10,
    )
    dispatcher.register(GROUP, SlowProjectionHandler(delay_s))
    return dispatcher


async def _main() -> None:
    db_path = sys.argv[1]
    delay_s = float(sys.argv[2]) if len(sys.argv) > 2 else 0.05
    await make_dispatcher(db_path, delay_s).run_forever(interval_s=0.02)


if __name__ == "__main__":  # pragma: no cover - executed as a child process
    asyncio.run(_main())
