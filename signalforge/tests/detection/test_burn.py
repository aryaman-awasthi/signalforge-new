"""SLO error-budget burn rates (REQ-041).

Pure domain: the burn rule is arithmetic over an error budget, and the thing
worth testing is the arithmetic plus what it refuses to do with a number that is
not a ratio.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from signalforge.domain.detection.baselines import Observation
from signalforge.domain.detection.burn import (
    FAST_BURN_WINDOW_S,
    MIN_BURN_SAMPLES,
    SLOW_BURN_WINDOW_S,
    BurnVerdict,
    error_ratio,
    evaluate_slo,
)
from signalforge.domain.onboarding.profile import (
    GoldenSignal,
    SignalDirection,
    SignalMapping,
    SloTarget,
)

NOW = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)

SUCCESS_SIGNAL = SignalMapping(
    key="payments-success",
    signal=GoldenSignal.BUSINESS,
    selector="payment_success_ratio",
    direction=SignalDirection.LOWER_IS_WORSE,
    unit="ratio",
    business_counter=True,
)
ERROR_SIGNAL = SignalMapping(
    key="errors",
    signal=GoldenSignal.ERRORS,
    selector="error_ratio",
    direction=SignalDirection.HIGHER_IS_WORSE,
    unit="ratio",
)
COUNT_SIGNAL = SignalMapping(
    key="requests",
    signal=GoldenSignal.TRAFFIC,
    selector="requests_total",
    direction=SignalDirection.LOWER_IS_WORSE,
    unit="count",
)

SLO = SloTarget(
    slo_id="payments-availability",
    name="Payments availability",
    objective_pct=99.0,
    journey_id="payments",
    indicator="payments-success",
)


def points(*values: float) -> tuple[Observation, ...]:
    return tuple(
        Observation(at=NOW - timedelta(seconds=5 * (len(values) - i)), value=value)
        for i, value in enumerate(values)
    )


@pytest.mark.req("REQ-041")
def test_a_success_ratio_is_inverted_and_an_error_ratio_is_not():
    """Which of the two an operator mapped is exactly what SignalDirection
    records, so detection reads a budget off `payment_success_ratio` without a
    naming convention."""
    ratio, reason = error_ratio(SUCCESS_SIGNAL, points(0.9, 0.9, 0.9))
    assert ratio == pytest.approx(0.1)
    assert "inverted" in reason

    ratio, _reason = error_ratio(ERROR_SIGNAL, points(0.1, 0.1, 0.1))
    assert ratio == pytest.approx(0.1)


@pytest.mark.req("REQ-041")
def test_an_indicator_that_is_not_a_ratio_is_refused_rather_than_guessed():
    """A request count says nothing about what fraction of requests failed. A
    burn rate that is silently wrong is worse than one that is missing."""
    ratio, reason = error_ratio(COUNT_SIGNAL, points(1200.0, 1300.0, 1400.0))
    assert ratio is None
    assert "not a ratio" in reason

    burn = evaluate_slo(
        SLO, COUNT_SIGNAL, {"": points(1200.0, 1300.0, 1400.0)}, {"": points(1200.0)}
    )
    assert burn.verdict is BurnVerdict.NONE
    assert burn.fast.burn_rate is None
    assert "not a ratio" in burn.fast.detail


@pytest.mark.req("REQ-041")
def test_a_fast_burn_proposes_and_a_slow_burn_opens_watch():
    steady = points(*[0.99] * 6)
    # 50% of events failing against a 1% budget is 50x.
    fast_burn = evaluate_slo(SLO, SUCCESS_SIGNAL, {"": points(*[0.5] * 6)}, {"": steady})
    assert fast_burn.verdict is BurnVerdict.PROPOSE
    assert fast_burn.fast.burn_rate == pytest.approx(50.0)
    assert fast_burn.fast.window_s == FAST_BURN_WINDOW_S
    assert "fast burn" in fast_burn.detail

    # 8% failing is 8x: past the 6x slow multiplier, short of the 14.4x fast one.
    slow_burn = evaluate_slo(
        SLO, SUCCESS_SIGNAL, {"": points(*[0.92] * 6)}, {"": points(*[0.92] * 6)}
    )
    assert slow_burn.verdict is BurnVerdict.WATCH
    assert slow_burn.slow.window_s == SLOW_BURN_WINDOW_S
    assert slow_burn.slow.burn_rate == pytest.approx(8.0)
    assert not slow_burn.proposes and slow_burn.opens_watch


@pytest.mark.req("REQ-041")
def test_the_budget_is_spent_no_faster_than_planned_at_one_times():
    burn = evaluate_slo(
        SLO, SUCCESS_SIGNAL, {"": points(*[0.99] * 6)}, {"": points(*[0.99] * 6)}
    )
    assert burn.verdict is BurnVerdict.NONE
    assert burn.fast.burn_rate == pytest.approx(1.0)
    assert "no faster than planned" in burn.detail


@pytest.mark.req("REQ-041")
def test_the_worst_region_burns_rather_than_the_average_of_both():
    """A two-region deployment exists so one region can fail while the other
    keeps serving. Averaging is how a total failure in one reads as half a
    problem."""
    burn = evaluate_slo(
        SLO,
        SUCCESS_SIGNAL,
        {"region-a": points(*[0.99] * 6), "region-b": points(*[0.5] * 6)},
        {"region-a": points(*[0.99] * 6), "region-b": points(*[0.5] * 6)},
    )
    assert burn.verdict is BurnVerdict.PROPOSE
    assert burn.fast.region == "region-b"
    assert burn.fast.burn_rate == pytest.approx(50.0)


@pytest.mark.req("REQ-041")
def test_too_few_samples_is_no_burn_rate_rather_than_a_confident_one():
    burn = evaluate_slo(SLO, SUCCESS_SIGNAL, {"": points(0.5)}, {"": points(0.5)})
    assert burn.verdict is BurnVerdict.NONE
    assert burn.fast.burn_rate is None
    assert str(MIN_BURN_SAMPLES) in burn.fast.detail


@pytest.mark.req("REQ-041")
def test_an_slo_whose_indicator_is_not_mapped_says_so():
    burn = evaluate_slo(SLO, None, {}, {})
    assert burn.verdict is BurnVerdict.NONE
    assert "not a mapped golden signal" in burn.fast.detail
