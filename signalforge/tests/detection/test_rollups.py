"""Five-second rollups: the grid, the idempotency, and the failure paths.

`test_two_polls_inside_one_bucket_are_one_observation` and
`test_another_tenants_rollups_are_invisible` were written before the store
existed and run red against a missing table first, as CLAUDE.md requires for
idempotency and tenant isolation.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from signalforge.domain.detection.rollups import ROLLUP_INTERVAL_S, bucket_start
from signalforge.tests.detection.conftest import (
    APP_ID,
    REGION_A,
    REGION_B,
    detection,
    exposition,
    onboard,
    owner,
    rollups,
    steady,
    tick,
    topics,
)

pytestmark = pytest.mark.anyio


@pytest.mark.req("REQ-040")
async def test_a_poll_writes_one_rollup_per_signal_and_region(api, observe):
    token, tenant_id = owner(api)
    onboard(api, token)
    observe.state.metrics_text = exposition()

    report, _ = await tick(api, tenant_id)

    assert report.ok, report.error
    # three signals across two regions
    assert report.written == 6, report.detail
    assert report.missed == ()

    body = rollups(api, token, window_s=300)
    assert {(row["signal_key"], row["region"]) for row in body["rollups"]} == {
        ("payments-success", REGION_A),
        ("payments-success", REGION_B),
        ("errors", REGION_A),
        ("errors", REGION_B),
        ("saturation", REGION_A),
        ("saturation", REGION_B),
    }
    assert all(
        row["samples"] == 1 and row["series_count"] == 1 for row in body["rollups"]
    )
    assert body["interval_s"] == ROLLUP_INTERVAL_S


@pytest.mark.req("REQ-040")
async def test_rollups_are_bucketed_onto_the_five_second_grid(api, observe):
    token, tenant_id = owner(api)
    onboard(api, token)
    observe.state.metrics_text = exposition()

    # Two polls two seconds apart land in the same bucket; a third, five
    # seconds later, does not.
    api.clock.advance(seconds=1)
    await api.container.rollup_poller.poll(tenant_id, APP_ID)
    api.clock.advance(seconds=2)
    await api.container.rollup_poller.poll(tenant_id, APP_ID)
    api.clock.advance(seconds=5)
    await api.container.rollup_poller.poll(tenant_id, APP_ID)

    body = rollups(api, token, window_s=300, signal_key="errors", region=REGION_A)
    buckets = sorted({row["bucket_start"] for row in body["rollups"]})
    assert len(buckets) == 2, "two seconds apart is one bucket; five is two"
    for raw in buckets:
        moment = datetime.fromisoformat(raw)
        assert moment == bucket_start(moment)
        assert moment.second % ROLLUP_INTERVAL_S == 0


@pytest.mark.req("REQ-040")
async def test_two_polls_inside_one_bucket_are_one_observation(api, observe):
    """Written failing-first. A bucket is one observation: a slow tick that
    overlaps the next, a restart mid-sweep or an operator forcing a sweep must
    not be able to move the baseline the detector is about to compare against."""
    token, tenant_id = owner(api)
    onboard(api, token)
    observe.state.metrics_text = exposition()

    api.clock.advance(seconds=5)
    first = await api.container.rollup_poller.poll(tenant_id, APP_ID)
    second = await api.container.rollup_poller.poll(tenant_id, APP_ID)

    assert first.written == 6 and first.duplicates == 0
    assert second.written == 0 and second.duplicates == 6, second.detail
    assert rollups(api, token, window_s=300)["count"] == 6


@pytest.mark.req("REQ-040")
async def test_a_signal_whose_selector_matches_nothing_is_named_and_the_rest_land(
    api, observe
):
    """Partial completion. Two of three signals reachable is two rollups and one
    named gap, not a lost poll."""
    token, tenant_id = owner(api)
    onboard(api, token)
    observe.state.metrics_text = exposition(saturation={})

    report, _ = await tick(api, tenant_id)

    assert report.ok
    assert report.written == 4
    assert [miss.signal_key for miss in report.missed] == ["saturation"]
    assert "saturation_ratio" in report.missed[0].reason
    assert report.partial


@pytest.mark.req("REQ-040")
async def test_a_timed_out_scrape_writes_no_rollup_and_says_so_once(api, observe):
    """Timeout is designed. No rollup is written, the outage is announced once
    rather than every five seconds, and recovery is announced too."""
    token, tenant_id = owner(api)
    onboard(api, token)
    observe.state.metrics_text = exposition()
    observe.times_out("/observe/metrics")

    first, _ = await tick(api, tenant_id)
    second, _ = await tick(api, tenant_id)

    assert not first.ok and first.degraded
    assert "timed out" in first.error
    assert first.written == 0 and second.written == 0

    degraded = topics(api, token, "system.detection.poll.degraded")
    assert len(degraded) == 1, "an hour of downtime is one event, not 720"
    assert degraded[0]["payload"]["outcome"] == "timeout"
    assert "stale rather than healthy" in degraded[0]["payload"]["consequence"]

    observe.timing_out.clear()
    third, _ = await tick(api, tenant_id)
    assert third.ok and third.written == 6
    assert len(topics(api, token, "system.detection.poll.recovered")) == 1


@pytest.mark.req("REQ-040", "REQ-013")
async def test_another_tenants_rollups_are_invisible(api, observe):
    """Written failing-first. Two tenants both call their application
    `payments`; neither may read the other's measurements."""
    token_a, tenant_a = owner(api)
    onboard(api, token_a)
    await steady(api, observe, tenant_a, 3)

    signed = api.client.post(
        "/auth/signup",
        json={
            "org_name": "Other Corp",
            "email": "other@other.io",
            "password": "another-strong-password",
            "display_name": "Other",
        },
    )
    assert signed.status_code == 201, signed.text
    token_b = signed.json()["tokens"]["access_token"]
    onboard(api, token_b)
    await steady(api, observe, signed.json()["org"]["id"], 1)

    assert rollups(api, token_b)["count"] == 6, "one tick of my own, none of theirs"
    assert rollups(api, token_a)["count"] == 18


@pytest.mark.req("REQ-040")
async def test_an_application_with_no_metrics_connector_is_not_an_error(api, observe):
    """A poll of an application nothing is bound to reports why and writes
    nothing. It is a configuration state, not a failure to be counted."""
    token, tenant_id = owner(api)
    from signalforge.tests.onboarding.conftest import register_app

    register_app(api, token, app_id="unwired", name="Unwired")
    report = await api.container.rollup_poller.poll(tenant_id, "unwired")
    assert report.ok and report.written == 0
    assert "no enabled metrics connector" in report.detail


@pytest.mark.req("REQ-040")
async def test_the_rollup_store_ages_out_and_the_detector_survives_it(api, observe):
    """Rollups are derived, so retention is aggressive. Losing them costs the
    detector its recent history and costs the incident record nothing."""
    token, tenant_id = owner(api)
    onboard(api, token)
    await steady(api, observe, tenant_id, 3)
    assert rollups(api, token)["count"] == 18

    # Eleven minutes later, with a ten-minute retention: old enough to age out,
    # and inside the fifteen-minute access token so the read below is still the
    # same operator asking.
    api.clock.advance(minutes=11)
    removed = await api.container.detection.prune(retention_s=600)
    assert removed >= 18

    assert rollups(api, token, window_s=86_400)["count"] == 0
    assert detection(api, token)["app_id"] == APP_ID
