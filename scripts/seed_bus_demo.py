#!/usr/bin/env python
"""Put demonstrable traffic on the event bus of a running install.

Demo tooling, not product code: it uses only the public application services,
publishes a small causal chain into one tenant's timeline, and - with --poison -
parks one event in the dead-letter store by registering a consumer that fails,
so the settings screen's dead-letter pane and its requeue button have something
real to act on before the first integrations arrive in Sprint 3.

    python scripts/seed_bus_demo.py --tenant <tenant-id>
    python scripts/seed_bus_demo.py --tenant <tenant-id> --poison
    python scripts/seed_bus_demo.py --tenant <tenant-id> --drain

The demo's consumer group belongs to this script, not to the running server, so
the settings screen will report "no consumer for this group runs on this node"
when you press Requeue - which is true, and is what the split api/worker
deployment looks like. --drain plays that missing worker: it registers the same
group with a working handler and delivers whatever has been requeued.

The tenant id is the "org" id returned by signup, visible in the signup or login
response. Deleting this script leaves a working product.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from signalforge.application.events.bus import EventBus  # noqa: E402
from signalforge.application.events.dispatcher import Dispatcher  # noqa: E402
from signalforge.contracts.envelope import (  # noqa: E402
    Actor,
    ActorType,
    Authority,
    EventEnvelope,
    Plane,
    Source,
)
from signalforge.domain.events.dispatch import RetryPolicy  # noqa: E402
from signalforge.domain.events.ports import EventHandler  # noqa: E402
from signalforge.infrastructure.clock import SystemClock  # noqa: E402
from signalforge.infrastructure.config import load_settings  # noqa: E402
from signalforge.infrastructure.persistence.db import (  # noqa: E402
    make_engine,
    make_session_factory,
)
from signalforge.infrastructure.persistence.event_uow import (  # noqa: E402
    SqlEventUnitOfWork,
)

CHAIN = [
    ("alert.fired", Plane.SIGNAL, {"rule": "error-rate", "region": "a"}),
    ("incident.declared", Plane.INCIDENT, {"severity": "sev2", "declared_by": "demo"}),
    ("incident.paged", Plane.INCIDENT, {"rotation": "primary", "acked": False}),
    ("incident.brief.sent", Plane.INCIDENT, {"audience": "internal"}),
    ("app.metric.sampled", Plane.SIGNAL, {"metric": "latency_p99_ms", "value": 812}),
    ("app.metric.sampled", Plane.SIGNAL, {"metric": "latency_p99_ms", "value": 344}),
]

POISON_TOPIC = "demo.poison.event"


DEMO_GROUP = "demo-projector"


class DemoHandler(EventHandler):
    """Fails on one topic so an operator can watch retry, park and requeue;
    healthy=True is the same handler after the operator has "fixed" it."""

    name = DEMO_GROUP

    def __init__(self, healthy: bool = False) -> None:
        self.healthy = healthy
        self.seen = 0

    async def handle(self, envelope: EventEnvelope, uow) -> None:
        self.seen += 1
        if envelope.topic == POISON_TOPIC and not self.healthy:
            raise RuntimeError("demo handler cannot process this event")


def envelope(tenant_id: str, topic: str, plane: Plane, payload: dict, now, parent=None):
    return EventEnvelope(
        topic=topic,
        tenant_id=tenant_id,
        incident_id="inc-demo" if plane is Plane.INCIDENT else None,
        app_id="demo-app",
        plane=plane,
        occurred_at=now,
        actor=Actor(type=ActorType.SYSTEM, id="seed-bus-demo", display="Demo seeder"),
        source=Source(system="seed-bus-demo", authority=Authority.OBSERVED, simulated=True),
        causation_id=parent.event_id if parent else None,
        payload=payload,
    )


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tenant", required=True, help="tenant (org) id to publish into")
    parser.add_argument(
        "--poison", action="store_true", help="also park one event in the dead letters"
    )
    parser.add_argument(
        "--drain",
        action="store_true",
        help="play the worker that owns the demo group and deliver what was requeued",
    )
    args = parser.parse_args()

    settings = load_settings()
    engine = make_engine(settings.db_url)
    session_factory = make_session_factory(engine)
    clock = SystemClock()
    uow_factory = lambda: SqlEventUnitOfWork(session_factory, clock=clock)  # noqa: E731
    bus = EventBus(recording_clock=clock)

    if args.drain:
        dispatcher = Dispatcher(uow_factory, clock=clock)
        dispatcher.register(DEMO_GROUP, DemoHandler(healthy=True))
        report = await dispatcher.run_once()
        print(
            f"drained: {report.redelivered} redelivered, "
            f"{report.redelivery_failed} still failing, {report.delivered} new"
        )
        await engine.dispose()
        return 0

    parent = None
    published = 0
    for topic, plane, payload in CHAIN:
        async with uow_factory() as uow:
            parent = await bus.publish(
                uow, envelope(args.tenant, topic, plane, payload, clock.now(), parent)
            )
            await uow.commit()
        published += 1
        print(f"published #{parent.seq} {topic}")

    if args.poison:
        async with uow_factory() as uow:
            poisoned = await bus.publish(
                uow,
                envelope(
                    args.tenant, POISON_TOPIC, Plane.SIGNAL,
                    {"reason": "demonstrates the dead-letter pane"}, clock.now(),
                ),
            )
            await uow.commit()
        dispatcher = Dispatcher(
            uow_factory,
            clock=clock,
            retry_policy=RetryPolicy(max_attempts=3, base_backoff_s=0.01, max_backoff_s=0.02),
        )
        dispatcher.register(DEMO_GROUP, DemoHandler())
        report = await dispatcher.run_once()
        print(
            f"published #{poisoned.seq} {POISON_TOPIC}; "
            f"dead-lettered {report.dead_lettered} after retries"
        )

    await engine.dispose()
    print(f"done: {published} events on the timeline for tenant {args.tenant}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
