"""S3-T04: auto-discovery (REQ-036, REQ-030).

Discovery asks two questions without a credential: which parts of the frozen
observe contract does this application serve, and what monitoring is already
running on this machine. It answers with suggestions built from the registry -
never with a binding, and never with an adapter id compiled into the core.
"""

import pytest

from signalforge.domain.onboarding.discovery import LocalService
from signalforge.tests.onboarding.conftest import (  # noqa: F401
    APP_ID,
    ObserveHarness,
    connect,
    observe,
    owner,
    register_app,
    topics,
)

OBSERVE_KINDS = {
    "observe:metrics",
    "observe:logs",
    "observe:traces",
    "observe:health",
    "observe:topology",
    "observe:actions",
    "observe:chaos",
}


def discover(api, token, app_id=APP_ID, *, expect=200) -> dict:
    response = api.client.post(
        f"/apps/{app_id}/discover", json={}, headers=api.auth_header(token)
    )
    assert response.status_code == expect, response.text
    return response.json()


@pytest.mark.req("REQ-036")
def test_discovery_finds_every_observe_endpoint_the_application_serves(api, observe):
    token, _tenant_id = owner(api)
    register_app(api, token)

    report = discover(api, token)
    found = {finding["key"] for finding in report["findings"] if finding["present"]}
    assert OBSERVE_KINDS <= found
    assert report["base_url"] == ObserveHarness.BASE_URL


@pytest.mark.req("REQ-036")
def test_an_endpoint_that_wants_a_credential_counts_as_served(api, observe):
    """The test double protects /observe/*, so an unauthenticated probe gets a
    401. That is the answer a correctly configured application gives, and
    treating it as absence would make discovery useless against exactly those."""
    token, _tenant_id = owner(api)
    register_app(api, token)

    report = discover(api, token)
    metrics = next(f for f in report["findings"] if f["key"] == "observe:metrics")
    assert metrics["status_code"] == 401
    assert metrics["guarded"] is True and metrics["present"] is True
    assert "wants a credential" in metrics["detail"]


@pytest.mark.req("REQ-036")
def test_the_basics_base_url_prefills_the_suggested_adapter_config(api, observe):
    token, _tenant_id = owner(api)
    register_app(api, token)

    report = discover(api, token)
    metrics = next(f for f in report["findings"] if f["key"] == "observe:metrics")
    candidates = {c["adapter_id"]: c for c in metrics["candidates"]}
    assert "http-observe-metrics" in candidates

    suggestion = candidates["http-observe-metrics"]
    assert suggestion["suggested_config"] == {"base_url": ObserveHarness.BASE_URL}
    assert suggestion["action"] == "bind"
    assert suggestion["credential_fields"] == ["token"]
    assert suggestion["already_bound"] is False
    assert suggestion["runnable"] is True


@pytest.mark.req("REQ-036")
def test_an_adapter_already_bound_is_not_suggested_again(api, observe):
    token, _tenant_id = owner(api)
    register_app(api, token)
    connect(api, token, "http-observe-metrics", app_id=APP_ID)

    report = discover(api, token)
    metrics = next(f for f in report["findings"] if f["key"] == "observe:metrics")
    suggestion = next(
        c for c in metrics["candidates"] if c["adapter_id"] == "http-observe-metrics"
    )
    assert suggestion["already_bound"] is True


@pytest.mark.req("REQ-036")
def test_an_unreachable_application_finds_nothing_and_suggests_nothing(api, observe):
    token, _tenant_id = owner(api)
    register_app(api, token, app_id="ghost-app", base_url="http://ghost.test")

    report = discover(api, token, "ghost-app")
    assert report["found"] == 0
    assert all(finding["candidates"] == [] for finding in report["findings"])
    assert all(
        "unreachable" in finding["detail"] for finding in report["findings"]
    )


@pytest.mark.req("REQ-036")
def test_local_monitoring_is_matched_by_vendor_not_by_adapter_id(api, observe):
    token, _tenant_id = owner(api)
    register_app(api, token)
    api.container.discovery.local_services = (
        LocalService(
            vendor="prometheus",
            label="Prometheus",
            url=ObserveHarness.PROMETHEUS_URL,
            health_path="/api/v1/query",
        ),
    )

    report = discover(api, token)
    local = next(f for f in report["findings"] if f["source"] == "local")
    assert local["present"] is True
    assert [c["adapter_id"] for c in local["candidates"]] == ["prometheus"]
    assert local["candidates"][0]["suggested_config"] == {
        "base_url": ObserveHarness.PROMETHEUS_URL
    }


@pytest.mark.req("REQ-036")
def test_a_push_adapter_is_suggested_as_a_key_not_as_a_binding(api, observe):
    """Direction matters in the suggestion too: Grafana posts to SignalForge, so
    what discovery offers is an ingest key and the path to configure, not a
    connector to poll."""
    token, _tenant_id = owner(api)
    register_app(api, token)
    api.container.discovery.local_services = (
        LocalService(
            vendor="grafana",
            label="Grafana",
            url=ObserveHarness.BASE_URL,
            health_path="/observe/health",
        ),
    )

    report = discover(api, token)
    local = next(f for f in report["findings"] if f["source"] == "local")
    suggestion = next(
        c for c in local["candidates"] if c["adapter_id"] == "grafana-alert-webhook"
    )
    assert suggestion["action"] == "issue_ingest_key"
    assert suggestion["stream"] == "alerts"
    assert suggestion["ingest_path"] == "/api/v1/ingest/alerts"


@pytest.mark.req("REQ-036")
def test_a_discovery_run_writes_evidence_and_no_binding(api, observe):
    token, tenant_id = owner(api)
    register_app(api, token)

    discover(api, token)

    events = topics(api, token, "system.app.discovered")
    assert len(events) == 1
    payload = events[0]["payload"]
    assert payload["found"] >= len(OBSERVE_KINDS)
    assert "http-observe-metrics" in payload["suggested_adapters"]
    assert "system.app.discovered" in api.audit_topics(tenant_id)

    connectors = api.client.get("/connectors", headers=api.auth_header(token)).json()
    assert connectors == [], "discovery proposes; the operator decides"


@pytest.mark.req("REQ-036")
def test_discovery_without_a_base_url_probes_only_the_local_services(api, observe):
    token, _tenant_id = owner(api)
    register_app(api, token, app_id="no-url-app", base_url=None)
    api.container.discovery.local_services = (
        LocalService(
            vendor="prometheus",
            label="Prometheus",
            url=ObserveHarness.PROMETHEUS_URL,
            health_path="/api/v1/query",
        ),
    )

    report = discover(api, token, "no-url-app")
    assert report["base_url"] is None
    assert [finding["source"] for finding in report["findings"]] == ["local"]
