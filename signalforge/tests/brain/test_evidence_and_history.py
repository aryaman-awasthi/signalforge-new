"""Progressive evidence budgets and historical similarity (S5-T04, REQ-054,
REQ-052).

Two pure modules, two properties worth defending: retrieval is bounded before it
begins, and history is advisory with a decaying relevance rather than a verdict.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from signalforge.domain.brain.errors import BrainValidationError
from signalforge.domain.brain.evidence import (
    MAX_ITEMS_PER_QUERY,
    BudgetLedger,
    EvidenceBundle,
    EvidenceItem,
    EvidenceQuery,
    QueryBudget,
    SourceKind,
    Stage,
    aggregate_plan,
    broad_plan,
    narrow_plan,
)
from signalforge.domain.brain.similarity import (
    HALF_LIFE_DAYS,
    IncidentFingerprint,
    cosine,
    decay,
    mistake_warnings,
    rank,
    structural_similarity,
)

NOW = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)


# --- budgets ------------------------------------------------------------------


@pytest.mark.req("REQ-054")
def test_a_query_cannot_ask_for_more_than_the_cap():
    """The cap is the cost control, so it is not negotiable per call site."""
    with pytest.raises(BrainValidationError):
        EvidenceQuery(
            source=SourceKind.BRIDGE,
            stage=Stage.NARROW,
            purpose="everything",
            limit=MAX_ITEMS_PER_QUERY + 1,
        )
    with pytest.raises(BrainValidationError):
        EvidenceQuery(source=SourceKind.BRIDGE, stage=Stage.NARROW, purpose="")


@pytest.mark.req("REQ-054")
def test_the_per_hypothesis_budget_stops_a_fourth_hypothesis_costing_four_more():
    ledger = BudgetLedger(QueryBudget(per_hypothesis=2, total=10))
    assert ledger.take("h1") and ledger.take("h1")
    assert not ledger.take("h1")
    # A different hypothesis has its own allowance.
    assert ledger.take("h2")
    assert ledger.spent == 3
    assert ledger.refused == 1


@pytest.mark.req("REQ-054")
def test_the_total_budget_refuses_rather_than_silently_narrowing():
    """"Nothing was found" and "we stopped looking" must be distinguishable."""
    ledger = BudgetLedger(QueryBudget(per_hypothesis=10, total=2))
    assert ledger.take("h1") and ledger.take("h2")
    assert not ledger.take("h3")
    assert ledger.summary() == {"queries": 2, "refused": 1, "limit": 2}


@pytest.mark.req("REQ-054")
def test_the_three_passes_go_from_counts_to_excerpts():
    broad = broad_plan()
    aggregate = aggregate_plan()
    narrow = narrow_plan("hypothesis:pool", subject="pool")
    assert all(query.stage is Stage.BROAD for query in broad)
    assert all(query.stage is Stage.AGGREGATE for query in aggregate)
    assert all(query.stage is Stage.NARROW for query in narrow)
    # Only the narrow pass is per-hypothesis, which is what makes the budget
    # per-hypothesis rather than per-incident.
    assert all(query.hypothesis_key == "" for query in broad + aggregate)
    assert all(query.hypothesis_key == "hypothesis:pool" for query in narrow)


@pytest.mark.req("REQ-054")
def test_the_narrow_plan_is_truncated_to_the_per_hypothesis_budget():
    assert len(narrow_plan("h", budget=QueryBudget(per_hypothesis=2))) == 2
    assert len(narrow_plan("h", budget=QueryBudget(per_hypothesis=99))) == 4


@pytest.mark.req("REQ-054")
def test_an_excerpt_is_capped_and_an_item_must_be_citable():
    item = EvidenceItem(
        ref="bridge:m1", source=SourceKind.BRIDGE, label="sam", excerpt="x" * 5_000
    )
    assert len(item.excerpt) <= 600
    with pytest.raises(BrainValidationError):
        EvidenceItem(ref="", source=SourceKind.BRIDGE, label="sam")


@pytest.mark.req("REQ-054")
def test_merging_dedupes_on_ref_and_keeps_the_richer_excerpt():
    thin = EvidenceBundle(
        incident_id="INC-1",
        stage=Stage.BROAD,
        gathered_at=NOW,
        items=(EvidenceItem(ref="change:1", source=SourceKind.CHANGE, label="deploy"),),
        counts={"changes": 1},
    )
    rich = EvidenceBundle(
        incident_id="INC-1",
        stage=Stage.NARROW,
        gathered_at=NOW,
        items=(
            EvidenceItem(
                ref="change:1",
                source=SourceKind.CHANGE,
                label="deploy",
                excerpt="started 12 minutes before onset",
            ),
        ),
        counts={"changes": 1},
    )
    merged = thin.merged(rich)
    assert merged.refs == ("change:1",)
    assert "12 minutes" in merged.items[0].excerpt
    assert merged.counts["changes"] == 2
    assert merged.stage is Stage.NARROW


@pytest.mark.req("REQ-054")
def test_a_bundle_fingerprints_what_the_model_was_shown():
    """Reproducibility: the refs and the caps, not a copy of the prompt."""
    one = EvidenceBundle(
        incident_id="INC-1",
        stage=Stage.NARROW,
        gathered_at=NOW,
        items=(
            EvidenceItem(ref="a", source=SourceKind.GRAPH, label="a", excerpt="xx"),
            EvidenceItem(ref="b", source=SourceKind.GRAPH, label="b", excerpt="yy"),
        ),
    )
    same = EvidenceBundle(
        incident_id="INC-1",
        stage=Stage.NARROW,
        gathered_at=NOW + timedelta(minutes=5),
        items=tuple(reversed(one.items)),
    )
    different = EvidenceBundle(
        incident_id="INC-1",
        stage=Stage.NARROW,
        gathered_at=NOW,
        items=one.items[:1],
    )
    assert one.fingerprint == same.fingerprint
    assert one.fingerprint != different.fingerprint


# --- similarity ---------------------------------------------------------------


def fingerprint(**overrides) -> IncidentFingerprint:
    base = dict(
        incident_id="INC-9",
        reference="INC-9",
        app_id="checkout",
        title="Checkout latency",
        declared_at=NOW - timedelta(days=10),
        severity="P1",
        signal_families=frozenset({"latency", "errors"}),
        services=frozenset({"pool"}),
        regions=frozenset({"eu"}),
        change_types=frozenset({"deployment"}),
        closed_at=NOW - timedelta(days=10),
        text="checkout latency, pool exhausted",
    )
    base.update(overrides)
    return IncidentFingerprint(**base)


@pytest.mark.req("REQ-052")
def test_structural_similarity_decomposes_into_arguable_components():
    """A similarity nobody can decompose is a similarity nobody should act on."""
    current = fingerprint(incident_id="INC-10", reference="INC-10")
    scored, parts = structural_similarity(current, fingerprint())
    assert scored > 0.9
    assert parts["signal_families"] == 1.0
    assert parts["same_app"] == 1.0
    assert set(parts) == {
        "signal_families",
        "services",
        "regions",
        "change_types",
        "same_app",
        "severity",
    }


@pytest.mark.req("REQ-052")
def test_relevance_decays_with_age_and_never_reaches_zero():
    recent = fingerprint(closed_at=NOW - timedelta(days=1))
    old = fingerprint(closed_at=NOW - timedelta(days=HALF_LIFE_DAYS))
    ancient = fingerprint(closed_at=NOW - timedelta(days=3_000))
    assert decay(recent, now=NOW) > decay(old, now=NOW) > decay(ancient, now=NOW)
    assert abs(decay(old, now=NOW) - 0.5) < 0.01
    assert decay(ancient, now=NOW) >= 0.1


@pytest.mark.req("REQ-052")
def test_rank_orders_by_decayed_relevance_not_raw_similarity():
    current = fingerprint(incident_id="INC-10", reference="INC-10")
    recent = fingerprint(
        incident_id="INC-8", reference="INC-8", closed_at=NOW - timedelta(days=2)
    )
    ancient = fingerprint(
        incident_id="INC-1", reference="INC-1", closed_at=NOW - timedelta(days=900)
    )
    ranked = rank(current, [ancient, recent], now=NOW)
    assert [item.fingerprint.incident_id for item in ranked] == ["INC-8", "INC-1"]
    assert ranked[0].relevance > ranked[1].relevance
    assert ranked[0].caveat().startswith("This is a prior incident")


@pytest.mark.req("REQ-052")
def test_a_degraded_embedding_path_leaves_the_structural_score_at_full_weight():
    """A degraded gateway must not look like a lack of similarity."""
    current = fingerprint(incident_id="INC-10", reference="INC-10")
    prior = fingerprint()
    without = rank(current, [prior], now=NOW)[0]
    with_low_cosine = rank(
        current, [prior], now=NOW, semantic={prior.incident_id: 0.0}
    )[0]
    assert without.semantic is None
    assert without.similarity > with_low_cosine.similarity


@pytest.mark.req("REQ-052")
def test_relevance_movement_is_reported_when_a_previous_score_is_known():
    current = fingerprint(incident_id="INC-10", reference="INC-10")
    prior = fingerprint()
    ranked = rank(
        current, [prior], now=NOW, previous={prior.incident_id: 0.1}
    )[0]
    assert ranked.movement > 0
    assert ranked.previous_relevance == 0.1


@pytest.mark.req("REQ-052")
def test_unrelated_incidents_fall_below_the_floor():
    current = fingerprint(incident_id="INC-10", reference="INC-10")
    unrelated = fingerprint(
        incident_id="INC-2",
        reference="INC-2",
        app_id="billing",
        severity="P3",
        signal_families=frozenset({"saturation"}),
        services=frozenset({"queue"}),
        regions=frozenset({"ap"}),
        change_types=frozenset(),
    )
    assert rank(current, [unrelated], now=NOW) == ()


@pytest.mark.req("REQ-052")
def test_mistake_warnings_come_from_what_was_ruled_out_not_what_was_concluded():
    """"It was the pool last time" invites the room to adopt a conclusion; "we
    ruled the pool out last time" invites it to test one."""
    current = fingerprint(incident_id="INC-10", reference="INC-10")
    prior = fingerprint(
        confirmed_cause="a bad index",
        ruled_out=("the connection pool", "DNS"),
    )
    ranked = rank(current, [prior], now=NOW)
    warnings = mistake_warnings(ranked)
    hints = {warning.hypothesis_hint for warning in warnings}
    assert hints == {"the connection pool", "DNS"}
    assert "a bad index" not in hints
    assert all("guidance from a prior incident" in str(w) for w in warnings)


@pytest.mark.req("REQ-052")
def test_cosine_is_clamped_and_safe_on_mismatched_vectors():
    assert cosine([1.0, 0.0], [1.0, 0.0]) == 1.0
    assert cosine([1.0, 0.0], [0.0, 1.0]) == 0.0
    assert cosine([], [1.0]) == 0.0
    assert cosine([1.0, 2.0], [1.0]) == 0.0
    assert 0.0 <= cosine([1.0, 1.0], [1.0, 1.0]) <= 1.0
