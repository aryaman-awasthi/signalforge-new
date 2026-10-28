"""Sprint 4's acceptance clause, driven for real (REQ-040, REQ-041, REQ-046, REQ-038).

    "Chaos on the harness produces Watch, then a corroborated P0 proposal with
     evidence refs" - and a person still has to declare it.

Sprint 4 got the first half and not the second: its drill broke one link on one
region's route, one signal family became anomalous, and the corroboration gate
refused - correctly - to call one witness two. The proposal was P2, the IC
declared P0 by hand, and the log recorded the shortfall honestly.

Nothing about the gate is changed here. The drill is (drills/s4-acceptance.yaml):
the fault moves to CORE, the one link both regions cross, and the second witness
is the platform's own synthetic probe rather than a second metric from the same
reporter. Two independent families, one of them customer-facing, two regions, a
tier-1 journey below 80 percent - which is what the application's own severity
matrix calls a P0, and it says so on its own.

**Deterministic on purpose.** The harness's seeded traffic generator is switched
off and this test drives every payment itself, so the success ratio is arithmetic
rather than a sample: the window holds exactly the payments driven into it. The
platform clock is frozen and advanced five seconds per tick, which is how the
rollup grid is written. Nothing here waits for anything.

It skips, loudly, when the jar is not built or the drill file has been removed.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
import yaml

from signalforge.tests.reference.conftest import (
    DRILL,
    ROOT,
    bearer,
    start_harness,
)

pytestmark = pytest.mark.anyio

DRILL_FILE = ROOT / "drills" / "s4-acceptance.yaml"
HTTP_TIMEOUT_S = 20.0


@pytest.fixture(scope="module")
def drill() -> dict[str, Any]:
    if not DRILL_FILE.exists():
        pytest.skip(
            "drills/s4-acceptance.yaml is not present; the acceptance scenario is "
            "data and the product runs without it (CLAUDE.md rule 7)"
        )
    return yaml.safe_load(DRILL_FILE.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def quiet_harness(tmp_path_factory):
    """The harness with its own traffic generator off.

    A seeded background stream is right for a demo and wrong for an assertion:
    it would make the success ratio a sample of two sources instead of a
    statement about the payments this test placed.
    """
    yield from start_harness(
        tmp_path_factory.mktemp("harness-acceptance"),
        env={"PAYSPHERE_TRAFFICGEN": "false"},
        settle_s=0.0,
    )


# --- driving the application --------------------------------------------------


def place(base_url: str, journey: str, region: str, *, count: int) -> None:
    """Real customer payments through the application's own public entry point.

    Not synthetic: the synthetic header would keep them out of the business
    counters, which is exactly the counter this drill is about.
    """
    with httpx.Client(timeout=HTTP_TIMEOUT_S) as client:
        for _ in range(count):
            client.post(
                f"{base_url}/api/payments/{journey}",
                headers={"X-Region": region},
                json={"amount_minor": 129900},
            )


def inject(base_url: str, drill: dict) -> dict:
    """The fault, through the frozen chaos path with a real drill credential."""
    chaos = drill["chaos"]
    response = httpx.post(
        f"{base_url}/observe/chaos/{chaos['scenario_id']}/inject",
        headers=bearer(DRILL),
        json={
            "params": chaos["params"],
            "scope": drill["scope"],
            "idempotency_key": chaos["idempotency_key"],
        },
        timeout=HTTP_TIMEOUT_S,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["state"] == "active", body
    return body


async def tick(api, base_url: str, tenant_id: str, app_id: str, *, payments: int,
               journey: str, regions: list[str]):
    """One five-second bucket: move the clock, drive load, scrape, probe, judge."""
    api.clock.advance(seconds=5)
    for region in regions:
        place(base_url, journey, region, count=payments)
    await api.container.rollup_poller.poll(tenant_id, app_id)
    await api.container.probe_runner.run(tenant_id, app_id)
    return await api.container.detection.evaluate(tenant_id, app_id)


# --- onboarding the application through its own public contracts --------------


def onboard(api, token: str, base_url: str, drill: dict) -> None:
    headers = api.auth_header(token)
    app = drill["application"]
    app_id = app["app_id"]

    registered = api.client.post(
        "/apps",
        json={
            "app_id": app_id,
            "name": "Payments",
            "tier": app["tier"],
            "base_url": base_url,
        },
        headers=headers,
    )
    assert registered.status_code == 201, registered.text

    for connector in drill["connectors"]:
        bound = api.client.post(
            "/connectors",
            json={
                "app_id": app_id,
                "adapter_id": connector["adapter_id"],
                "name": f"payments {connector['adapter_id']}",
                "credential_scope": connector["credential_scope"],
                "config": (
                    {"base_url": base_url}
                    if connector["targets_the_application"]
                    else {}
                ),
                "credentials": (
                    {"token": connector["credential"]}
                    if connector["credential"]
                    else {}
                ),
            },
            headers=headers,
        )
        assert bound.status_code == 201, bound.text

    mapped = api.client.put(
        f"/apps/{app_id}/mapping", json=drill["mapping"], headers=headers
    )
    assert mapped.status_code == 200, mapped.text

    governed = api.client.put(
        f"/apps/{app_id}/governance", json={"slos": drill["slos"]}, headers=headers
    )
    assert governed.status_code == 200, governed.text


# --- the acceptance -----------------------------------------------------------


@pytest.mark.req("REQ-038", "REQ-040", "REQ-041", "REQ-042", "REQ-046")
async def test_the_drill_produces_a_corroborated_p0_proposal_that_still_needs_a_human(
    api, quiet_harness, drill
):
    signed = api.signup()
    token = signed["tokens"]["access_token"]
    tenant_id = signed["org"]["id"]
    app_id = drill["application"]["app_id"]
    traffic = drill["traffic"]
    regions = list(drill["mapping"]["regions"])
    expected = drill["expected"]

    onboard(api, token, quiet_harness, drill)

    # -- steady --------------------------------------------------------------
    verdict = None
    for _ in range(traffic["warmup_ticks"]):
        verdict = await tick(
            api,
            quiet_harness,
            tenant_id,
            app_id,
            payments=traffic["payments_per_region_per_tick"],
            journey=traffic["journey"],
            regions=regions,
        )
    assert verdict is not None
    assert verdict.stance.value == "steady", verdict.reasons
    assert verdict.severity is None, "nothing is proposed about a healthy application"
    assert verdict.health.band == "healthy", verdict.health.detail

    # -- the fault -----------------------------------------------------------
    injection = inject(quiet_harness, drill)
    assert injection["scenario_id"] == drill["chaos"]["scenario_id"]

    first = await tick(
        api,
        quiet_harness,
        tenant_id,
        app_id,
        payments=traffic["fault_payments_per_region_per_tick"],
        journey=traffic["journey"],
        regions=regions,
    )
    assert first.stance.value == expected["stance_after_first_fault_tick"], first.reasons
    assert not first.corroboration.corroborated, (
        "one breach is not two witnesses; Watch is the whole point of Watch"
    )

    for _ in range(traffic["fault_ticks"] - 1):
        verdict = await tick(
            api,
            quiet_harness,
            tenant_id,
            app_id,
            payments=traffic["fault_payments_per_region_per_tick"],
            journey=traffic["journey"],
            regions=regions,
        )

    # -- the proposal --------------------------------------------------------
    assert verdict.stance.value == expected["stance_after_sustained_breach"], (
        verdict.reasons
    )
    assert verdict.corroboration.corroborated is expected["corroborated"], (
        verdict.corroboration.reason
    )
    assert set(verdict.corroboration.families) == set(expected["families"]), (
        f"the drill must produce two independent witnesses, got "
        f"{verdict.corroboration.families}"
    )
    assert verdict.corroboration.customer_facing_families, (
        "corroboration needs at least one customer-facing family"
    )

    proposal = verdict.severity
    assert proposal is not None
    assert proposal.severity.value == expected["severity"], (
        f"{proposal.severity.value} proposed, not {expected['severity']}: "
        f"{proposal.detail}"
    )
    assert proposal.impact.regions_affected == expected["regions_affected"]
    assert proposal.impact.journey_tier == expected["journey_tier"]
    assert proposal.impact.customer_impacting is True
    assert proposal.impact.success_rate_pct is not None
    assert proposal.impact.success_rate_pct < 80.0
    assert proposal.requires_human is expected["requires_human"]

    # -- evidence refs point at measurements ---------------------------------
    kinds = {ref.kind for ref in proposal.evidence}
    for kind in expected["evidence_kinds"]:
        assert kind in kinds, f"the proposal carries no {kind} evidence: {kinds}"
    refs = {ref.ref for ref in proposal.evidence}
    for region in regions:
        assert f"rollup:payments-success/{region}" in refs, (
            f"the proposal must cite the measurement it read in {region}: {refs}"
        )

    # -- read back over HTTP, which is what a screen sees ---------------------
    body = api.client.get(
        f"/apps/{app_id}/detection", headers=api.auth_header(token)
    ).json()
    assert body["stance"] == "proposed"
    assert body["severity"]["severity"] == expected["severity"]
    assert body["severity"]["declared"] is expected["declared"]
    assert body["severity"]["requires_human"] is expected["requires_human"]
    assert body["corroboration"]["corroborated"] is True

    # -- and detection has still declared nothing ----------------------------
    listed = api.client.get("/incidents", headers=api.auth_header(token)).json()
    assert listed["count"] == 0, (
        "detection reached a P0 proposal and stopped; only a person declares"
    )

    # -- the human act -------------------------------------------------------
    #
    # By a person who holds `declare_incident`, and by nobody else: the org
    # owner who onboarded the application does not hold it and is refused, which
    # is the frozen roles matrix doing its job in the middle of a P0.
    refused = api.client.post(
        f"/incidents?app_id={app_id}", json={}, headers=api.auth_header(token)
    )
    assert refused.status_code == 403, refused.text

    ic = api.invite_and_accept(
        token,
        email="ic@acme.io",
        roles=["incident_commander"],
        display_name="Ida Commander",
        password="ic-strong-password",
    )
    ic_token = ic["tokens"]["access_token"]
    declared = api.client.post(
        f"/incidents?app_id={app_id}", json={}, headers=api.auth_header(ic_token)
    )
    assert declared.status_code == 201, declared.text
    incident = declared.json()
    assert incident["severity"] == expected["severity"]
    assert incident["severity_source"] == "proposed", (
        "the IC accepted what the matrix proposed rather than overriding it - "
        "which is the whole difference this drill exists to demonstrate"
    )
    assert incident["overrode_detection"] is False
    assert incident["declared_by_human"] is True
    assert incident["declared_by"] == ic["user"]["id"]
    assert incident["state"] == "active"
    assert incident["proposal"]["corroborated"] is True
    assert set(incident["proposal"]["families"]) == set(expected["families"])
