"""Retries, dead letters, requeue and head-of-line protection (REQ-022)."""

from __future__ import annotations

import pytest

from signalforge.application.events.dispatcher import DEAD_LETTER_TOPIC, REQUEUED_TOPIC
from signalforge.domain.events.dispatch import (
    MAX_ATTEMPTS_CAP,
    DeliveryOutcome,
    RetryPolicy,
    topic_matches,
)
from signalforge.domain.events.entities import DeadLetterState
from signalforge.domain.events.errors import (
    DeadLetterNotFoundError,
    UnknownConsumerGroupError,
)
from signalforge.tests.events.conftest import RecordingHandler

pytestmark = pytest.mark.anyio

NO_WAIT = RetryPolicy(max_attempts=3, base_backoff_s=0.0, max_backoff_s=0.0)


class FlakyHandler(RecordingHandler):
    """Fails the first `failures` invocations, then behaves."""

    def __init__(self, failures: int, name: str = "flaky"):
        super().__init__(name)
        self.remaining_failures = failures

    async def handle(self, envelope, uow):
        if self.remaining_failures > 0:
            self.remaining_failures -= 1
            self.invocations.append(envelope.topic)
            raise RuntimeError("transient downstream failure")
        await super().handle(envelope, uow)


# --- policy (pure domain) ----------------------------------------------------


@pytest.mark.req("REQ-022")
async def test_retry_policy_is_bounded_and_backs_off():
    policy = RetryPolicy(max_attempts=3, base_backoff_s=0.1, max_backoff_s=0.3)
    assert policy.decide(1) is DeliveryOutcome.RETRY
    assert policy.decide(2) is DeliveryOutcome.RETRY
    assert policy.decide(3) is DeliveryOutcome.DEAD_LETTER
    assert [policy.backoff_s(n) for n in (0, 1, 2, 3, 9)] == [0.0, 0.1, 0.2, 0.3, 0.3]

    with pytest.raises(ValueError):
        RetryPolicy(max_attempts=MAX_ATTEMPTS_CAP + 1)
    with pytest.raises(ValueError):
        RetryPolicy(max_attempts=0)


@pytest.mark.req("REQ-021")
async def test_topic_patterns():
    assert topic_matches("system.llm.degraded", None)
    assert topic_matches("system.llm.degraded", ("system.*",))
    assert topic_matches("system.llm.degraded", ("system.llm.degraded",))
    assert not topic_matches("app.thing.happened", ("system.*",))
    assert not topic_matches("systematic.thing", ("system.*",))


# --- dead lettering ----------------------------------------------------------


@pytest.mark.req("REQ-022")
async def test_poison_event_is_retried_then_dead_lettered(bus):
    event = await bus.publish(topic="app.poison.dropped")

    handler = RecordingHandler(fail_topics=("app.poison.dropped",))
    dispatcher = bus.dispatcher(retry_policy=NO_WAIT)
    dispatcher.register("projector", handler)

    report = await dispatcher.run_once()

    assert report.dead_lettered == 1
    assert report.delivered == 0
    assert len(handler.invocations) == 3, "max_attempts attempts, then parked"

    async with bus.uow_factory() as uow:
        dead = list(await uow.dead_letters.list(tenant_id="tenant-a"))
    assert len(dead) == 1
    assert dead[0].state is DeadLetterState.DEAD
    assert dead[0].event_id == str(event.event_id)
    assert dead[0].attempts == 3
    assert "poisoned handler" in dead[0].error

    # the failure is auditable, not just visible in a table
    assert DEAD_LETTER_TOPIC in _audit_topics(bus, "tenant-a")


@pytest.mark.req("REQ-022")
async def test_a_dead_letter_does_not_block_its_lane_or_the_others(bus):
    """Head-of-line blocking would take the whole platform down with one bad
    handler, so a parked event must not stop the events behind it."""
    await bus.publish(topic="app.good.first", incident_id="inc-a")
    await bus.publish(topic="app.poison.dropped", incident_id="inc-a")
    await bus.publish(topic="app.good.second", incident_id="inc-a")
    await bus.publish(topic="app.good.other", incident_id="inc-b")

    handler = RecordingHandler(fail_topics=("app.poison.dropped",))
    dispatcher = bus.dispatcher(retry_policy=NO_WAIT)
    dispatcher.register("projector", handler)
    report = await dispatcher.run_once()

    assert report.delivered == 3 and report.dead_lettered == 1
    delivered = [r["source_topic"] for r in bus.projection_rows()]
    assert delivered == ["app.good.first", "app.good.second", "app.good.other"]

    assert (await dispatcher.run_once()).delivered == 0, "no re-scan of parked events"


@pytest.mark.req("REQ-022")
async def test_transient_failure_recovers_without_a_dead_letter(bus):
    await bus.publish(topic="app.thing.happened")

    handler = FlakyHandler(failures=2)
    dispatcher = bus.dispatcher(retry_policy=NO_WAIT)
    dispatcher.register("projector", handler)
    report = await dispatcher.run_once()

    assert report.delivered == 1 and report.dead_lettered == 0 and report.retried == 2
    assert len(bus.projection_rows()) == 1


# --- requeue -----------------------------------------------------------------


@pytest.mark.req("REQ-022")
async def test_requeue_redelivers_and_resolves(bus):
    await bus.publish(topic="app.poison.dropped")

    handler = RecordingHandler(fail_topics=("app.poison.dropped",))
    dispatcher = bus.dispatcher(retry_policy=NO_WAIT)
    dispatcher.register("projector", handler)
    await dispatcher.run_once()

    async with bus.uow_factory() as uow:
        dead = (await uow.dead_letters.list(tenant_id="tenant-a"))[0]

    # the operator fixes the handler, then asks for redelivery
    handler.fail_topics = ()
    requeued = await dispatcher.requeue(
        dead.id, tenant_id="tenant-a", requested_by="operator-1"
    )
    assert requeued.state is DeadLetterState.REQUEUED
    assert REQUEUED_TOPIC in _audit_topics(bus, "tenant-a")

    report = await dispatcher.run_once()
    assert report.redelivered == 1
    assert len(bus.projection_rows()) == 1

    async with bus.uow_factory() as uow:
        after = await uow.dead_letters.get(dead.id, tenant_id="tenant-a")
    assert after.state is DeadLetterState.RESOLVED

    assert (await dispatcher.run_once()).redelivered == 0, "resolved letters stay resolved"


@pytest.mark.req("REQ-022")
async def test_requeue_of_a_still_broken_handler_returns_to_dead(bus):
    await bus.publish(topic="app.poison.dropped")

    handler = RecordingHandler(fail_topics=("app.poison.dropped",))
    dispatcher = bus.dispatcher(retry_policy=NO_WAIT)
    dispatcher.register("projector", handler)
    await dispatcher.run_once()

    async with bus.uow_factory() as uow:
        dead = (await uow.dead_letters.list(tenant_id="tenant-a"))[0]
    await dispatcher.requeue(dead.id, tenant_id="tenant-a", requested_by="operator-1")

    report = await dispatcher.run_once()
    assert report.redelivery_failed == 1

    async with bus.uow_factory() as uow:
        after = await uow.dead_letters.get(dead.id, tenant_id="tenant-a")
    assert after.state is DeadLetterState.DEAD
    assert after.attempts == 4
    assert len(bus.projection_rows()) == 0


@pytest.mark.req("REQ-022", "REQ-013")
async def test_requeue_is_tenant_scoped_and_group_checked(bus):
    await bus.publish(topic="app.poison.dropped", tenant_id="tenant-a")

    handler = RecordingHandler(fail_topics=("app.poison.dropped",))
    dispatcher = bus.dispatcher(retry_policy=NO_WAIT)
    dispatcher.register("projector", handler)
    await dispatcher.run_once()

    async with bus.uow_factory() as uow:
        dead = (await uow.dead_letters.list(tenant_id="tenant-a"))[0]

    with pytest.raises(DeadLetterNotFoundError):
        await dispatcher.requeue(dead.id, tenant_id="tenant-b", requested_by="attacker")

    other = bus.dispatcher(retry_policy=NO_WAIT)
    other.register("some-other-group", RecordingHandler())
    with pytest.raises(UnknownConsumerGroupError):
        await other.requeue(dead.id, tenant_id="tenant-a", requested_by="operator-1")


def _audit_topics(bus, tenant_id: str) -> list[str]:
    import sqlite3

    conn = sqlite3.connect(bus.db_path)
    try:
        return [
            row[0]
            for row in conn.execute(
                "select topic from audit_log where tenant_id = ? order by seq",
                (tenant_id,),
            ).fetchall()
        ]
    finally:
        conn.close()
