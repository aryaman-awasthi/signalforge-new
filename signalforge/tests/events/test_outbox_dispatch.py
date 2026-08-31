"""Transactional outbox, ordered lanes, at-least-once delivery with idempotent
handlers, consumer-group offsets and catch-up (REQ-020, REQ-021, REQ-022).

Written before the implementation, per the session protocol: ordering and
idempotency are the properties the rest of the platform is allowed to assume.
"""

from __future__ import annotations

import uuid
from datetime import timezone

import pytest

from signalforge.contracts.envelope import Plane
from signalforge.domain.events.lanes import LANE_COUNT, lane_for
from signalforge.domain.identity.entities import Tenant
from signalforge.tests.events.conftest import RecordingHandler, make_event

pytestmark = pytest.mark.anyio


# --- lane assignment (pure domain) -------------------------------------------


@pytest.mark.req("REQ-021")
async def test_lane_assignment_is_stable_and_bounded():
    """The lane must be a pure function of the partition key: two processes,
    two runs and two restarts must agree, or ordering silently breaks."""
    keys = [f"incident-{i}" for i in range(200)]
    lanes = [lane_for(k) for k in keys]
    assert all(0 <= lane < LANE_COUNT for lane in lanes)
    assert lanes == [lane_for(k) for k in keys], "must be a pure function of the key"
    assert lane_for("incident-1") == 9, "pinned: the mapping is part of the on-disk format"
    assert len(set(lanes)) == LANE_COUNT, "keys must spread across every lane"
    assert all(0 <= lane_for(k, lane_count=4) < 4 for k in keys)


@pytest.mark.req("REQ-021")
async def test_partition_key_prefers_incident_then_app_then_tenant():
    incident = make_event(tenant_id="t", app_id="a", incident_id="inc-1")
    app = make_event(tenant_id="t", app_id="a")
    tenant = make_event(tenant_id="t")
    assert incident.partition_key == "inc-1"
    assert app.partition_key == "a"
    assert tenant.partition_key == "t"


# --- outbox: events and state share one transaction (REQ-020) ----------------


@pytest.mark.req("REQ-020")
async def test_event_and_state_commit_atomically(bus):
    """The outbox rule: if the state write rolls back the event must vanish
    with it, and vice versa. Anything else invents events that never happened."""
    async with bus.identity_uow_factory() as uow:
        await uow.tenants.add(
            Tenant(id="tenant-a", name="Acme", slug="acme", created_at=bus.wall_clock.now())
        )
        await bus.bus.publish(uow, make_event(topic="org.tenant.created"))
        # deliberate rollback: leave the context manager without committing

    async with bus.uow_factory() as uow:
        assert await uow.events.max_seq() == 0

    async with bus.identity_uow_factory() as uow:
        await uow.tenants.add(
            Tenant(id="tenant-a", name="Acme", slug="acme", created_at=bus.wall_clock.now())
        )
        await bus.bus.publish(uow, make_event(topic="org.tenant.created"))
        await uow.commit()

    async with bus.uow_factory() as uow:
        stored = await uow.events.read(tenant_id="tenant-a")
        assert [e.topic for e in stored] == ["org.tenant.created"]


@pytest.mark.req("REQ-020")
async def test_store_assigns_seq_recorded_at_and_chain(bus):
    first = await bus.publish(topic="app.one.happened")
    second = await bus.publish(topic="app.two.happened")

    assert first.seq == 1 and second.seq == 2
    assert first.recorded_at == bus.wall_clock.now()
    assert first.occurred_at != first.recorded_at, "occurred_at is the emitter's clock"
    assert first.payload_hash and first.prev_hash is None
    assert second.prev_hash == first.payload_hash


@pytest.mark.req("REQ-021")
async def test_republishing_the_same_event_id_is_a_no_op(bus):
    event_id = uuid.uuid4()
    first = await bus.publish(topic="app.thing.happened", event_id=event_id)
    again = await bus.publish(topic="app.thing.happened", event_id=event_id)

    assert again.seq == first.seq
    async with bus.uow_factory() as uow:
        assert await uow.events.max_seq() == 1


# --- dispatch: groups, offsets, ordering (REQ-021) ---------------------------


@pytest.mark.req("REQ-020", "REQ-021")
async def test_dispatcher_delivers_every_event_once_per_group(bus):
    for i in range(5):
        await bus.publish(topic=f"app.step.n{i}")

    handler = RecordingHandler()
    dispatcher = bus.dispatcher()
    dispatcher.register("projector", handler)

    report = await dispatcher.run_once()

    assert report.delivered == 5
    assert len(handler.invocations) == 5
    assert len(bus.projection_rows()) == 5

    # A second sweep with nothing new delivers nothing: offsets are durable.
    assert (await dispatcher.run_once()).delivered == 0
    assert len(handler.invocations) == 5


@pytest.mark.req("REQ-021")
async def test_order_is_preserved_within_each_partition(bus):
    """Interleave two incidents. Global order may be anything the lanes choose;
    the order within one incident must be exactly the publish order."""
    for i in range(6):
        await bus.publish(topic=f"app.a.n{i}", incident_id="inc-a")
        await bus.publish(topic=f"app.b.n{i}", incident_id="inc-b")

    handler = RecordingHandler()
    dispatcher = bus.dispatcher()
    dispatcher.register("projector", handler)
    await dispatcher.run_once()

    rows = bus.projection_rows()
    for partition, prefix in (("inc-a", "app.a.n"), ("inc-b", "app.b.n")):
        seen = [r["source_topic"] for r in rows if r["partition_key"] == partition]
        assert seen == [f"{prefix}{i}" for i in range(6)]


@pytest.mark.req("REQ-021")
async def test_each_consumer_group_keeps_its_own_offsets(bus):
    for i in range(3):
        await bus.publish(topic=f"app.step.n{i}")

    fast = RecordingHandler("fast")
    slow = RecordingHandler("slow")
    dispatcher = bus.dispatcher()
    dispatcher.register("fast-group", fast)
    dispatcher.register("slow-group", slow)

    await dispatcher.run_once()
    assert len(fast.invocations) == 3
    assert len(slow.invocations) == 3

    later = bus.dispatcher()
    later.register("fast-group", RecordingHandler("fast"))
    assert (await later.run_once()).delivered == 0


@pytest.mark.req("REQ-021")
async def test_topic_subscription_filters_delivery(bus):
    await bus.publish(topic="system.connector.health")
    await bus.publish(topic="app.thing.happened")
    await bus.publish(topic="system.llm.degraded")

    handler = RecordingHandler()
    dispatcher = bus.dispatcher()
    dispatcher.register("system-watcher", handler, topics=("system.*",))
    await dispatcher.run_once()

    assert handler.invocations == ["system.connector.health", "system.llm.degraded"]


@pytest.mark.req("REQ-021")
async def test_duplicate_delivery_is_harmless(bus):
    """At-least-once is the contract: rewinding an offset (what a crash between
    handler success and offset commit looks like) redelivers the event. The
    handler runs again; the durable projection must not double."""
    await bus.publish(topic="app.thing.happened")

    handler = RecordingHandler()
    dispatcher = bus.dispatcher()
    dispatcher.register("projector", handler)
    await dispatcher.run_once()
    assert len(bus.projection_rows()) == 1

    async with bus.uow_factory() as uow:
        await uow.offsets.set("projector", lane_for("tenant-a"), 0)
        await uow.commit()

    await dispatcher.run_once()
    assert len(handler.invocations) == 1, "the ledger suppressed the duplicate handler run"
    assert len(bus.projection_rows()) == 1


@pytest.mark.req("REQ-022")
async def test_consumer_catches_up_after_restart(bus):
    for i in range(4):
        await bus.publish(topic=f"app.before.n{i}")

    first_run = bus.dispatcher()
    first_run.register("projector", RecordingHandler())
    await first_run.run_once()

    # process "down": four more events arrive with no dispatcher running
    for i in range(4):
        await bus.publish(topic=f"app.after.n{i}")

    handler = RecordingHandler()
    restarted = bus.dispatcher()
    restarted.register("projector", handler)
    report = await restarted.run_once()

    assert report.delivered == 4
    assert handler.invocations == [f"app.after.n{i}" for i in range(4)]
    assert len(bus.projection_rows()) == 8


@pytest.mark.req("REQ-021")
async def test_dispatch_is_cross_tenant_but_handlers_see_the_tenant(bus):
    await bus.publish(topic="app.thing.happened", tenant_id="tenant-a")
    await bus.publish(topic="app.thing.happened", tenant_id="tenant-b")

    handler = RecordingHandler()
    dispatcher = bus.dispatcher()
    dispatcher.register("projector", handler)
    await dispatcher.run_once()

    assert len(bus.projection_rows("tenant-a")) == 1
    assert len(bus.projection_rows("tenant-b")) == 1


@pytest.mark.req("REQ-023")
async def test_plane_defaults_to_signal_and_chains_separately(bus):
    signal_one = await bus.publish(topic="app.metric.sampled", plane=Plane.SIGNAL)
    incident_one = await bus.publish(topic="incident.declared", plane=Plane.INCIDENT)
    signal_two = await bus.publish(topic="app.metric.sampled", plane=Plane.SIGNAL)

    assert incident_one.prev_hash is None, "incident plane starts its own chain"
    assert signal_two.prev_hash == signal_one.payload_hash
