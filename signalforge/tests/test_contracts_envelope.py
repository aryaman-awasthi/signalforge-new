"""S0-T02: event envelope contract validation."""

import uuid
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from signalforge.contracts.envelope import (
    Actor,
    ActorType,
    Authority,
    EventEnvelope,
    Plane,
    Redaction,
    Source,
    canonical_payload_hash,
    utc_now,
)

ACTOR = Actor(type=ActorType.SYSTEM, id="detector")
SOURCE = Source(system="prometheus-adapter", authority=Authority.OBSERVED)


def make_envelope(**overrides):
    base = dict(
        topic="anomaly.detected",
        tenant_id="t1",
        app_id="app-1",
        occurred_at=utc_now(),
        actor=ACTOR,
        source=SOURCE,
    )
    base.update(overrides)
    return EventEnvelope(**base)


@pytest.mark.req("REQ-001")
def test_minimal_envelope_valid():
    env = make_envelope()
    assert env.event_id is not None
    assert env.plane is Plane.SIGNAL
    assert env.recorded_at is None and env.seq is None  # store-assigned


@pytest.mark.req("REQ-001")
def test_occurred_at_and_recorded_at_are_distinct_fields():
    drill_clock = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    env = make_envelope(occurred_at=drill_clock, recorded_at=utc_now())
    assert env.occurred_at == drill_clock
    assert env.recorded_at != env.occurred_at


@pytest.mark.req("REQ-001")
def test_naive_timestamps_rejected():
    with pytest.raises(ValidationError):
        make_envelope(occurred_at=datetime(2026, 1, 1, 12, 0))


@pytest.mark.req("REQ-001")
def test_topic_must_be_namespaced():
    for bad in ["", "flat", "Bad.Case", "a..b"]:
        with pytest.raises(ValidationError):
            make_envelope(topic=bad)


@pytest.mark.req("REQ-001")
def test_confidence_bounds():
    assert make_envelope(confidence=0.7).confidence == 0.7
    with pytest.raises(ValidationError):
        make_envelope(confidence=1.5)


@pytest.mark.req("REQ-001")
def test_partition_key_prefers_incident_then_app_then_tenant():
    assert make_envelope(incident_id="inc-9").partition_key == "inc-9"
    assert make_envelope().partition_key == "app-1"
    assert make_envelope(app_id=None).partition_key == "t1"


@pytest.mark.req("REQ-001")
def test_causation_correlation_supersedes_and_idempotency():
    parent = uuid.uuid4()
    superseded = uuid.uuid4()
    env = make_envelope(
        correlation_id="corr-1",
        causation_id=parent,
        supersedes=superseded,
        idempotency_key="key-1",
    )
    assert env.causation_id == parent
    assert env.supersedes == superseded


@pytest.mark.req("REQ-001")
def test_source_carries_authority_freshness_simulated():
    src = Source(
        system="teams-sim",
        authority=Authority.INFERRED,
        freshness_s=2.5,
        simulated=True,
    )
    env = make_envelope(source=src)
    assert env.source.simulated is True
    assert env.source.freshness_s == 2.5
    with pytest.raises(ValidationError):
        Source(system="x", freshness_s=-1)


@pytest.mark.req("REQ-001")
def test_redaction_block():
    env = make_envelope(
        redaction=Redaction(applied=True, fields=("payload.card_number",), policy_id="pii-v1")
    )
    assert env.redaction.applied and "payload.card_number" in env.redaction.fields


@pytest.mark.req("REQ-001")
def test_payload_hash_is_canonical_and_reproducible():
    h1 = canonical_payload_hash({"b": 1, "a": [1, 2]})
    h2 = canonical_payload_hash({"a": [1, 2], "b": 1})
    assert h1 == h2 and len(h1) == 64
    assert canonical_payload_hash({"a": [2, 1]}) != h1
    env = make_envelope(payload_hash=h1, prev_hash=h2)
    assert env.payload_hash == h1
    with pytest.raises(ValidationError):
        make_envelope(payload_hash="not-a-hash")
