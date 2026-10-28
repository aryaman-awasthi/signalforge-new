"""Virtual clock (REQ-024).

A drill compresses hours of an incident into minutes. occurred_at has to follow
that compressed timeline, because that is when things happened in the world the
drill is simulating; recorded_at must stay wall time, because that is when this
machine actually wrote the row. Confusing the two makes a drill indistinguishable
from a real incident in the audit trail.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from signalforge.contracts.envelope import Plane
from signalforge.infrastructure.clock import SwitchableClock, VirtualClock
from signalforge.tests.events.conftest import SCENARIO_START, WALL_START, make_event
from signalforge.tests.helpers import FakeClock

pytestmark = pytest.mark.anyio


@pytest.mark.req("REQ-024")
async def test_virtual_clock_runs_at_a_chosen_speed():
    wall = FakeClock(WALL_START)
    virtual = VirtualClock(wall, start_at=SCENARIO_START, speed=60.0)

    assert virtual.now() == SCENARIO_START
    wall.advance(seconds=30)
    assert virtual.now() == SCENARIO_START + timedelta(minutes=30)
    assert wall.now() == WALL_START + timedelta(seconds=30)


@pytest.mark.req("REQ-024")
async def test_virtual_clock_pauses_steps_and_resumes():
    wall = FakeClock(WALL_START)
    virtual = VirtualClock(wall, start_at=SCENARIO_START, speed=10.0)

    virtual.pause()
    wall.advance(minutes=5)
    assert virtual.now() == SCENARIO_START, "a paused drill clock does not drift"

    virtual.step(minutes=2)
    assert virtual.now() == SCENARIO_START + timedelta(minutes=2)

    virtual.resume()
    wall.advance(seconds=6)
    assert virtual.now() == SCENARIO_START + timedelta(minutes=3)

    virtual.set_speed(1.0)
    wall.advance(seconds=10)
    assert virtual.now() == SCENARIO_START + timedelta(minutes=3, seconds=10)


@pytest.mark.req("REQ-024")
async def test_virtual_clock_rejects_impossible_speeds():
    wall = FakeClock(WALL_START)
    with pytest.raises(ValueError):
        VirtualClock(wall, start_at=SCENARIO_START, speed=0)
    with pytest.raises(ValueError):
        VirtualClock(wall, start_at=SCENARIO_START, speed=-1)
    with pytest.raises(ValueError):
        VirtualClock(wall, start_at=datetime(2026, 1, 1))  # naive


@pytest.mark.req("REQ-024")
async def test_switchable_clock_reports_its_source_and_returns_to_wall():
    wall = FakeClock(WALL_START)
    clock = SwitchableClock(wall)

    assert clock.now() == WALL_START
    assert clock.source == "wall" and not clock.is_virtual

    virtual = VirtualClock(wall, start_at=SCENARIO_START, speed=2.0)
    clock.use(virtual)
    assert clock.is_virtual and clock.source == "virtual"
    assert clock.now() == SCENARIO_START
    assert clock.speed == 2.0

    clock.reset()
    assert clock.now() == WALL_START and clock.source == "wall"
    assert clock.speed == 1.0


@pytest.mark.req("REQ-024", "REQ-020")
async def test_occurred_at_follows_the_drill_while_recorded_at_stays_wall(bus):
    """The event the drill emits is dated in drill time and recorded in real
    time, and the store's ordering is by the real one."""
    virtual = VirtualClock(bus.wall_clock, start_at=SCENARIO_START, speed=120.0)

    first = await bus.publish(
        make_event(topic="app.metric.sampled", occurred_at=virtual.now())
    )
    bus.wall_clock.advance(seconds=30)
    second = await bus.publish(
        make_event(topic="app.metric.sampled", occurred_at=virtual.now())
    )

    assert first.occurred_at == SCENARIO_START
    assert second.occurred_at == SCENARIO_START + timedelta(hours=1)
    assert first.recorded_at == WALL_START
    assert second.recorded_at == WALL_START + timedelta(seconds=30)
    assert second.seq > first.seq, "recording order is wall order, not drill order"

    async with bus.uow_factory() as uow:
        replayed = await uow.events.read(tenant_id="tenant-a")
    assert [e.occurred_at for e in replayed] == [first.occurred_at, second.occurred_at]
    assert [e.recorded_at for e in replayed] == [first.recorded_at, second.recorded_at]


@pytest.mark.req("REQ-024", "REQ-023")
async def test_retention_uses_wall_time_not_the_drill_clock(bus):
    """A drill that fast-forwards a week must not make the platform delete a
    week of real signal data."""
    from signalforge.application.events.retention import RetentionService
    from signalforge.domain.events.retention import RetentionPolicy

    virtual = VirtualClock(bus.wall_clock, start_at=SCENARIO_START, speed=100_000.0)
    for i in range(3):
        await bus.publish(
            make_event(topic=f"app.metric.n{i}", occurred_at=virtual.now(), plane=Plane.SIGNAL)
        )
    bus.wall_clock.advance(seconds=60)  # ~70 virtual days, one real minute

    assert virtual.now() > SCENARIO_START + timedelta(days=60)
    retention = RetentionService(
        bus.uow_factory, bus.bus, clock=bus.wall_clock, policy=RetentionPolicy(7)
    )
    assert (await retention.sweep()).pruned == 0

    async with bus.uow_factory() as uow:
        assert len(await uow.events.read(tenant_id="tenant-a", limit=50)) == 3
