"""The versioned incident graph, as a value object (S5-T01, REQ-050).

Pure domain tests: no store, no clock, no model. What they pin down is the
rule the whole sprint rests on - nothing is overwritten, and every belief can be
replayed at the version it was believed.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from signalforge.contracts.envelope import ActorType, Authority
from signalforge.domain.brain.errors import BrainValidationError
from signalforge.domain.brain.nodes import (
    ConfirmationState,
    GraphEdge,
    GraphNode,
    NodeStatus,
    NodeType,
    Provenance,
    Relation,
    SituationSnapshot,
    by_type,
    current_nodes,
    derived_key,
    history_of,
)

NOW = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)


def node(**overrides) -> GraphNode:
    base = dict(
        id="n1",
        tenant_id="t1",
        incident_id="INC-1",
        node_type=NodeType.HYPOTHESIS,
        derived_key=derived_key(NodeType.HYPOTHESIS, "pool"),
        label="The connection pool is exhausted",
        created_at=NOW,
        provenance=Provenance(source="hypothesis-agent"),
    )
    base.update(overrides)
    return GraphNode(**base)


@pytest.mark.req("REQ-050")
def test_a_node_names_its_incident_its_tenant_and_its_source():
    with pytest.raises(BrainValidationError):
        node(tenant_id="")
    with pytest.raises(BrainValidationError):
        node(incident_id="")
    with pytest.raises(BrainValidationError):
        Provenance(source="")


@pytest.mark.req("REQ-050")
def test_confidence_outside_zero_to_one_is_refused():
    """A score that is not a probability is not a score, and one that leaked to
    a screen would be read as one."""
    with pytest.raises(BrainValidationError):
        node(confidence=1.4)
    with pytest.raises(BrainValidationError):
        node(confidence=-0.1)
    assert node(confidence=0.5).confidence == 0.5


@pytest.mark.req("REQ-050")
def test_a_new_version_supersedes_rather_than_overwrites():
    first = node(confidence=0.65)
    second = first.next_version(node_id="n2", at=NOW + timedelta(minutes=5), confidence=0.32)
    retired = first.as_superseded(by=second.id, at=NOW + timedelta(minutes=5))

    assert second.version == 2
    assert second.supersedes == first.id
    assert second.confidence == 0.32
    # The old row is still readable, still says what it said, and now says what
    # replaced it. This is what makes an incident replayable.
    assert retired.status is NodeStatus.SUPERSEDED
    assert retired.confidence == 0.65
    assert retired.superseded_by == second.id
    assert not retired.current


@pytest.mark.req("REQ-050")
def test_a_retracted_belief_is_kept_and_excluded_from_the_current_view():
    ruled_out = node().as_retracted(at=NOW, reason="the pool was measured as idle")
    assert ruled_out.status is NodeStatus.RETRACTED
    assert not ruled_out.current
    assert "idle" in ruled_out.retired_reason
    assert current_nodes([ruled_out]) == ()


@pytest.mark.req("REQ-050")
def test_an_unchanged_observation_is_not_a_new_version():
    """The idempotency guard: an agent running every thirty seconds against an
    unchanged world must write nothing, or the history becomes a heartbeat."""
    held = node(confidence=0.6, content="two families agree")
    assert not held.differs_from(
        label=held.label,
        content="two families agree",
        confidence=0.6000001,
        confirmation=None,
    )
    assert held.differs_from(
        label=held.label, content="two families agree", confidence=0.7, confirmation=None
    )
    assert held.differs_from(
        label="something else", content="two families agree", confidence=0.6,
        confirmation=None,
    )


@pytest.mark.req("REQ-050")
def test_derived_keys_normalise_and_never_collide_by_truncation():
    assert derived_key(NodeType.FACT, "Connection Pool") == derived_key(
        NodeType.FACT, "connection-pool"
    )
    long_one = derived_key(NodeType.FACT, "x" * 200)
    long_two = derived_key(NodeType.FACT, "x" * 199 + "y")
    # Hashed rather than truncated: truncation makes two different sources one
    # key, which is the single failure this key exists to prevent.
    assert long_one != long_two
    with pytest.raises(BrainValidationError):
        derived_key(NodeType.FACT, "   ")


@pytest.mark.req("REQ-050")
def test_confirmed_and_predicted_are_distinct_values_on_the_node():
    confirmed = node(node_type=NodeType.IMPACT, confirmation=ConfirmationState.CONFIRMED)
    predicted = node(node_type=NodeType.IMPACT, confirmation=ConfirmationState.PREDICTED)
    assert confirmed.confirmation.observed
    assert not predicted.confirmation.observed
    assert confirmed.confirmation is not predicted.confirmation


@pytest.mark.req("REQ-050")
def test_an_edge_cannot_relate_a_node_to_itself():
    with pytest.raises(BrainValidationError):
        GraphEdge(
            id="e1",
            tenant_id="t1",
            incident_id="INC-1",
            source_id="n1",
            target_id="n1",
            relation=Relation.SUPPORTS,
            created_at=NOW,
        )


@pytest.mark.req("REQ-050")
def test_history_of_reads_a_belief_in_version_order():
    first = node(id="a", confidence=0.4)
    second = first.next_version(node_id="b", at=NOW, confidence=0.5)
    third = second.next_version(node_id="c", at=NOW, confidence=0.2)
    versions = history_of([third, first, second], first.derived_key)
    assert [item.version for item in versions] == [1, 2, 3]
    assert [item.confidence for item in versions] == [0.4, 0.5, 0.2]


@pytest.mark.req("REQ-050")
def test_by_type_filters_and_respects_status():
    fact = node(node_type=NodeType.FACT, derived_key=derived_key(NodeType.FACT, "a"))
    stale = node(
        node_type=NodeType.FACT, derived_key=derived_key(NodeType.FACT, "b")
    ).as_retracted(at=NOW, reason="wrong")
    assert by_type([fact, stale], NodeType.FACT) == (fact,)
    assert len(by_type([fact, stale], NodeType.FACT, current_only=False)) == 2


@pytest.mark.req("REQ-050")
def test_provenance_records_the_model_and_never_claims_observation():
    provenance = Provenance(
        source="conversation-agent",
        actor_type=ActorType.AGENT,
        authority=Authority.INFERRED,
        model="genailab-maas-gpt-5.4-mini",
        model_class="general",
        prompt_purpose="statement-classification",
    )
    assert provenance.model_written
    assert provenance.authority is Authority.INFERRED
    assert provenance.as_dict()["model"] == "genailab-maas-gpt-5.4-mini"

    degraded = Provenance(source="x", model="m", degraded=True)
    assert not degraded.model_written


@pytest.mark.req("REQ-050")
def test_a_situation_snapshot_fingerprints_what_was_current():
    one = SituationSnapshot(
        id="s1",
        tenant_id="t1",
        incident_id="INC-1",
        taken_at=NOW,
        reason="declared",
        node_ids=("a", "b"),
        hypotheses={"h1": 0.65},
    )
    same = SituationSnapshot(
        id="s2",
        tenant_id="t1",
        incident_id="INC-1",
        taken_at=NOW + timedelta(minutes=1),
        reason="a later pass",
        node_ids=("b", "a"),
        hypotheses={"h1": 0.6501},
    )
    moved = SituationSnapshot(
        id="s3",
        tenant_id="t1",
        incident_id="INC-1",
        taken_at=NOW,
        reason="after evidence",
        node_ids=("a", "b"),
        hypotheses={"h1": 0.32},
    )
    # Order-independent, and insensitive to floating-point noise, so "nothing has
    # changed since you last looked" is answerable without diffing the graph.
    assert one.fingerprint == same.fingerprint
    assert one.fingerprint != moved.fingerprint
