"""Deterministic hypothesis confidence (S5-T03, REQ-053).

These tests are the contract behind the sentence "a model never produces a
number". They pin the arithmetic, its monotonicity, its order-independence and
the authority scaling that stops a chain of model inferences from manufacturing
certainty.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from signalforge.contracts.envelope import Authority
from signalforge.domain.brain.confidence import (
    MAX_CONFIDENCE,
    MIN_CONFIDENCE,
    PRIOR,
    EvidenceKind,
    Stance,
    WeightedEvidence,
    discriminating_need,
    entry_for,
    score,
)
from signalforge.domain.brain.errors import BrainValidationError

NOW = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)


def ev(node_id, kind, stance=Stance.SUPPORTS, authority=Authority.OBSERVED, strength=1.0):
    return WeightedEvidence(
        node_id=node_id,
        kind=kind,
        stance=stance,
        authority=authority,
        strength=strength,
        label=node_id,
    )


@pytest.mark.req("REQ-053")
def test_a_hypothesis_with_no_evidence_sits_at_the_prior_not_at_zero():
    """Zero would read as "ruled out". A fresh hypothesis has not been ruled
    out; it is unevidenced, and the screen has to be able to say so."""
    assert score(()) == PRIOR
    assert 0.0 < PRIOR < 0.5


@pytest.mark.req("REQ-053")
def test_supporting_evidence_raises_and_refuting_evidence_lowers():
    supporting = score((ev("a", EvidenceKind.TELEMETRY_ANOMALY),))
    refuting = score((ev("a", EvidenceKind.TELEMETRY_ANOMALY, Stance.REFUTES),))
    assert supporting > PRIOR > refuting


@pytest.mark.req("REQ-053")
def test_evidence_against_can_pull_a_confident_hypothesis_back_down():
    """The acceptance case: a hypothesis that rose must be able to fall when
    something argues the other way."""
    up = (
        ev("a", EvidenceKind.CORROBORATION),
        ev("b", EvidenceKind.SLO_BURN),
    )
    risen = score(up)
    fallen = score(up + (ev("c", EvidenceKind.DIAGNOSTIC_RESULT, Stance.REFUTES),))
    assert risen > 0.6
    assert fallen < risen


@pytest.mark.req("REQ-053")
def test_the_score_is_order_independent():
    """Two agents merging evidence in different orders after a redelivery must
    land on the same number, or the history fills with deltas nobody caused."""
    items = [
        ev("a", EvidenceKind.TELEMETRY_ANOMALY),
        ev("b", EvidenceKind.CHANGE_PROXIMITY),
        ev("c", EvidenceKind.HUMAN_STATEMENT, Stance.REFUTES),
    ]
    assert score(items) == score(list(reversed(items)))


@pytest.mark.req("REQ-053")
def test_inferred_evidence_counts_a_quarter_of_authoritative_evidence():
    """What stops a chain of model inferences manufacturing certainty."""
    measured = score((ev("a", EvidenceKind.TELEMETRY_ANOMALY, authority=Authority.AUTHORITATIVE),))
    inferred = score((ev("a", EvidenceKind.TELEMETRY_ANOMALY, authority=Authority.INFERRED),))
    assert measured > inferred > PRIOR


@pytest.mark.req("REQ-053")
def test_history_alone_cannot_carry_a_hypothesis_past_even_odds():
    """Historical similarity is advisory. Four prior incidents all agreeing must
    not on their own make a cause."""
    only_history = tuple(
        ev(f"h{i}", EvidenceKind.HISTORICAL_SIMILARITY) for i in range(4)
    )
    assert score(only_history) < 0.5
    assert score((ev("h", EvidenceKind.HISTORICAL_SIMILARITY),)) < 0.5
    # And it cannot rule one out either: history is advisory in both directions.
    against = tuple(
        ev(f"h{i}", EvidenceKind.HISTORICAL_SIMILARITY, Stance.REFUTES)
        for i in range(4)
    )
    assert score(against) > 0.2
    # One real measurement outweighs the whole advisory budget.
    assert score(only_history + (ev("m", EvidenceKind.CORROBORATION),)) > 0.7


@pytest.mark.req("REQ-053")
def test_confidence_is_never_zero_and_never_one():
    huge = tuple(ev(f"s{i}", EvidenceKind.DIAGNOSTIC_RESULT) for i in range(30))
    against = tuple(
        ev(f"r{i}", EvidenceKind.DIAGNOSTIC_RESULT, Stance.REFUTES) for i in range(30)
    )
    assert score(huge) <= MAX_CONFIDENCE
    assert score(against) >= MIN_CONFIDENCE
    assert score(huge) < 1.0 and score(against) > 0.0


@pytest.mark.req("REQ-053")
def test_strength_scales_a_published_weight_and_is_bounded():
    weak = score((ev("a", EvidenceKind.TELEMETRY_ANOMALY, strength=0.3),))
    strong = score((ev("a", EvidenceKind.TELEMETRY_ANOMALY, strength=1.8),))
    assert strong > weak
    with pytest.raises(BrainValidationError):
        ev("a", EvidenceKind.TELEMETRY_ANOMALY, strength=5.0)


@pytest.mark.req("REQ-053")
def test_an_entry_records_the_delta_the_evidence_and_a_readable_explanation():
    before = (ev("a", EvidenceKind.CHANGE_PROXIMITY),)
    after = before + (ev("b", EvidenceKind.DIAGNOSTIC_RESULT, Stance.REFUTES),)

    first = entry_for(
        hypothesis_key="hypothesis:change",
        at=NOW,
        previous=None,
        evidence=before,
        trigger="declared",
    )
    second = entry_for(
        hypothesis_key="hypothesis:change",
        at=NOW + timedelta(minutes=10),
        previous=first.current,
        evidence=after,
        previous_ids=[item.node_id for item in before],
        trigger="diagnostic returned",
    )

    assert second.previous == first.current
    assert second.current < first.current
    assert second.direction == "fell"
    assert second.added == ("b",)
    assert second.refuting == ("b",)
    # The explanation is written from the stored numbers, so it says the same
    # thing with or without a model.
    text = second.explain()
    assert f"{first.current:.0%}" in text and f"{second.current:.0%}" in text
    assert "1 new piece(s) of evidence" in text
    assert "diagnostic returned" in text


@pytest.mark.req("REQ-053")
def test_the_narrative_is_optional_and_the_explanation_is_not():
    entry = entry_for(
        hypothesis_key="h", at=NOW, previous=None, evidence=(), trigger=""
    )
    assert entry.narrative == ""
    assert entry.explain()
    assert entry.current == PRIOR


@pytest.mark.req("REQ-053")
def test_the_discriminating_test_names_the_next_useful_step():
    assert "any telemetry" in discriminating_need((), PRIOR)

    one_family = (ev("a", EvidenceKind.TELEMETRY_ANOMALY),)
    assert "second, independent" in discriminating_need(one_family, 0.6)

    two_families = one_family + (ev("b", EvidenceKind.SLO_BURN),)
    assert "disprove" in discriminating_need(two_families, 0.7)

    both_ways = two_families + (
        ev("c", EvidenceKind.HUMAN_STATEMENT, Stance.REFUTES),
    )
    assert discriminating_need(both_ways, 0.4)
