"""Corroboration and the weighted health score (REQ-041, REQ-042).

Pure domain. Corroboration is the gate a proposal has to pass, and the health
score is the number an operator reads first, so both are tested against states
constructed directly rather than through a poll - the point under test is the
rule, not the plumbing that feeds it.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from signalforge.domain.detection.anomaly import SignalState, SignalStatus
from signalforge.domain.detection.corroboration import corroborate
from signalforge.domain.detection.health import (
    FAMILY_WEIGHTS,
    TOTAL_WEIGHT,
    HealthBand,
    score_health,
)
from signalforge.domain.detection.probes import JourneyHealth
from signalforge.domain.onboarding.profile import GoldenSignal

NOW = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)


def state(
    signal_key: str,
    family: GoldenSignal,
    *,
    region: str = "region-a",
    status: SignalStatus = SignalStatus.HEALTHY,
    deviation: float = 0.0,
) -> SignalState:
    return SignalState(
        tenant_id="t",
        app_id="payments",
        signal_key=signal_key,
        region=region,
        family=family,
        status=status,
        streak=3 if status is SignalStatus.ANOMALOUS else 0,
        value=1.0,
        threshold=0.5,
        baseline_mean=1.0,
        deviation=deviation,
        last_value_at=NOW,
        evaluated_at=NOW,
        detail=f"{signal_key} is {status.value}",
    )


# --- corroboration ------------------------------------------------------------


@pytest.mark.req("REQ-041")
def test_two_independent_families_with_one_customer_facing_corroborate():
    result = corroborate(
        [
            state("errors", GoldenSignal.ERRORS, status=SignalStatus.ANOMALOUS),
            state("cpu", GoldenSignal.SATURATION, status=SignalStatus.ANOMALOUS),
        ],
        [],
        [],
        at=NOW,
    )
    assert result.corroborated
    assert result.families == ("errors", "saturation")
    assert result.customer_facing_families == ("errors",)
    assert {ref.kind for ref in result.evidence} == {"signal"}
    assert all(ref.ref.startswith("rollup:") for ref in result.evidence)


@pytest.mark.req("REQ-041")
def test_one_family_twice_is_one_witness():
    """Two error signals on two services are the same measurement taken twice.
    Treating them as two witnesses is how alert storms become incidents nobody
    declared."""
    result = corroborate(
        [
            state("checkout-errors", GoldenSignal.ERRORS, status=SignalStatus.ANOMALOUS),
            state("billing-errors", GoldenSignal.ERRORS, status=SignalStatus.ANOMALOUS),
        ],
        [],
        [],
        at=NOW,
    )
    assert not result.corroborated
    assert result.families == ("errors",)
    assert "2 independent families" in result.reason


@pytest.mark.req("REQ-041")
def test_two_families_with_no_customer_evidence_do_not_corroborate():
    result = corroborate(
        [
            state("cpu", GoldenSignal.SATURATION, status=SignalStatus.ANOMALOUS),
            state("p99", GoldenSignal.LATENCY, status=SignalStatus.ANOMALOUS),
        ],
        [],
        [],
        at=NOW,
    )
    assert not result.corroborated
    assert "none of them is customer-facing" in result.reason


@pytest.mark.req("REQ-041")
def test_a_failing_probe_is_customer_facing_evidence_and_carries_its_ref():
    result = corroborate(
        [state("cpu", GoldenSignal.SATURATION, status=SignalStatus.ANOMALOUS)],
        [],
        [
            JourneyHealth(
                journey_id="payments",
                region="region-b",
                passed=1,
                failed=4,
                unknown=0,
                tier=1,
                last_at=NOW,
                last_detail="503 from /checkout",
            )
        ],
        at=NOW,
    )
    assert result.corroborated
    assert "availability" in result.families
    probe = next(ref for ref in result.evidence if ref.kind == "probe")
    assert probe.ref == "journey:payments/region-b"
    assert probe.customer_facing and "4 of 5" in probe.detail


@pytest.mark.req("REQ-041")
def test_a_breaching_signal_is_not_yet_corroborating_evidence():
    """Corroboration counts anomalies, not breaches. Otherwise the sustained
    rule would be bypassed by having two noisy families at once."""
    result = corroborate(
        [
            state("errors", GoldenSignal.ERRORS, status=SignalStatus.BREACHING),
            state("cpu", GoldenSignal.SATURATION, status=SignalStatus.BREACHING),
        ],
        [],
        [],
        at=NOW,
    )
    assert not result.corroborated
    assert result.reason == "nothing is anomalous"


# --- health score -------------------------------------------------------------


@pytest.mark.req("REQ-042")
def test_the_weights_are_the_architectures_and_sum_to_one_hundred():
    assert TOTAL_WEIGHT == 100
    assert FAMILY_WEIGHTS[GoldenSignal.AVAILABILITY] == 30
    assert FAMILY_WEIGHTS[GoldenSignal.ERRORS] == 25
    assert FAMILY_WEIGHTS[GoldenSignal.BUSINESS] == 20
    assert FAMILY_WEIGHTS[GoldenSignal.LATENCY] == 15
    assert FAMILY_WEIGHTS[GoldenSignal.SATURATION] == 10
    assert GoldenSignal.TRAFFIC not in FAMILY_WEIGHTS, (
        "traffic is context, not health: a score that could not tell an outage "
        "from a Sunday would drift every weekend"
    )


@pytest.mark.req("REQ-042")
def test_a_healthy_application_scores_one_hundred_over_the_weight_it_covers():
    score = score_health(
        [
            state("availability", GoldenSignal.AVAILABILITY),
            state("errors", GoldenSignal.ERRORS),
        ]
    )
    assert score.score == 100.0
    assert score.band is HealthBand.HEALTHY
    assert score.covered_weight == 55, (
        "an application with no latency metric is not permanently capped at 85 "
        "for a signal it does not emit"
    )


@pytest.mark.req("REQ-042")
def test_a_signal_on_its_threshold_costs_half_its_family_weight():
    score = score_health(
        [
            state("availability", GoldenSignal.AVAILABILITY, deviation=0.0),
            state("errors", GoldenSignal.ERRORS, deviation=1.0),
        ]
    )
    # 30 points at full, 25 at half, over a covered weight of 55.
    assert score.score == pytest.approx(100.0 * (30 + 12.5) / 55, abs=0.01)
    assert score.band is HealthBand.DEGRADED, (
        "on the threshold is where 'unusual' starts, not where 'broken' does"
    )


@pytest.mark.req("REQ-042")
def test_the_worst_region_wins_rather_than_the_average(api=None):
    """An application at 100 in one region and 20 in another is a 20. Averaging
    would let a healthy region hide the broken one a two-region deployment
    exists to survive."""
    score = score_health(
        [
            state("availability", GoldenSignal.AVAILABILITY, region="region-a"),
            state("errors", GoldenSignal.ERRORS, region="region-a"),
            state("availability", GoldenSignal.AVAILABILITY, region="region-b"),
            state(
                "errors",
                GoldenSignal.ERRORS,
                region="region-b",
                status=SignalStatus.ANOMALOUS,
                deviation=5.0,
            ),
        ]
    )
    assert score.worst_region == "region-b"
    assert score.score == pytest.approx(100.0 * 30 / 55, abs=0.01)
    by_region = {region.region: region.score for region in score.regions}
    assert by_region["region-a"] == 100.0


@pytest.mark.req("REQ-042")
def test_the_worst_signal_in_a_family_speaks_for_the_family():
    """Averaging would let four healthy error metrics bury the one on fire."""
    score = score_health(
        [
            state("errors-a", GoldenSignal.ERRORS, deviation=0.0),
            state("errors-b", GoldenSignal.ERRORS, deviation=0.0),
            state(
                "errors-c",
                GoldenSignal.ERRORS,
                status=SignalStatus.ANOMALOUS,
                deviation=3.0,
            ),
        ]
    )
    assert score.score == 0.0
    family = score.regions[0].families[0]
    assert family.family is GoldenSignal.ERRORS
    assert family.fraction == 0.0
    assert set(family.signals) == {"errors-a", "errors-b", "errors-c"}


@pytest.mark.req("REQ-042")
def test_nothing_measured_is_unknown_rather_than_a_hundred():
    score = score_health(
        [state("errors", GoldenSignal.ERRORS, status=SignalStatus.STALE)]
    )
    assert score.score is None
    assert score.band is HealthBand.UNKNOWN
    assert score.covered_weight == 0
    assert "would be a lie" in score.detail
