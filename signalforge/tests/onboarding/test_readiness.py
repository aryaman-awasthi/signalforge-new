"""S3-T04: the test page and the readiness score (REQ-036, REQ-038).

The acceptance question for this sprint is whether an application can be onboarded
end to end through public contracts. This file answers it against an application
that implements only the frozen observe contract: seven steps, no seeds, no
app-specific code, and a readiness score of 100 at the end.

The score itself is pinned as a pure function too, because a percentage that
drifts as checks are added is not comparable between two applications.
"""

import pytest

from signalforge.domain.onboarding.readiness import (
    READY_THRESHOLD,
    ReadinessInputs,
    evaluate,
)
from signalforge.tests.onboarding.conftest import (  # noqa: F401
    APP_ID,
    SIGNAL_BODY,
    SIMPLE_TOPOLOGY,
    connect,
    import_actions,
    observe,
    owner,
    put_governance,
    put_mapping,
    register_app,
    topics,
    upload_topology,
)

SLO = {
    "slo_id": "checkout-availability",
    "name": "Checkout availability",
    "objective_pct": 99.9,
    "journey_id": "checkout",
    "indicator": "checkout_success_ratio",
}

PULL_ADAPTERS = (
    "http-observe-metrics",
    "http-observe-health",
    "http-observe-actions",
    "topo-import",
)


def onboard_fully(api, token) -> None:
    register_app(api, token, tier="tier_1", owner_team="Payments")
    for adapter_id in PULL_ADAPTERS:
        connect(api, token, adapter_id, app_id=APP_ID)
    put_mapping(api, token, **SIGNAL_BODY)
    import_actions(api, token)
    upload_topology(api, token, SIMPLE_TOPOLOGY)
    put_governance(api, token, slos=[SLO])
    api.client.post(
        f"/apps/{APP_ID}/baseline", json={"samples": 3}, headers=api.auth_header(token)
    )


def readiness(api, token, app_id=APP_ID) -> dict:
    response = api.client.get(
        f"/apps/{app_id}/readiness", headers=api.auth_header(token)
    )
    assert response.status_code == 200, response.text
    return response.json()


def run_test_page(api, token, app_id=APP_ID) -> dict:
    response = api.client.post(
        f"/apps/{app_id}/test", headers=api.auth_header(token)
    )
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.req("REQ-036", "REQ-038")
def test_an_application_serving_only_the_observe_contract_reaches_full_readiness(
    api, observe
):
    token, tenant_id = owner(api)
    onboard_fully(api, token)

    report = run_test_page(api, token)
    assert report["summary"]["failed"] == 0
    assert report["summary"]["pending"] == 0
    assert report["summary"]["skipped"] == 0
    assert report["summary"]["passed"] == len(PULL_ADAPTERS) + 2
    assert {result["outcome"] for result in report["connectors"]} == {"passed"}
    assert {probe["outcome"] for probe in report["probes"]} == {"passed"}

    final = report["readiness"]
    assert final["score"] == 100
    assert final["ready"] is True
    assert final["outstanding"] == []
    assert readiness(api, token)["score"] == 100

    events = topics(api, token, "system.app.tested")
    assert events[-1]["payload"]["passed"] == report["summary"]["passed"]
    assert "system.app.tested" in api.audit_topics(tenant_id)


@pytest.mark.req("REQ-036")
def test_the_stepper_can_show_progress_per_wizard_step(api, observe):
    token, _tenant_id = owner(api)
    register_app(api, token)
    put_mapping(api, token, **SIGNAL_BODY)

    steps = readiness(api, token)["steps"]
    assert steps["basics"] == [10, 10]
    assert steps["map"] == [30, 35], "everything but the imported actions"
    assert steps["connect"] == [0, 20]
    assert steps["topology"] == [0, 10]
    assert sum(available for _earned, available in steps.values()) == 100


@pytest.mark.req("REQ-036")
def test_a_failing_connector_keeps_the_test_page_honest(api, observe):
    token, _tenant_id = owner(api)
    onboard_fully(api, token)
    observe.state.fail("/observe/metrics", 500)

    report = run_test_page(api, token)
    assert report["summary"]["failed"] == 1
    assert report["readiness"]["ready"] is False

    checks = {check["key"]: check for check in report["readiness"]["checks"]}
    assert checks["connect.healthy"]["passed"] is False
    assert checks["test.green"]["passed"] is False
    assert report["readiness"]["outstanding"][0] == "connect.healthy", (
        "a blocking check is what to fix first, whatever the score says"
    )


@pytest.mark.req("REQ-036")
def test_a_connection_test_writes_its_verdict_onto_the_binding(api, observe):
    """The integration health screen reads the binding, not the test result, so
    an explicit test has to answer there immediately rather than leaving the row
    `unknown` until the next health poll."""
    token, _tenant_id = owner(api)
    register_app(api, token)
    connect(api, token, "http-observe-metrics", app_id=APP_ID)

    before = api.client.get(
        "/connectors/health", headers=api.auth_header(token)
    ).json()[0]
    assert before["status"] == "unknown"

    api.client.post(
        f"/apps/{APP_ID}/connection-tests", json={}, headers=api.auth_header(token)
    )

    after = api.client.get(
        "/connectors/health", headers=api.auth_header(token)
    ).json()[0]
    assert after["status"] == "up"
    assert "metric series" in after["status_detail"]
    assert after["last_ok_at"] is not None


@pytest.mark.req("REQ-036")
def test_probes_are_skipped_with_a_reason_when_nothing_can_run_them(api, observe):
    token, _tenant_id = owner(api)
    register_app(api, token)
    put_mapping(api, token, **SIGNAL_BODY)

    report = run_test_page(api, token)
    assert {probe["outcome"] for probe in report["probes"]} == {"skipped"}
    assert "no enabled health connector" in report["probes"][0]["detail"]
    assert report["summary"]["skipped"] == 2


@pytest.mark.req("REQ-036")
def test_a_probe_that_gets_the_wrong_status_fails_with_what_it_got(api, observe):
    token, _tenant_id = owner(api)
    register_app(api, token)
    put_mapping(api, token, **SIGNAL_BODY)
    connect(api, token, "http-observe-health", app_id=APP_ID)
    observe.state.fail("/checkout", 503)

    report = run_test_page(api, token)
    assert {probe["outcome"] for probe in report["probes"]} == {"failed"}
    assert report["probes"][0]["status_code"] == 503
    assert "expected 200" in report["probes"][0]["detail"]


# --- the score as a pure function --------------------------------------------


@pytest.mark.req("REQ-036")
def test_the_weights_sum_to_one_hundred_and_every_check_is_actionable():
    readiness = evaluate(ReadinessInputs())
    assert readiness.total_weight == 100
    assert readiness.score == 0
    assert all(check.detail for check in readiness.checks)
    assert len({check.key for check in readiness.checks}) == len(readiness.checks)


@pytest.mark.req("REQ-036")
def test_a_high_score_can_never_mean_ready_while_a_blocking_check_is_red():
    almost = evaluate(
        ReadinessInputs(
            base_url_set=True,
            connectors_enabled=2,
            connectors_healthy=2,
            signals=3,
            business_signals=1,
            region_label_set=True,
            journeys=2,
            probes=2,
            actions_imported=4,
            topology_version="sha256:abc",
            slos=0,
            severity_configured=True,
            baselines=3,
            last_test_all_green=True,
        )
    )
    assert almost.score == 90 and almost.score >= READY_THRESHOLD
    assert almost.ready is False
    assert [check.key for check in almost.blocking_failures] == ["slos.targets"]
    assert almost.outstanding[0].key == "slos.targets", "blocking first"
