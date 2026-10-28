"""S3-T04: direction-correct connection tests (REQ-036, REQ-031).

A pull integration is tested by reading it, an invoke integration by reading the
manifest it publishes and *never* by executing anything, and a push integration
by looking at what has actually arrived - because SignalForge is the server for
a push binding and would always answer its own ping.

The failure states are tested as carefully as the success ones: a target
answering 500, a rejected credential and a disabled connector each land somewhere
different, because the operator's next action is different in each case.
"""

import pytest

from signalforge.tests.ingest.conftest import batch, issue_key, alert
from signalforge.tests.ingest.conftest import Sender
from signalforge.tests.onboarding.conftest import (  # noqa: F401
    APP_ID,
    ObserveHarness,
    connect,
    observe,
    owner,
    register_app,
    topics,
)


def run_tests(api, token, app_id=APP_ID, *, binding_id=None, expect=200) -> list[dict]:
    body = {} if binding_id is None else {"binding_id": binding_id}
    response = api.client.post(
        f"/apps/{app_id}/connection-tests", json=body, headers=api.auth_header(token)
    )
    assert response.status_code == expect, response.text
    return response.json()


def only(results: list[dict], adapter_id: str) -> dict:
    return next(result for result in results if result["adapter_id"] == adapter_id)


@pytest.mark.req("REQ-036")
def test_a_pull_connector_is_tested_by_reading_it(api, observe):
    token, _tenant_id = owner(api)
    register_app(api, token)
    connect(api, token, "http-observe-metrics", app_id=APP_ID)

    result = only(run_tests(api, token), "http-observe-metrics")
    assert result["outcome"] == "passed"
    assert result["direction"] == "inbound (SignalForge polls)"
    assert "metric series" in result["detail"]
    assert [check["name"] for check in result["checks"]] == [
        "reachable", "query_instant"
    ]


@pytest.mark.req("REQ-036")
def test_each_kind_is_read_with_its_own_capability(api, observe):
    token, _tenant_id = owner(api)
    register_app(api, token)
    for adapter_id in (
        "http-observe-logs",
        "http-observe-traces",
        "http-observe-health",
        "topo-import",
    ):
        connect(api, token, adapter_id, app_id=APP_ID)

    results = {result["adapter_id"]: result for result in run_tests(api, token)}
    assert all(result["outcome"] == "passed" for result in results.values())
    assert "log record" in results["http-observe-logs"]["detail"]
    assert "span" in results["http-observe-traces"]["detail"]
    assert "health is degraded" in results["http-observe-health"]["detail"]
    assert "payment-gateway" in results["http-observe-health"]["detail"]
    assert "3 node(s)" in results["topo-import"]["detail"]


@pytest.mark.req("REQ-036", "REQ-030")
def test_an_invoke_connector_is_tested_without_executing_anything(api, observe):
    token, _tenant_id = owner(api)
    register_app(api, token)
    connect(api, token, "http-observe-actions", app_id=APP_ID)

    result = only(run_tests(api, token), "http-observe-actions")
    assert result["outcome"] == "passed"
    assert result["direction"] == "outbound (SignalForge calls)"
    assert result["detail"] == "published 2 action(s)"
    assert observe.state.executions == {}, "a test never changes the target"
    assert observe.state.count("/observe/actions") >= 1


@pytest.mark.req("REQ-036")
def test_a_simulated_connector_says_so(api, observe):
    token, _tenant_id = owner(api)
    register_app(api, token)
    connect(api, token, "itsm-lite", app_id=APP_ID)

    result = only(run_tests(api, token), "itsm-lite")
    assert result["simulated"] is True
    assert result["outcome"] == "passed"


@pytest.mark.req("REQ-036")
def test_a_target_that_answers_500_is_a_named_failure(api, observe):
    token, _tenant_id = owner(api)
    register_app(api, token)
    connect(api, token, "http-observe-metrics", app_id=APP_ID)
    observe.state.fail("/observe/metrics", 500)

    result = only(run_tests(api, token), "http-observe-metrics")
    assert result["outcome"] == "failed"
    assert "unreachable" in result["detail"]
    assert "500" in result["detail"]
    assert result["checks"][0]["outcome"] == "failed"


@pytest.mark.req("REQ-036", "REQ-015")
def test_a_rejected_credential_is_distinguished_from_a_dead_target(api, observe):
    token, _tenant_id = owner(api)
    register_app(api, token)
    connect(
        api,
        token,
        "http-observe-metrics",
        app_id=APP_ID,
        credentials={"token": "not-the-right-token"},
    )

    result = only(run_tests(api, token), "http-observe-metrics")
    assert result["outcome"] == "failed"
    assert "credentials rejected" in result["detail"]
    assert "not-the-right-token" not in result["detail"], (
        "a test result never carries a credential value"
    )


@pytest.mark.req("REQ-036")
def test_an_unreachable_host_fails_with_the_reason(api, observe):
    token, _tenant_id = owner(api)
    register_app(api, token)
    connect(
        api,
        token,
        "http-observe-metrics",
        app_id=APP_ID,
        config={"base_url": "http://nothing-here.test"},
    )

    result = only(run_tests(api, token), "http-observe-metrics")
    assert result["outcome"] == "failed"
    assert "unreachable" in result["detail"]


@pytest.mark.req("REQ-036")
def test_a_disabled_connector_is_skipped_not_failed(api, observe):
    token, _tenant_id = owner(api)
    register_app(api, token)
    binding = connect(api, token, "http-observe-metrics", app_id=APP_ID)
    api.client.patch(
        f"/connectors/{binding['id']}",
        json={"enabled": False},
        headers=api.auth_header(token),
    )

    result = only(run_tests(api, token), "http-observe-metrics")
    assert result["outcome"] == "skipped"
    assert "disabled" in result["detail"]


@pytest.mark.req("REQ-036", "REQ-032")
def test_a_push_connector_reads_pending_until_something_arrives(api, observe):
    token, _tenant_id = owner(api)
    register_app(api, token)
    binding = connect(api, token, "grafana-alert-webhook", app_id=APP_ID, config={})

    no_key = only(run_tests(api, token), "grafana-alert-webhook")
    assert no_key["outcome"] == "pending"
    assert no_key["direction"] == "inbound (the app posts)"
    assert "no ingest key" in no_key["detail"]

    key = issue_key(
        api, token, stream="alerts", app_id=APP_ID, binding_id=binding["id"]
    )
    issued = only(run_tests(api, token), "grafana-alert-webhook")
    assert issued["outcome"] == "pending"
    assert "nothing has been posted yet" in issued["detail"]

    sender = Sender(api=api, key=key)
    posted = sender.post(batch(alert("a-1")))
    assert posted.status_code == 202, posted.text

    receiving = only(run_tests(api, token), "grafana-alert-webhook")
    assert receiving["outcome"] == "passed"
    assert "1 item(s) accepted" in receiving["detail"]
    assert [check["name"] for check in receiving["checks"]] == [
        "ingest key", "freshness"
    ]


@pytest.mark.req("REQ-036")
def test_a_test_run_is_on_the_timeline_and_in_the_audit_chain(api, observe):
    token, tenant_id = owner(api)
    register_app(api, token)
    connect(api, token, "http-observe-metrics", app_id=APP_ID)

    run_tests(api, token)

    events = topics(api, token, "system.connector.tested")
    assert len(events) == 1
    assert events[0]["payload"]["outcome"] == "passed"
    assert events[0]["payload"]["mode"] == "pull"
    assert "system.connector.tested" in api.audit_topics(tenant_id)


@pytest.mark.req("REQ-036")
def test_one_connector_can_be_tested_on_its_own(api, observe):
    token, _tenant_id = owner(api)
    register_app(api, token)
    metrics = connect(api, token, "http-observe-metrics", app_id=APP_ID)
    connect(api, token, "http-observe-health", app_id=APP_ID)

    results = run_tests(api, token, binding_id=metrics["id"])
    assert [result["adapter_id"] for result in results] == ["http-observe-metrics"]
