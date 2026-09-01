"""The synthetic probe runner (REQ-040).

The verdict has three values and the third one is the point: a probe that could
not be *run* is UNKNOWN, never FAILED. A platform that reported "checkout is
failing in both regions" because its own health connector was down would be
worse than one that reported nothing.
"""

from __future__ import annotations

import pytest

from signalforge.domain.detection.probes import ProbeVerdict
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
)

pytestmark = pytest.mark.anyio


@pytest.mark.req("REQ-040")
async def test_a_probe_runs_once_per_declared_region(api, observe):
    token, tenant_id = owner(api)
    onboard(api, token, with_health=True)

    records = await api.container.probe_runner.run(tenant_id, APP_ID)

    assert {record.region for record in records} == {REGION_A, REGION_B}
    assert {record.probe_id for record in records} == {"payments-probe"}
    assert {record.journey_id for record in records} == {"payments"}
    assert all(record.verdict is ProbeVerdict.PASSED for record in records)
    assert all(record.status_code == 200 for record in records)


@pytest.mark.req("REQ-040")
async def test_a_probe_the_target_fails_is_a_failure_and_reaches_the_verdict(
    api, observe
):
    token, tenant_id = owner(api)
    onboard(api, token, with_health=True)
    observe.state.fail("/checkout", 503)

    records = await api.container.probe_runner.run(tenant_id, APP_ID)
    assert all(record.verdict is ProbeVerdict.FAILED for record in records)

    await steady(api, observe, tenant_id, 1)
    verdict = await api.container.detection.evaluate(tenant_id, APP_ID)
    rows = {row.region: row for row in verdict.journeys}
    assert rows[REGION_A].failed == 1 and rows[REGION_A].passed == 0
    assert rows[REGION_A].success_rate_pct == 0.0
    assert rows[REGION_A].tier == 1
    assert verdict.stance.value == "watch", "a failing journey is worth watching"

    body = detection(api, token)
    assert {row["region"] for row in body["journeys"]} == {REGION_A, REGION_B}


@pytest.mark.req("REQ-040")
async def test_a_probe_that_could_not_be_run_is_unknown_not_failed(api, observe):
    """Permission change mid-flow, landing safely. A connector disabled between
    the sweep's work list and the call means the platform could not check - which
    says nothing about the journey, and must not be able to contribute to a
    severity proposal.

    A probe that *ran* and timed out is a different fact and is a FAILED: a
    customer hitting that entry point would have timed out too.
    """
    token, tenant_id = owner(api)
    bindings = onboard(api, token, with_health=True)

    async with api.container.uow_factory() as uow:
        stale = await uow.connectors.get(tenant_id, bindings["health"])

    disabled = api.client.patch(
        f"/connectors/{bindings['health']}",
        json={"enabled": False},
        headers=api.auth_header(token),
    )
    assert disabled.status_code == 200, disabled.text

    records = await api.container.probe_runner.run(tenant_id, APP_ID, binding=stale)
    assert all(record.verdict is ProbeVerdict.UNKNOWN for record in records)
    assert all("ConnectorDisabled" in record.detail for record in records)

    await steady(api, observe, tenant_id, 1)
    verdict = await api.container.detection.evaluate(tenant_id, APP_ID)
    row = next(row for row in verdict.journeys if row.region == REGION_A)
    assert row.unknown == 1 and row.failed == 0
    assert row.success_rate_pct is None
    assert not row.failing
    assert verdict.severity is None or not verdict.severity.impact.customer_impacting


@pytest.mark.req("REQ-040")
async def test_with_no_health_connector_the_journey_is_unknown_rather_than_green(
    api, observe
):
    token, tenant_id = owner(api)
    onboard(api, token)  # metrics only

    records = await api.container.probe_runner.run(tenant_id, APP_ID)
    assert records
    assert all(record.verdict is ProbeVerdict.UNKNOWN for record in records)
    assert "no enabled health connector" in records[0].detail


@pytest.mark.req("REQ-040")
async def test_running_the_probe_sweep_twice_at_one_instant_is_one_measurement(
    api, observe
):
    """The runner is a loop on a thirty-second timer. A replayed tick is not a
    second data point."""
    token, tenant_id = owner(api)
    onboard(api, token, with_health=True)

    await api.container.probe_runner.run(tenant_id, APP_ID)
    await api.container.probe_runner.run(tenant_id, APP_ID)

    await steady(api, observe, tenant_id, 1)
    verdict = await api.container.detection.evaluate(tenant_id, APP_ID)
    row = next(row for row in verdict.journeys if row.region == REGION_A)
    assert row.passed == 1, "two sweeps at one instant is one measurement"


@pytest.mark.req("REQ-040")
async def test_the_sweep_covers_every_application_with_a_probe_runner(api, observe):
    token, tenant_id = owner(api)
    onboard(api, token, with_health=True)

    sweep = await api.container.probe_runner.run_all()
    assert sweep.applications == 1
    assert sweep.runs == 2 and sweep.passed == 2
    assert "2 probe run(s)" in str(sweep)


@pytest.mark.req("REQ-040")
async def test_a_failing_probe_is_the_customer_facing_half_of_corroboration(
    api, observe
):
    """When an application's own business counter has stopped being exported,
    the platform's own first-hand check is what says customers are affected."""
    token, tenant_id = owner(api)
    onboard(api, token, with_health=True)
    await steady(api, observe, tenant_id, 10, evaluate=True)

    observe.state.fail("/checkout", 503)
    observe.state.metrics_text = exposition(
        saturation={REGION_A: 0.95, REGION_B: 0.95}
    )
    for _ in range(3):
        await api.container.probe_runner.run(tenant_id, APP_ID)
        _report, verdict = await tick(api, tenant_id, evaluate=True)

    assert verdict.corroboration.corroborated
    assert set(verdict.corroboration.families) == {"availability", "saturation"}
    probe_evidence = [
        ref for ref in verdict.corroboration.evidence if ref.kind == "probe"
    ]
    assert probe_evidence and probe_evidence[0].customer_facing
    assert verdict.stance.value == "proposed"
