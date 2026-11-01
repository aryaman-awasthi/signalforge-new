"""Steady to a corroborated proposal, through the real API (S4-T01).

This is the sprint's acceptance clause on the detection side: chaos on the
application produces Watch, then a corroborated proposal with evidence refs. It
is driven end to end - an application onboarded through the wizard's own routes,
the shipped poller scraping the frozen `/observe/metrics` contract, and the
verdict read back over HTTP - so what is under test is the pipeline rather than
a hand-built verdict.
"""

from __future__ import annotations

import json

import pytest

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

WARMUP_TICKS = 10

BOTH_REGIONS_BROKEN = dict(
    success={REGION_A: 0.45, REGION_B: 0.50},
    errors={REGION_A: 0.55, REGION_B: 0.50},
)
ONE_REGION_BROKEN = dict(
    success={REGION_A: 0.99, REGION_B: 0.70},
    errors={REGION_A: 0.01, REGION_B: 0.30},
)


@pytest.mark.req("REQ-040", "REQ-041", "REQ-042", "REQ-046")
async def test_steady_then_watch_then_a_corroborated_p0_proposal(api, observe):
    token, tenant_id = owner(api)
    onboard(api, token)

    await steady(api, observe, tenant_id, WARMUP_TICKS, evaluate=True)
    body = detection(api, token)
    assert body["stance"] == "steady"
    assert body["severity"] is None
    assert body["health"]["band"] == "healthy"

    observe.state.metrics_text = exposition(**BOTH_REGIONS_BROKEN)

    _report, first = await tick(api, tenant_id, evaluate=True)
    assert first.stance.value == "watch", "a breach is worth watching, not proposing"
    assert not first.corroboration.corroborated

    await tick(api, tenant_id, evaluate=True)
    _report, third = await tick(api, tenant_id, evaluate=True)

    assert third.stance.value == "proposed"
    assert third.corroboration.corroborated
    assert set(third.corroboration.families) == {"business", "errors"}
    assert third.severity is not None
    assert third.severity.severity.value == "P0"
    assert third.severity.impact.regions_affected == 2
    assert third.severity.impact.journey_tier == 1
    assert third.severity.requires_human is True

    # The proposal is readable over HTTP, and it says it declared nothing.
    body = detection(api, token)
    assert body["stance"] == "proposed"
    assert body["severity"]["severity"] == "P0"
    assert body["severity"]["declared"] is False
    assert body["severity"]["requires_human"] is True
    assert body["health"]["band"] in ("degraded", "unhealthy")
    assert body["health"]["worst_region"] in (REGION_A, REGION_B)

    # Evidence refs point at measurements, not at prose.
    refs = {ref["ref"] for ref in body["severity"]["evidence"]}
    assert f"rollup:payments-success/{REGION_B}" in refs
    assert any(ref.startswith("slo:payments-availability/") for ref in refs)
    series = rollups(api, token, signal_key="payments-success", region=REGION_B)
    assert series["count"] > 0, "the evidence ref names rollups that can be read"

    # And the whole escalation is on the timeline as transitions, not a heartbeat.
    stances = topics(api, token, "detection.stance.changed")
    assert [event["payload"]["stance"] for event in stances] == [
        "steady", "watch", "proposed"
    ]
    proposals = topics(api, token, "detection.severity.proposed")
    assert len(proposals) == 1
    assert proposals[0]["payload"]["severity"] == "P0"
    assert proposals[0]["payload"]["declared"] is False
    assert proposals[0]["payload"]["requires_human"] is True
    assert proposals[0]["actor"]["type"] == "system"

    opened = topics(api, token, "detection.anomaly.opened")
    assert {event["payload"]["family"] for event in opened} == {"business", "errors"}


@pytest.mark.req("REQ-041", "REQ-046")
async def test_a_single_region_fault_is_graded_by_its_impact_not_its_region_count(
    api, observe
):
    """The matrix discriminates on how far the success rate fell, not on how
    many regions fell (S10-T06). A region count is a poor proxy for blast
    radius: in a single-region deployment the worst incident and the mildest are
    both "one region". One region with a tier-1 journey at 70 percent is a P0,
    and the proposal still names which region it is."""
    token, tenant_id = owner(api)
    onboard(api, token)
    await steady(api, observe, tenant_id, WARMUP_TICKS, evaluate=True)

    observe.state.metrics_text = exposition(**ONE_REGION_BROKEN)
    for _ in range(3):
        _report, verdict = await tick(api, tenant_id, evaluate=True)

    assert verdict.stance.value == "proposed"
    assert verdict.severity.severity.value == "P0"
    assert verdict.severity.impact.regions_affected == 1
    assert verdict.severity.impact.affected_regions == (REGION_B,)


@pytest.mark.req("REQ-041")
async def test_one_family_alone_reaches_watch_and_never_a_proposal(api, observe):
    """The false-alarm rule. Saturation on its own is a backup job, and it must
    not be able to page anybody however long it lasts."""
    token, tenant_id = owner(api)
    onboard(api, token)
    await steady(api, observe, tenant_id, WARMUP_TICKS, evaluate=True)

    observe.state.metrics_text = exposition(
        saturation={REGION_A: 0.95, REGION_B: 0.95}
    )
    for _ in range(6):
        _report, verdict = await tick(api, tenant_id, evaluate=True)

    assert verdict.stance.value == "watch"
    assert not verdict.corroboration.corroborated
    assert verdict.corroboration.families == ("saturation",)
    assert "corroboration needs" in verdict.corroboration.reason
    assert not topics(api, token, "detection.severity.proposed")


@pytest.mark.req("REQ-041")
async def test_a_slow_burn_opens_watch_before_any_signal_is_anomalous(api, observe):
    """Fast burn proposes; slow burn opens Watch.

    92 percent success against a 99 percent objective burns the budget several
    times faster than planned, but it is only a seven-point drop - inside the
    rolling baseline's ten-percent floor, so no individual signal ever breaches.
    The budget is what notices, which is exactly what an error budget is for.
    """
    token, tenant_id = owner(api)
    onboard(api, token)
    await steady(api, observe, tenant_id, WARMUP_TICKS, evaluate=True)

    observe.state.metrics_text = exposition(
        success={REGION_A: 0.92, REGION_B: 0.92}
    )
    for _ in range(30):
        _report, verdict = await tick(api, tenant_id, evaluate=True)

    burn = verdict.burns[0]
    assert burn.verdict.value == "watch"
    assert 6.0 <= burn.slow.burn_rate < 14.4, burn.detail
    assert not burn.fast.breached
    assert verdict.stance.value == "watch"
    assert not any(state.status.value == "anomalous" for state in verdict.states)

    burns = topics(api, token, "detection.slo.burn")
    assert burns and burns[-1]["payload"]["verdict"] == "watch"


@pytest.mark.req("REQ-040", "REQ-043")
async def test_forcing_an_evaluation_is_a_human_act_that_still_declares_nothing(
    api, observe
):
    token, tenant_id = owner(api)
    onboard(api, token)
    await steady(api, observe, tenant_id, WARMUP_TICKS)

    response = api.client.post(
        f"/apps/{APP_ID}/detection/evaluate", headers=api.auth_header(token)
    )
    assert response.status_code == 200, response.text
    assert response.json()["stance"] in ("steady", "watch")

    forced = topics(api, token, "detection.evaluation.forced")
    assert len(forced) == 1
    assert forced[0]["actor"]["type"] == "human"
    assert "never declares" in forced[0]["payload"]["note"]
    assert "detection.evaluation.forced" in api.audit_topics(tenant_id)


@pytest.mark.req("REQ-042")
async def test_an_alert_storm_is_clustered_from_the_timeline(api, observe):
    """Alerts arrive by push and land on the timeline, so the clusterer reads
    them from the same rows an auditor replays - there is no second store of
    alerts to fall out of step with the events."""
    from signalforge.tests.onboarding.conftest import SIMPLE_TOPOLOGY, upload_topology

    token, tenant_id = owner(api)
    onboard(api, token)
    upload_topology(api, token, SIMPLE_TOPOLOGY, app_id=APP_ID)

    async with api.container.uow_factory() as uow:
        for index, node in enumerate(["db", "web", "web"]):
            await uow.events.append(
                _alert_envelope(tenant_id, f"a{index}", node, api.clock)
            )
        await uow.commit()

    verdict = await api.container.detection.evaluate(tenant_id, APP_ID)
    assert verdict.storm.alerts == 3
    assert len(verdict.storm.clusters) == 1
    cluster = verdict.storm.clusters[0]
    assert cluster.suppressed_count == 2
    assert cluster.root_node_id == "db", "the most depended-upon node in the cluster"

    body = detection(api, token)
    assert body["storm"]["suppressed"] == 2
    assert body["storm"]["storming"] is True
    assert body["storm"]["clusters"][0]["root_node_id"] == "db"

    storms = topics(api, token, "detection.alert.storm")
    assert storms and storms[-1]["payload"]["suppressed"] == 2
    assert storms[-1]["payload"]["roots"][0]["root_node_id"] == "db"


def _alert_envelope(tenant_id: str, alert_id: str, node: str, clock):
    from signalforge.contracts.envelope import (
        Actor,
        ActorType,
        Authority,
        EventEnvelope,
        Source,
    )

    return EventEnvelope(
        topic="alert.fired",
        tenant_id=tenant_id,
        app_id=APP_ID,
        occurred_at=clock.now(),
        actor=Actor(type=ActorType.EXTERNAL, id="grafana"),
        source=Source(system="grafana", authority=Authority.OBSERVED),
        payload=json.loads(
            json.dumps(
                {
                    "alert_id": alert_id,
                    "name": "HighErrorRate",
                    "severity": "critical",
                    "status": "firing",
                    "starts_at": clock.now().isoformat(),
                    "labels": {"service": node},
                    "annotations": {},
                }
            )
        ),
    )


@pytest.mark.req("REQ-042")
async def test_an_alert_that_resolved_leaves_the_storm_and_a_naive_timestamp_does_not_break_it(
    api, observe
):
    """Written failing-first, two defects in the timeline read.

    Every alert row on the timeline was handed to the clusterer, so an alert's
    firing row stayed a storm member after its resolution had arrived; the
    clusterer only ever saw both rows. One alert is its latest row. And a
    producer whose timestamps carry no zone made the subtraction against the
    clock's aware `now` raise, failing the evaluation of every signal; such a
    row is skipped like one that will not parse.
    """
    from signalforge.tests.onboarding.conftest import SIMPLE_TOPOLOGY, upload_topology

    token, tenant_id = owner(api)
    onboard(api, token)
    upload_topology(api, token, SIMPLE_TOPOLOGY, app_id=APP_ID)

    async with api.container.uow_factory() as uow:
        await uow.events.append(_alert_envelope(tenant_id, "a0", "db", api.clock))
        await uow.events.append(_alert_envelope(tenant_id, "a1", "web", api.clock))
        resolved = _alert_envelope(tenant_id, "a1", "web", api.clock)
        resolved.payload["status"] = "resolved"
        resolved.payload["ends_at"] = api.clock.now().isoformat()
        await uow.events.append(resolved.model_copy(update={"topic": "alert.resolved"}))
        naive = _alert_envelope(tenant_id, "a2", "web", api.clock)
        naive.payload["starts_at"] = api.clock.now().replace(tzinfo=None).isoformat()
        await uow.events.append(naive)
        await uow.commit()

    verdict = await api.container.detection.evaluate(tenant_id, APP_ID)
    assert verdict.storm.alerts == 1, "a1 resolved and a2 is malformed; a0 stands"
    assert verdict.storm.clusters[0].alert_ids == ("a0",)
