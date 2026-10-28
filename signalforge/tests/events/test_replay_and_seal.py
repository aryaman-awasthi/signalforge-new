"""Replay bounds and filters, the causal chain, the hash chain and seals
(REQ-023, REQ-016, REQ-013)."""

from __future__ import annotations

import sqlite3
import uuid

import pytest

from signalforge.application.events.replay import MAX_REPLAY_LIMIT, ReplayService
from signalforge.application.events.sealing import SealService
from signalforge.application.identity.principal import Principal
from signalforge.contracts.envelope import Plane
from signalforge.domain.events.chain import ChainLink, seal_hash, verify_links
from signalforge.domain.events.errors import (
    EventNotFoundError,
    ReplayRangeError,
    SealConflictError,
)
from signalforge.tests.events.conftest import make_event

pytestmark = pytest.mark.anyio


def principal_for(tenant_id: str = "tenant-a", user_id: str = "user-1") -> Principal:
    return Principal(
        user_id=user_id,
        tenant_id=tenant_id,
        session_id="session-1",
        email="ic@acme.io",
        display_name="IC",
        bindings=(),
    )


# --- chain primitives (pure domain) ------------------------------------------


@pytest.mark.req("REQ-016", "REQ-023")
async def test_verify_links_detects_a_removed_event():
    links = [
        ChainLink(1, "a", "h1", None),
        ChainLink(2, "b", "h2", "h1"),
        ChainLink(3, "c", "h3", "h2"),
    ]
    assert verify_links(links) == ()
    assert verify_links([links[0], links[2]]) == (3,), "the gap is visible"
    assert verify_links(links[1:]) == (2,), "a chain that starts mid-air is visible"
    assert verify_links(links[1:], anchor_hash="h1") == (), "unless a prune anchors it"


@pytest.mark.req("REQ-023")
async def test_seal_hash_binds_position_and_identity():
    links = [ChainLink(1, "a", "h1", None), ChainLink(2, "b", "h2", "h1")]
    baseline = seal_hash(links)
    assert baseline == seal_hash(list(links))
    assert baseline != seal_hash(list(reversed(links)))
    assert baseline != seal_hash(links, prev_seal_hash="earlier-seal")
    assert baseline != seal_hash([links[0], ChainLink(2, "b2", "h2", "h1")])


# --- replay ------------------------------------------------------------------


@pytest.mark.req("REQ-023")
async def test_replay_walks_forward_by_seq_with_paging(bus):
    for i in range(5):
        await bus.publish(topic=f"app.step.n{i}")
    replay = ReplayService(bus.uow_factory)

    page = await replay.read(principal_for(), limit=2)
    assert [e.topic for e in page.events] == ["app.step.n0", "app.step.n1"]
    assert page.has_more and page.next_after_seq == 2

    page = await replay.read(principal_for(), after_seq=page.next_after_seq, limit=2)
    assert [e.topic for e in page.events] == ["app.step.n2", "app.step.n3"]

    last = await replay.read(principal_for(), after_seq=4, limit=2)
    assert [e.topic for e in last.events] == ["app.step.n4"]
    assert not last.has_more


@pytest.mark.req("REQ-023")
async def test_replay_filters_by_until_topics_incident_and_plane(bus):
    await bus.publish(topic="app.metric.sampled")
    await bus.publish(topic="incident.declared", incident_id="inc-1", plane=Plane.INCIDENT)
    await bus.publish(topic="incident.note.added", incident_id="inc-1", plane=Plane.INCIDENT)
    await bus.publish(topic="incident.declared", incident_id="inc-2", plane=Plane.INCIDENT)
    replay = ReplayService(bus.uow_factory)

    until = await replay.read(principal_for(), until=2)
    assert [e.seq for e in until.events] == [1, 2]

    topics = await replay.read(principal_for(), topics=["incident.declared"])
    assert [e.incident_id for e in topics.events] == ["inc-1", "inc-2"]

    wildcard = await replay.read(principal_for(), topics=["incident.*"])
    assert len(wildcard.events) == 3

    scoped = await replay.read(principal_for(), incident_id="inc-1")
    assert [e.topic for e in scoped.events] == ["incident.declared", "incident.note.added"]

    plane = await replay.read(principal_for(), plane=Plane.SIGNAL)
    assert [e.topic for e in plane.events] == ["app.metric.sampled"]


@pytest.mark.req("REQ-023")
async def test_replay_rejects_impossible_ranges(bus):
    replay = ReplayService(bus.uow_factory)
    with pytest.raises(ReplayRangeError):
        await replay.read(principal_for(), after_seq=10, until=5)
    with pytest.raises(ReplayRangeError):
        await replay.read(principal_for(), after_seq=-1)
    with pytest.raises(ReplayRangeError):
        await replay.read(principal_for(), limit=MAX_REPLAY_LIMIT + 1)


@pytest.mark.req("REQ-013", "REQ-023")
async def test_replay_never_crosses_the_tenant_boundary(bus):
    await bus.publish(topic="app.secret.happened", tenant_id="tenant-a")
    await bus.publish(topic="app.other.happened", tenant_id="tenant-b")
    replay = ReplayService(bus.uow_factory)

    mine = await replay.read(principal_for("tenant-a"))
    assert [e.topic for e in mine.events] == ["app.secret.happened"]

    theirs = await replay.read(principal_for("tenant-b"))
    assert [e.topic for e in theirs.events] == ["app.other.happened"]

    stolen = await replay.read(principal_for("tenant-b"), topics=["app.secret.happened"])
    assert stolen.events == ()


@pytest.mark.req("REQ-023")
async def test_replay_returns_the_exact_causal_chain(bus):
    """The acceptance test for replay: an alert causes a declaration, which
    causes two actions; asking about any of them returns that chain and nothing
    from the unrelated traffic recorded in between."""
    alert = await bus.publish(topic="alert.fired", incident_id="inc-1")
    await bus.publish(topic="app.metric.sampled")  # unrelated noise
    declared = await bus.publish(
        topic="incident.declared", incident_id="inc-1", causation_id=alert.event_id
    )
    await bus.publish(topic="app.metric.sampled")  # more noise
    paged = await bus.publish(
        topic="incident.paged", incident_id="inc-1", causation_id=declared.event_id
    )
    briefed = await bus.publish(
        topic="incident.brief.sent", incident_id="inc-1", causation_id=declared.event_id
    )
    await bus.publish(topic="alert.fired", incident_id="inc-2")  # a different chain

    replay = ReplayService(bus.uow_factory)
    chain = await replay.causal_chain(principal_for(), str(paged.event_id))

    assert [e.topic for e in chain] == [
        "alert.fired",
        "incident.declared",
        "incident.paged",
        "incident.brief.sent",
    ]
    assert [e.seq for e in chain] == sorted(e.seq for e in chain)
    assert chain[1].causation_id == alert.event_id
    assert chain[2].causation_id == declared.event_id
    assert chain[3].event_id == briefed.event_id

    with pytest.raises(EventNotFoundError):
        await replay.causal_chain(principal_for(), str(uuid.uuid4()))


# --- sealing -----------------------------------------------------------------


@pytest.mark.req("REQ-023", "REQ-016")
async def test_seal_covers_the_incident_and_verifies(bus):
    for topic in ("incident.declared", "incident.paged", "incident.closed"):
        await bus.publish(topic=topic, incident_id="inc-1", plane=Plane.INCIDENT)
    await bus.publish(topic="app.metric.sampled", plane=Plane.SIGNAL)

    seals = SealService(bus.uow_factory, clock=bus.wall_clock)
    seal = await seals.seal(principal_for(), incident_id="inc-1")

    assert seal.event_count == 3
    assert seal.from_seq == 1 and seal.to_seq == 3
    assert seal.prev_seal_hash is None
    assert (await seals.verify(principal_for(), seal.id)).ok

    listed = await seals.list_seals(principal_for(), incident_id="inc-1")
    assert [s.id for s in listed] == [seal.id]


@pytest.mark.req("REQ-023")
async def test_seals_chain_and_never_overlap(bus):
    await bus.publish(topic="incident.declared", incident_id="inc-1", plane=Plane.INCIDENT)
    seals = SealService(bus.uow_factory, clock=bus.wall_clock)
    first = await seals.seal(principal_for(), incident_id="inc-1")

    with pytest.raises(SealConflictError):
        await seals.seal(principal_for(), incident_id="inc-1")

    await bus.publish(topic="incident.closed", incident_id="inc-1", plane=Plane.INCIDENT)
    second = await seals.seal(principal_for(), incident_id="inc-1")

    assert second.from_seq == 2 and second.to_seq == 2
    assert second.prev_seal_hash == first.seal_hash
    assert (await seals.verify(principal_for(), second.id)).ok


@pytest.mark.req("REQ-016", "REQ-023")
async def test_a_sealed_range_detects_tampering(bus):
    """Editing history after the fact is the attack a seal exists to catch."""
    for topic in ("incident.declared", "incident.note.added", "incident.closed"):
        await bus.publish(
            topic=topic, incident_id="inc-1", plane=Plane.INCIDENT, payload={"t": topic}
        )
    seals = SealService(bus.uow_factory, clock=bus.wall_clock)
    seal = await seals.seal(principal_for(), incident_id="inc-1")
    assert (await seals.verify(principal_for(), seal.id)).ok

    conn = sqlite3.connect(bus.db_path)
    try:
        conn.execute(
            "update events set payload_hash = ? where seq = 2",
            ("0" * 64,),
        )
        conn.commit()
    finally:
        conn.close()

    verdict = await seals.verify(principal_for(), seal.id)
    assert not verdict.ok
    assert 3 in verdict.broken_seqs, "the doctored row breaks its successor's link"
    assert verdict.recomputed_hash != seal.seal_hash


@pytest.mark.req("REQ-016", "REQ-023")
async def test_deleting_a_sealed_event_is_detected(bus):
    for topic in ("incident.declared", "incident.note.added", "incident.closed"):
        await bus.publish(topic=topic, incident_id="inc-1", plane=Plane.INCIDENT)
    seals = SealService(bus.uow_factory, clock=bus.wall_clock)
    seal = await seals.seal(principal_for(), incident_id="inc-1")

    conn = sqlite3.connect(bus.db_path)
    try:
        conn.execute("delete from events where seq = 2")
        conn.commit()
    finally:
        conn.close()

    verdict = await seals.verify(principal_for(), seal.id)
    assert not verdict.ok
    assert verdict.event_count == 2 and seal.event_count == 3


@pytest.mark.req("REQ-013", "REQ-023")
async def test_seals_are_tenant_scoped(bus):
    await bus.publish(topic="incident.declared", tenant_id="tenant-a", plane=Plane.INCIDENT)
    seals = SealService(bus.uow_factory, clock=bus.wall_clock)
    seal = await seals.seal(principal_for("tenant-a"))

    assert await seals.list_seals(principal_for("tenant-b")) == ()
    from signalforge.domain.identity.errors import NotFoundError

    with pytest.raises(NotFoundError):
        await seals.verify(principal_for("tenant-b"), seal.id)
