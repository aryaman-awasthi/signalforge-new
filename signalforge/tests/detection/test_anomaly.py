"""Sustained-breach detection, end to end through the real poller.

Ten steady buckets, then a fault. Ten because a rolling baseline needs six
reference samples and the reference window stops four buckets short of the
bucket being judged - which is the rule that stops a breach from teaching the
baseline to accept it.
"""

from __future__ import annotations

import pytest

from signalforge.domain.detection.anomaly import SUSTAINED_EVALUATIONS
from signalforge.tests.detection.conftest import (
    APP_ID,
    REGION_A,
    REGION_B,
    detection,
    exposition,
    onboard,
    owner,
    steady,
    tick,
    topics,
)

pytestmark = pytest.mark.anyio

WARMUP_TICKS = 10

BROKEN_REGION_B = dict(
    success={REGION_A: 0.99, REGION_B: 0.50},
    errors={REGION_A: 0.01, REGION_B: 0.50},
)


def _state(verdict, signal_key: str, region: str):
    return next(
        state
        for state in verdict.states
        if state.signal_key == signal_key and state.region == region
    )


@pytest.mark.req("REQ-040")
async def test_a_breach_becomes_an_anomaly_only_on_the_third_evaluation(api, observe):
    token, tenant_id = owner(api)
    onboard(api, token)
    await steady(api, observe, tenant_id, WARMUP_TICKS)

    observe.state.metrics_text = exposition(**BROKEN_REGION_B)
    statuses = []
    for _ in range(SUSTAINED_EVALUATIONS):
        _report, verdict = await tick(api, tenant_id, evaluate=True)
        statuses.append(_state(verdict, "payments-success", REGION_B).status.value)

    assert statuses == ["breaching", "breaching", "anomalous"], statuses

    final = _state(verdict, "payments-success", REGION_B)
    assert final.streak == SUSTAINED_EVALUATIONS
    assert final.anomalous_since is not None
    assert final.threshold is not None and final.value == 0.5
    # The healthy region beside the broken one is the control the corroboration
    # rule needs, and it stays healthy.
    assert _state(verdict, "payments-success", REGION_A).status.value == "healthy"


@pytest.mark.req("REQ-040")
async def test_two_breaching_evaluations_are_not_an_anomaly(api, observe):
    """The whole point of the rule: one scrape is a GC pause, two is a slow
    disk, three is an event."""
    token, tenant_id = owner(api)
    onboard(api, token)
    await steady(api, observe, tenant_id, WARMUP_TICKS)

    observe.state.metrics_text = exposition(**BROKEN_REGION_B)
    for _ in range(2):
        _report, verdict = await tick(api, tenant_id, evaluate=True)

    assert _state(verdict, "payments-success", REGION_B).status.value == "breaching"
    assert verdict.stance.value == "watch", "a breach is worth watching, not proposing"
    assert not topics(api, token, "detection.anomaly.opened")


@pytest.mark.req("REQ-040")
async def test_one_clean_evaluation_clears_the_streak(api, observe):
    """Not a decay, not a half-life. A streak that survived recovery would
    eventually fire on three unrelated blips an hour apart."""
    token, tenant_id = owner(api)
    onboard(api, token)
    await steady(api, observe, tenant_id, WARMUP_TICKS)

    observe.state.metrics_text = exposition(**BROKEN_REGION_B)
    for _ in range(2):
        await tick(api, tenant_id, evaluate=True)

    observe.state.metrics_text = exposition()
    _report, recovered = await tick(api, tenant_id, evaluate=True)
    state = _state(recovered, "payments-success", REGION_B)
    assert state.status.value == "healthy"
    assert state.streak == 0
    assert state.first_breach_at is None

    observe.state.metrics_text = exposition(**BROKEN_REGION_B)
    _report, again = await tick(api, tenant_id, evaluate=True)
    assert _state(again, "payments-success", REGION_B).streak == 1


@pytest.mark.req("REQ-040")
async def test_a_baseline_is_held_while_the_signal_is_breaching(api, observe):
    """A breach that lasted long enough used to teach the rolling window that it
    was normal. The threshold in force when the breach began is the one it is
    still measured against."""
    token, tenant_id = owner(api)
    onboard(api, token)
    await steady(api, observe, tenant_id, WARMUP_TICKS)

    observe.state.metrics_text = exposition(**BROKEN_REGION_B)
    _report, first = await tick(api, tenant_id, evaluate=True)
    threshold = _state(first, "payments-success", REGION_B).threshold

    for _ in range(8):
        _report, verdict = await tick(api, tenant_id, evaluate=True)

    state = _state(verdict, "payments-success", REGION_B)
    assert state.status.value == "anomalous", "eight more ticks of 50% success"
    assert state.threshold == threshold
    assert state.baseline_source == "frozen"


@pytest.mark.req("REQ-040")
async def test_a_signal_that_stops_reporting_goes_stale_not_healthy(api, observe):
    """Silence is not health. A detector that reads a dead exporter as a healthy
    application is worse than no detector."""
    token, tenant_id = owner(api)
    onboard(api, token)
    await steady(api, observe, tenant_id, WARMUP_TICKS)
    _report, healthy = await tick(api, tenant_id, evaluate=True)
    assert _state(healthy, "saturation", REGION_A).status.value == "healthy"

    observe.state.metrics_text = exposition(saturation={})
    for _ in range(14):  # past the 60-second staleness limit
        _report, verdict = await tick(api, tenant_id, evaluate=True)

    stale = _state(verdict, "saturation", REGION_A)
    assert stale.status.value == "stale"
    assert "staleness limit" in stale.detail
    assert stale.streak == 0

    # A stale family scores nothing rather than scoring full marks, and drops
    # out of the covered weight instead of inflating the score.
    saturation = next(
        family
        for region in verdict.health.regions
        if region.region == REGION_A
        for family in region.families
        if family.family.value == "saturation"
    )
    assert saturation.fraction is None
    assert "stale" in saturation.detail

    events = topics(api, token, "detection.signal.stale")
    assert any(e["payload"]["signal_key"] == "saturation" for e in events)


@pytest.mark.req("REQ-040")
async def test_a_counter_is_read_as_a_rate_and_a_gauge_as_a_level(api, observe):
    """Nothing in Prometheus text exposition says whether a number accumulates,
    so the shape of the window decides. `errors_total` rising forever is not an
    anomaly; `errors_total` rising four times faster than it was is."""
    token, tenant_id = owner(api)
    onboard(
        api,
        token,
        mapping={
            "signals": [
                {
                    "key": "requests",
                    "signal": "traffic",
                    "selector": "requests_total",
                    "direction": "higher_is_worse",
                    "unit": "count",
                },
                {
                    "key": "saturation",
                    "signal": "saturation",
                    "selector": "saturation_ratio",
                    "direction": "higher_is_worse",
                    "unit": "ratio",
                },
            ],
            "region_label": "region",
            "regions": [REGION_A],
            "journeys": [],
            "probes": [],
        },
        slos=[],
    )

    total = 1_000
    for _ in range(WARMUP_TICKS + 1):
        total += 100
        observe.state.metrics_text = (
            f'requests_total{{region="{REGION_A}"}} {total}\n'
            f'saturation_ratio{{region="{REGION_A}"}} 0.4\n'
        )
        _report, verdict = await tick(api, tenant_id, evaluate=True)

    counter = _state(verdict, "requests", REGION_A)
    gauge = _state(verdict, "saturation", REGION_A)
    assert counter.basis.value == "rate"
    assert counter.value == pytest.approx(20.0), "100 per five-second bucket"
    assert counter.status.value == "healthy", "steady growth is not an anomaly"
    assert gauge.basis.value == "level"
    assert gauge.value == pytest.approx(0.4)


@pytest.mark.req("REQ-040")
async def test_reading_the_detection_view_never_advances_a_streak(api, observe):
    """A GET that aged a breach into an anomaly would make every proposal
    depend on how often somebody refreshed a screen."""
    token, tenant_id = owner(api)
    onboard(api, token)
    await steady(api, observe, tenant_id, WARMUP_TICKS)

    observe.state.metrics_text = exposition(**BROKEN_REGION_B)
    await tick(api, tenant_id, evaluate=True)

    for _ in range(5):
        body = detection(api, token)
    breach = next(
        signal
        for signal in body["signals"]
        if signal["signal_key"] == "payments-success" and signal["region"] == REGION_B
    )
    assert breach["status"] == "breaching"
    assert breach["streak"] == 1, "five reads, one evaluation"


@pytest.mark.req("REQ-040")
async def test_a_signal_that_has_regions_stops_reporting_an_unlabelled_one(
    api, observe
):
    """The bootstrap "no region" row does not outlive the bootstrap.

    Between the moment a signal is mapped and the moment it is first scraped,
    the detector has a mapped signal and no observation of it, and it says so:
    one unlabelled row reading UNKNOWN. That is right, and it is also every
    freshly onboarded application for the first few seconds.

    What was wrong is that the row was then carried forward for ever, because
    each evaluation unions the previous evaluation's states. A region that
    reports nothing outranks a region reporting well - correctly, since a
    silent region is the more important fact - so a perfectly healthy signal
    went on rendering as "no data" on the golden tile long after both of its
    real regions had been measured.
    """
    token, tenant_id = owner(api)
    onboard(api, token)

    # The bootstrap window: mapped, never scraped. One unlabelled row, UNKNOWN.
    _report, verdict = await tick(api, tenant_id, evaluate=True)
    bootstrap = [
        state
        for state in verdict.states
        if state.signal_key == "payments-success" and state.region == ""
    ]
    assert bootstrap, "a mapped signal with no data should report one unknown row"

    await steady(api, observe, tenant_id, WARMUP_TICKS, evaluate=True)
    _report, verdict = await tick(api, tenant_id, evaluate=True)

    regions = {
        state.region for state in verdict.states if state.signal_key == "payments-success"
    }
    assert regions == {REGION_A, REGION_B}, (
        "once a signal is measured per region it must not also carry the "
        f"unlabelled bootstrap row; got {sorted(regions)}"
    )

    # And it is gone from what the screens actually read, not only from this
    # verdict: the stored states are the last verdict, not a union of all of them.
    read = detection(api, token)
    stored = {
        signal["region"]
        for signal in read["signals"]
        if signal["signal_key"] == "payments-success"
    }
    assert stored == {REGION_A, REGION_B}, sorted(stored)
    assert [
        region for region in read["health"]["regions"] if region["region"] == ""
    ] == [], "the health breakdown still carries a region with no name"
