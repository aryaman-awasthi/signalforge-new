"""S3-T04: the Basics step (REQ-036).

Registering an application is the only step that creates anything, and it creates
four things in one commit: the application, its onboarding profile with the
policy floor already stored, the audit entry and the timeline event. The base URL
it captures is what every later step resolves against.
"""

import pytest

from signalforge.tests.onboarding.conftest import (  # noqa: F401
    APP_ID,
    ObserveHarness,
    connect,
    observe,
    owner,
    register_app,
    topics,
)


@pytest.mark.req("REQ-036")
def test_registering_an_application_stores_the_floor_with_it(api, observe):
    token, tenant_id = owner(api)
    created = register_app(api, token, owner_team="Payments", tier="tier_1")

    assert created["application"]["app_id"] == APP_ID
    assert created["application"]["base_url"] == ObserveHarness.BASE_URL
    assert created["application"]["tier"] == "tier_1"

    policy = created["profile"]["policy"]
    assert policy["auto_execute_low_risk"] is False
    assert policy["approval_ttl_s"] <= 3_600
    assert set(policy["require_approval_classes"]) >= {
        "rollback", "restart", "failover", "traffic_shift",
        "config_change", "customer_impacting",
    }
    assert [rule["severity"] for rule in created["profile"]["severity_matrix"]["rules"]] == [
        "P0", "P1", "P2"
    ]

    assert "system.app.registered" in api.audit_topics(tenant_id)
    assert len(topics(api, token, "system.app.registered")) == 1


@pytest.mark.req("REQ-036")
def test_readiness_starts_low_and_names_what_is_missing(api, observe):
    token, _tenant_id = owner(api)
    created = register_app(api, token)

    readiness = created["readiness"]
    assert readiness["total_weight"] == 100
    assert readiness["score"] == 15, (
        "the base URL, plus the severity matrix the floor ships already configured"
    )
    assert readiness["ready"] is False
    assert "connect.bound" in readiness["outstanding"]
    blocking = [check for check in readiness["checks"] if check["blocking"]]
    assert {check["key"] for check in blocking} == {
        "basics.base_url",
        "connect.bound",
        "connect.healthy",
        "map.signals",
        "slos.targets",
    }


@pytest.mark.req("REQ-036")
def test_app_id_and_base_url_are_validated(api, observe):
    token, _tenant_id = owner(api)

    refused = register_app(api, token, app_id="Not A Slug", expect=422)
    assert "kebab-case" in refused["detail"]

    hostile = register_app(
        api, token, app_id="ok-app", base_url="file:///etc/passwd", expect=422
    )
    assert "http(s)" in hostile["detail"]

    with_query = register_app(
        api,
        token,
        app_id="ok-app",
        base_url="http://app.test/?admin=1",
        expect=422,
    )
    assert "root URL" in with_query["detail"]


@pytest.mark.req("REQ-036")
def test_registering_the_same_app_id_twice_is_a_conflict(api, observe):
    token, _tenant_id = owner(api)
    register_app(api, token)
    again = register_app(api, token, expect=409)
    assert APP_ID in again["detail"]


@pytest.mark.req("REQ-036")
def test_editing_basics_records_that_the_base_url_moved(api, observe):
    token, token_tenant = owner(api)
    register_app(api, token)

    response = api.client.patch(
        f"/apps/{APP_ID}",
        json={"base_url": "http://elsewhere.test", "name": "Checkout v2"},
        headers=api.auth_header(token),
    )
    assert response.status_code == 200, response.text
    assert response.json()["application"]["base_url"] == "http://elsewhere.test"

    events = topics(api, token, "system.app.updated")
    assert len(events) == 1
    assert events[0]["payload"]["base_url_changed"] is True
    assert events[0]["payload"]["previous_base_url"] == ObserveHarness.BASE_URL


@pytest.mark.req("REQ-036")
def test_an_application_cannot_be_removed_while_integrations_point_at_it(api, observe):
    token, _tenant_id = owner(api)
    register_app(api, token)
    binding = connect(api, token, "http-observe-metrics", app_id=APP_ID)

    refused = api.client.delete(f"/apps/{APP_ID}", headers=api.auth_header(token))
    assert refused.status_code == 409
    assert "connector" in refused.json()["detail"]

    assert (
        api.client.delete(
            f"/connectors/{binding['id']}", headers=api.auth_header(token)
        ).status_code
        == 204
    )
    removed = api.client.delete(f"/apps/{APP_ID}", headers=api.auth_header(token))
    assert removed.status_code == 204
    assert (
        api.client.get(f"/apps/{APP_ID}", headers=api.auth_header(token)).status_code
        == 404
    )


@pytest.mark.req("REQ-036")
def test_the_apps_overview_carries_a_readiness_score_per_application(api, observe):
    token, _tenant_id = owner(api)
    register_app(api, token, app_id="checkout", name="Checkout")
    register_app(api, token, app_id="ledger", name="Ledger", base_url=None)

    listing = api.client.get("/apps", headers=api.auth_header(token)).json()
    by_id = {app["app_id"]: app for app in listing}
    assert by_id["checkout"]["readiness_score"] == 15
    assert by_id["ledger"]["readiness_score"] == 5, "no base URL, matrix defaulted"
    assert by_id["ledger"]["base_url"] is None
    assert all(app["ready"] is False for app in listing)
