"""S3-T04: the Map step (REQ-036).

Golden signals including business counters, the region label selector, journeys,
probe templates and the actions imported from the application's own manifest.

The mapping is where an operator describes the application in the platform's
vocabulary, so the validation is deliberately strict: a selector that could never
match, a probe measuring a journey nobody declared, or a region label that is not
a label name are all refused while somebody is looking at the field.
"""

import copy

import pytest

from signalforge.tests.onboarding.conftest import (  # noqa: F401
    APP_ID,
    SIGNAL_BODY,
    connect,
    import_actions,
    observe,
    owner,
    patch_action,
    put_mapping,
    register_app,
    topics,
)


@pytest.mark.req("REQ-036")
def test_golden_signals_business_counters_regions_journeys_and_probes(api, observe):
    token, tenant_id = owner(api)
    register_app(api, token)

    profile = put_mapping(api, token, **SIGNAL_BODY)

    assert [signal["key"] for signal in profile["signals"]] == [
        "errors", "payments-success"
    ]
    business = next(
        signal for signal in profile["signals"] if signal["business_counter"]
    )
    assert business["signal"] == "business"
    assert business["direction"] == "lower_is_worse"
    assert profile["region_label"] == "region"
    assert profile["regions"] == ["eu-west-1", "us-east-1"]
    assert profile["journeys"][0]["tier"] == 1
    assert profile["probes"][0]["synthetic_header"] == "X-SignalForge-Synthetic"
    assert profile["can_corroborate"] is True, (
        "two families with one customer-facing is what corroboration needs"
    )

    events = topics(api, token, "system.app.mapping.updated")
    assert len(events) == 1
    assert sorted(events[0]["payload"]["families"]) == ["business", "errors"]
    assert "system.app.mapping.updated" in api.audit_topics(tenant_id)


@pytest.mark.req("REQ-036")
def test_a_selector_that_could_never_match_is_refused(api, observe):
    token, _tenant_id = owner(api)
    register_app(api, token)

    refused = put_mapping(
        api,
        token,
        signals=[
            {
                "key": "bad",
                "signal": "errors",
                "selector": 'rate(http_requests_total[5m]) > 1',
                "direction": "higher_is_worse",
            }
        ],
        expect=422,
    )
    assert "selector" in refused["detail"]

    empty = put_mapping(
        api,
        token,
        signals=[
            {
                "key": "blank",
                "signal": "errors",
                "selector": "  ",
                "direction": "higher_is_worse",
            }
        ],
        expect=422,
    )
    assert "matches every series" in empty["detail"]


@pytest.mark.req("REQ-036")
def test_a_probe_cannot_measure_a_journey_nobody_declared(api, observe):
    token, _tenant_id = owner(api)
    register_app(api, token)

    body = copy.deepcopy(SIGNAL_BODY)
    body["probes"][0]["journey_id"] = "not-a-journey"
    refused = put_mapping(api, token, **body, expect=422)
    assert "not-a-journey" in refused["detail"]


@pytest.mark.req("REQ-036")
def test_the_region_label_must_be_a_metric_label_name(api, observe):
    token, _tenant_id = owner(api)
    register_app(api, token)

    refused = put_mapping(api, token, region_label="region name!", expect=422)
    assert "label name" in refused["detail"]


@pytest.mark.req("REQ-036")
def test_a_business_counter_belongs_to_the_business_family(api, observe):
    token, _tenant_id = owner(api)
    register_app(api, token)

    refused = put_mapping(
        api,
        token,
        signals=[
            {
                "key": "mislabelled",
                "signal": "latency",
                "selector": "request_latency_seconds",
                "direction": "higher_is_worse",
                "business_counter": True,
            }
        ],
        expect=422,
    )
    assert "business" in refused["detail"]


@pytest.mark.req("REQ-036")
def test_a_probe_template_fans_out_across_the_declared_regions(api, observe):
    token, _tenant_id = owner(api)
    register_app(api, token)
    put_mapping(api, token, **SIGNAL_BODY)
    connect(api, token, "http-observe-health", app_id=APP_ID)

    report = api.client.post(
        f"/apps/{APP_ID}/test", headers=api.auth_header(token)
    ).json()
    assert [probe["probe_id"] for probe in report["probes"]] == [
        "checkout-probe:eu-west-1",
        "checkout-probe:us-east-1",
    ]
    assert all(probe["outcome"] == "passed" for probe in report["probes"])


@pytest.mark.req("REQ-036")
def test_actions_are_imported_from_the_manifest_with_their_declared_risk(api, observe):
    token, tenant_id = owner(api)
    register_app(api, token)
    connect(api, token, "http-observe-actions", app_id=APP_ID)

    result = import_actions(api, token)
    assert result["imported"] == 2 and result["refreshed"] == 0
    assert result["adapter_id"] == "http-observe-actions"

    restart = next(
        action for action in result["actions"] if action["action_id"] == "restart-billing"
    )
    assert restart["manifest_risk"] == "high"
    assert restart["action_class"] == "restart"
    assert restart["approver_roles"] == ["incident_commander"]

    events = topics(api, token, "system.app.actions.updated")
    assert events[0]["payload"]["high_risk"] == ["restart-billing"]
    assert "system.app.actions.updated" in api.audit_topics(tenant_id)


@pytest.mark.req("REQ-036")
def test_a_reimport_refreshes_the_manifest_and_keeps_the_operators_decision(api, observe):
    token, _tenant_id = owner(api)
    register_app(api, token)
    connect(api, token, "http-observe-actions", app_id=APP_ID)
    import_actions(api, token)

    patch_action(
        api, token, "restart-billing", approver_roles=["tech_lead"], enabled=False
    )
    patch_action(api, token, "collect-diagnostics", risk_override="medium")

    observe.state.actions["actions"][0]["name"] = "Restart billing (rolling)"
    again = import_actions(api, token)
    assert again["imported"] == 0 and again["refreshed"] == 2

    by_id = {action["action_id"]: action for action in again["actions"]}
    assert by_id["restart-billing"]["name"] == "Restart billing (rolling)"
    assert by_id["restart-billing"]["approver_roles"] == ["tech_lead"]
    assert by_id["restart-billing"]["enabled"] is False
    assert by_id["collect-diagnostics"]["risk_override"] == "medium"


@pytest.mark.req("REQ-036")
def test_an_action_withdrawn_from_the_manifest_is_dropped(api, observe):
    token, _tenant_id = owner(api)
    register_app(api, token)
    connect(api, token, "http-observe-actions", app_id=APP_ID)
    import_actions(api, token)

    observe.state.actions["actions"] = observe.state.actions["actions"][:1]
    again = import_actions(api, token)
    assert again["withdrawn"] == 1
    assert [action["action_id"] for action in again["actions"]] == ["restart-billing"]


@pytest.mark.req("REQ-036")
def test_importing_without_an_actions_connector_says_what_to_do(api, observe):
    token, _tenant_id = owner(api)
    register_app(api, token)

    refused = import_actions(api, token, expect=422)
    assert "no enabled actions connector" in refused["detail"]


@pytest.mark.req("REQ-036")
def test_overriding_an_action_that_was_never_imported_is_refused(api, observe):
    token, _tenant_id = owner(api)
    register_app(api, token)

    refused = patch_action(api, token, "ghost-action", enabled=False, expect=422)
    assert "not imported" in refused["detail"]


@pytest.mark.req("REQ-036")
def test_a_failing_manifest_read_is_reported_as_the_targets_failure(api, observe):
    token, _tenant_id = owner(api)
    register_app(api, token)
    connect(api, token, "http-observe-actions", app_id=APP_ID)
    observe.state.fail("/observe/actions", 503)

    response = api.client.post(
        f"/apps/{APP_ID}/actions/import", json={}, headers=api.auth_header(token)
    )
    assert response.status_code == 502, response.text
    assert response.json()["error"] == "AdapterUnavailableError"
