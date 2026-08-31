"""Two retention planes: the signal plane ages out on a configured window, the
incident plane is kept and sealed (REQ-023).

Retention is the one place in the platform allowed to remove a recorded event,
so its rules are written failing-test-first: what it may never touch, what it
leaves behind so the chain still verifies, and that running it twice changes
nothing.
"""

from __future__ import annotations

import sqlite3

import pytest

from signalforge.application.events.retention import RetentionService
from signalforge.application.events.sealing import SealService
from signalforge.contracts.envelope import Plane
from signalforge.domain.events.chain import verify_links
from signalforge.domain.events.retention import (
    SIGNAL_RETENTION_MAX_DAYS,
    SIGNAL_RETENTION_MIN_DAYS,
    RetentionPolicy,
)
from signalforge.tests.events.conftest import WALL_START
from signalforge.tests.events.test_replay_and_seal import principal_for

pytestmark = pytest.mark.anyio


# --- policy (pure domain) ----------------------------------------------------


@pytest.mark.req("REQ-023")
async def test_retention_policy_bounds_and_cutoff():
    policy = RetentionPolicy(signal_retention_days=7)
    assert policy.cutoff(WALL_START) == WALL_START.replace(day=25, month=8)
    assert policy.applies_to(Plane.SIGNAL)
    assert not policy.applies_to(Plane.INCIDENT), "the kept plane is never pruned"

    with pytest.raises(ValueError):
        RetentionPolicy(signal_retention_days=SIGNAL_RETENTION_MIN_DAYS - 1)
    with pytest.raises(ValueError):
        RetentionPolicy(signal_retention_days=SIGNAL_RETENTION_MAX_DAYS + 1)


# --- the sweep ---------------------------------------------------------------


async def _seed_two_planes(bus):
    """Four old signal events and three old incident events, then two fresh
    signal events. Ages are set through recorded_at, which is wall time."""
    old = []
    bus.wall_clock.current = WALL_START
    for i in range(4):
        old.append(await bus.publish(topic=f"app.metric.n{i}", plane=Plane.SIGNAL))
    for i in range(3):
        old.append(
            await bus.publish(
                topic=f"incident.step.n{i}", incident_id="inc-1", plane=Plane.INCIDENT
            )
        )
    bus.wall_clock.advance(days=10)
    fresh = [
        await bus.publish(topic=f"app.metric.fresh{i}", plane=Plane.SIGNAL)
        for i in range(2)
    ]
    return old, fresh


@pytest.mark.req("REQ-023")
async def test_signal_plane_ages_out_and_the_incident_plane_is_kept(bus):
    old, fresh = await _seed_two_planes(bus)
    retention = RetentionService(
        bus.uow_factory, bus.bus, clock=bus.wall_clock, policy=RetentionPolicy(7)
    )

    result = await retention.sweep()

    assert result.pruned == 4, "only the four aged signal events"
    async with bus.uow_factory() as uow:
        remaining = await uow.events.read(tenant_id="tenant-a", limit=100)
    topics = [e.topic for e in remaining]
    assert [t for t in topics if t.startswith("incident.")] == [
        "incident.step.n0",
        "incident.step.n1",
        "incident.step.n2",
    ], "the incident plane is kept however old it is"
    assert [t for t in topics if t.startswith("app.")] == [
        "app.metric.fresh0",
        "app.metric.fresh1",
    ]


@pytest.mark.req("REQ-023", "REQ-016")
async def test_pruning_leaves_an_anchor_so_the_chain_still_verifies(bus):
    await _seed_two_planes(bus)
    retention = RetentionService(
        bus.uow_factory, bus.bus, clock=bus.wall_clock, policy=RetentionPolicy(7)
    )
    async with bus.uow_factory() as uow:
        before = list(
            await uow.events.chain_links(tenant_id="tenant-a", plane=Plane.SIGNAL)
        )
    pruned_tip = before[3].payload_hash

    await retention.sweep()

    async with bus.uow_factory() as uow:
        after = list(
            await uow.events.chain_links(tenant_id="tenant-a", plane=Plane.SIGNAL)
        )
        watermark = await uow.retention.get("tenant-a", Plane.SIGNAL)

    assert watermark is not None
    assert watermark.pruned_through_seq == 4 and watermark.pruned_count == 4
    assert watermark.anchor_hash == pruned_tip
    assert verify_links(after, anchor_hash=watermark.anchor_hash) == ()
    assert verify_links(after) == (after[0].seq,), (
        "without the anchor the surviving chain looks like it starts mid-air, "
        "which is exactly how a deletion is told apart from a prune"
    )


@pytest.mark.req("REQ-023")
async def test_sweep_is_idempotent_and_records_what_it_did(bus):
    await _seed_two_planes(bus)
    retention = RetentionService(
        bus.uow_factory, bus.bus, clock=bus.wall_clock, policy=RetentionPolicy(7)
    )

    first = await retention.sweep()
    second = await retention.sweep()

    assert first.pruned == 4 and second.pruned == 0

    async with bus.uow_factory() as uow:
        kept = await uow.events.read(
            tenant_id="tenant-a", plane=Plane.INCIDENT, limit=100
        )
    pruned_events = [e for e in kept if e.topic == "system.retention.pruned"]
    assert len(pruned_events) == 1, "one durable record of the one prune that happened"
    assert pruned_events[0].payload["pruned"] == 4
    assert pruned_events[0].payload["plane"] == "signal"

    conn = sqlite3.connect(bus.db_path)
    try:
        topics = [
            r[0]
            for r in conn.execute("select topic from audit_log order by seq").fetchall()
        ]
    finally:
        conn.close()
    assert "system.retention.pruned" in topics


@pytest.mark.req("REQ-023")
async def test_a_sealed_signal_range_is_never_pruned(bus):
    """Sealing is a promise that a range will still verify. Retention must lose
    to that promise, not the other way round."""
    bus.wall_clock.current = WALL_START
    for i in range(4):
        await bus.publish(topic=f"app.metric.n{i}", plane=Plane.SIGNAL)
    seals = SealService(bus.uow_factory, clock=bus.wall_clock)
    seal = await seals.seal(principal_for(), plane=Plane.SIGNAL)
    bus.wall_clock.advance(days=10)

    retention = RetentionService(
        bus.uow_factory, bus.bus, clock=bus.wall_clock, policy=RetentionPolicy(7)
    )
    result = await retention.sweep()

    assert result.pruned == 0
    assert result.protected_by_seal == 4
    assert (await seals.verify(principal_for(), seal.id)).ok


@pytest.mark.req("REQ-023", "REQ-013")
async def test_retention_sweeps_each_tenant_on_its_own(bus):
    bus.wall_clock.current = WALL_START
    await bus.publish(topic="app.metric.n0", tenant_id="tenant-a")
    await bus.publish(topic="app.metric.n0", tenant_id="tenant-b")
    bus.wall_clock.advance(days=10)
    await bus.publish(topic="app.metric.fresh", tenant_id="tenant-b")

    retention = RetentionService(
        bus.uow_factory, bus.bus, clock=bus.wall_clock, policy=RetentionPolicy(7)
    )
    result = await retention.sweep()

    assert result.pruned == 2
    assert sorted(result.tenants) == ["tenant-a", "tenant-b"]
    async with bus.uow_factory() as uow:
        assert [e.topic for e in await uow.events.read(tenant_id="tenant-b", limit=50)][
            :1
        ] == ["app.metric.fresh"]
        assert (await uow.retention.get("tenant-a", Plane.SIGNAL)).pruned_count == 1
        assert (await uow.retention.get("tenant-b", Plane.SIGNAL)).pruned_count == 1
