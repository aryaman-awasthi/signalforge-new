"""The Spring Actuator family against an application nobody changed (REQ-031).

Every test goes the whole way: sign up, bind the adapter through the API (so the
config is schema-validated and the binding is real), then call it through the
ConnectorInvoker (so the credential-scope floor, the manifest timeout and the
metrics are all real). On the other end is a Starlette app serving the two
endpoints Actuator serves and nothing else - no observe contract, no action
manifest - because that is the claim under test: a Spring Boot application with
Micrometer is onboardable as it stands.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from signalforge.contracts.canonical import HealthStatus, ProbeSpec
from signalforge.contracts.errors import (
    AdapterTimeoutError,
    AdapterUnavailableError,
    NotSupportedError,
)
from signalforge.tests.adapters.conftest import connect

pytestmark = pytest.mark.anyio

AT = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)
WINDOW = (AT - timedelta(minutes=5), AT)

BOOT_URL = "http://boot.test"
DEAD_URL = "http://nothing-listening.test"

METRICS = "spring-actuator-metrics"
HEALTH = "spring-actuator-health"


@pytest.fixture
def actuator(api, observe):
    """The Boot application, mounted on the harness the adapter suite already
    uses, plus a helper that binds one of the two adapters and calls it."""
    from signalforge.tests.adapters.actuator_app import build_actuator_app

    app, state = build_actuator_app()
    observe.mount(BOOT_URL, app)

    signup = api.signup()
    token = signup["tokens"]["access_token"]
    tenant_id = signup["org"]["id"]

    class Bound:
        def __init__(self):
            self.harness = observe
            self.state = state

        def bind(self, adapter_id, *, base_url=BOOT_URL, **kwargs):
            kwargs.setdefault("config", {"base_url": base_url})
            return connect(api, token, adapter_id, **kwargs)

        async def invoke(self, binding, capability, *args, **kwargs):
            return await api.container.invoker.invoke(
                tenant_id, binding["id"], capability, *args, **kwargs
            )

        def manifest(self, adapter_id):
            return api.container.adapter_registry.get(adapter_id)

        def listing(self):
            body = api.client.get("/adapters", headers=api.auth_header(token)).json()
            return body, {entry["id"]: entry for entry in body["adapters"]}

        async def runtime(self, binding, capability="ping"):
            """The adapter instance itself, for the assertions that are about
            what the runtime does rather than what the invoker allows."""
            resolved = await api.container.invoker.resolve(
                tenant_id, binding["id"], capability
            )
            return resolved.adapter

    return Bound()


# --- the registry --------------------------------------------------------------


@pytest.mark.req("REQ-030", "REQ-031")
async def test_both_manifests_are_discovered_and_ship_a_runtime(actuator):
    """Installed, not merely available. "SignalForge knows what Actuator is" and
    "this deployment can read an Actuator endpoint" must never be the same
    statement, and `status` is where the difference is told."""
    body, by_id = actuator.listing()
    assert body["errors"] == []

    metrics = by_id[METRICS]
    assert metrics["kind"] == "metrics"
    assert metrics["mode"] == "pull"
    assert metrics["vendor"] == "spring"
    assert metrics["availability"] == "built_in"
    assert metrics["status"] == "installed"
    assert metrics["runnable"] is True
    assert metrics["capabilities"] == ["query_instant"]
    assert metrics["unsupported"] == ["query_range"], (
        "the registry has to show what an adapter cannot do, not only what it can"
    )
    assert metrics["auth"]["method"] == "none", "Actuator is unauthenticated by default"
    assert metrics["credential_scopes"] == ["read_only"]
    assert metrics["config_schema"]["required"] == ["base_url"]

    health = by_id[HEALTH]
    assert health["kind"] == "health"
    assert health["status"] == "installed"
    assert health["runnable"] is True
    assert health["capabilities"] == ["get_health", "run_probe"]
    assert health["mutating_capabilities"] == [], "neither adapter can change anything"
    assert health["redaction"]["policy_id"] == "actuator-health-details"


# --- metrics -------------------------------------------------------------------


@pytest.mark.req("REQ-031")
async def test_micrometer_exposition_is_parsed_into_canonical_series(actuator):
    binding = actuator.bind(METRICS)
    series = await actuator.invoke(binding, "query_instant", "orders_placed_total", AT)

    assert {one.name for one in series} == {"orders_placed_total"}
    by_labels = {
        (one.labels["region"], one.labels["status"]): one.points[0].value
        for one in series
    }
    assert by_labels == {
        ("region-a", "200"): 1240.0,
        ("region-a", "500"): 17.0,
        ("region-b", "200"): 860.0,
    }
    assert all(point.ts == AT for one in series for point in one.points)


@pytest.mark.req("REQ-031")
async def test_a_label_selector_returns_only_matching_series_with_their_labels(
    actuator,
):
    binding = actuator.bind(METRICS)
    region_a = await actuator.invoke(
        binding, "query_instant", 'orders_placed_total{region="region-a"}', AT
    )

    assert {one.labels["status"] for one in region_a} == {"200", "500"}
    assert all(one.labels["region"] == "region-a" for one in region_a)
    assert all(one.labels["application"] == "checkout" for one in region_a), (
        "the labels travel with the series; a selector narrows, it does not strip"
    )

    errors = await actuator.invoke(
        binding,
        "query_instant",
        'orders_placed_total{region="region-a",status="500"}',
        AT,
    )
    assert [one.points[0].value for one in errors] == [17.0]

    timer = await actuator.invoke(
        binding, "query_instant", 'http_server_requests_seconds_count{uri="/orders"}', AT
    )
    assert len(timer) == 3, "a timer's count series are three, one per label set"


@pytest.mark.req("REQ-031")
async def test_query_range_is_refused_and_names_the_adapter_that_can(actuator):
    """A scrape has no history. Answering a range from it would be invention.

    Refused twice over: the invoker will not route an undeclared capability at
    all, and the runtime itself raises NotSupportedError as the SPI prescribes.
    """
    from signalforge.domain.adapters.errors import CredentialScopeError

    binding = actuator.bind(METRICS)
    with pytest.raises(CredentialScopeError):
        await actuator.invoke(binding, "query_range", "x", WINDOW[0], WINDOW[1], 15.0)

    adapter = await actuator.runtime(binding)
    with pytest.raises(NotSupportedError) as refused:
        await adapter.query_range("x", WINDOW[0], WINDOW[1], 15.0)
    assert "prometheus" in str(refused.value)


# --- health --------------------------------------------------------------------


@pytest.mark.req("REQ-031")
async def test_spring_status_strings_map_onto_the_canonical_four(actuator):
    binding = actuator.bind(HEALTH)
    report = await actuator.invoke(binding, "get_health")

    by_component = {c.component: c.status for c in report.components}
    assert by_component == {
        "db": HealthStatus.UP,
        "diskSpace": HealthStatus.DOWN,
        "mailSender": HealthStatus.DEGRADED,
        "rateLimiter": HealthStatus.UNKNOWN,
    }, "OUT_OF_SERVICE is degraded; a status Spring never defined is unknown"
    assert report.checked_at == AT, "dated by the platform clock, not by the target"


@pytest.mark.req("REQ-031")
async def test_the_applications_own_aggregate_is_carried_through_not_recomputed(
    actuator,
):
    """The document says UP while one of its components says DOWN. That is the
    application's verdict about itself - a health group can exclude an indicator
    - and an adapter that recomputed it would be deciding, which is the one
    thing an adapter may never do."""
    binding = actuator.bind(HEALTH)
    report = await actuator.invoke(binding, "get_health")

    assert report.status is HealthStatus.UP
    assert HealthStatus.DOWN in {c.status for c in report.components}


@pytest.mark.req("REQ-031")
async def test_a_document_without_components_is_still_a_valid_report(actuator):
    """show-details is off by default, and then Actuator returns the status and
    nothing else. An adapter that needed the components would report every
    default-configured Boot application as broken."""
    binding = actuator.bind(HEALTH)
    actuator.state.hide_details()

    report = await actuator.invoke(binding, "get_health")
    assert report.status is HealthStatus.UP
    assert report.components == []


@pytest.mark.req("REQ-031")
async def test_indicator_details_go_through_the_manifests_redaction(actuator):
    """An indicator's details are whatever the application chose to put there,
    and it chose without knowing the text would be stored, shown and put in
    front of a model."""
    binding = actuator.bind(HEALTH)
    report = await actuator.invoke(binding, "get_health")
    detail = {c.component: c.detail for c in report.components}

    assert "hunter2" not in detail["mailSender"]
    assert "alerts@example.com" not in detail["mailSender"]
    assert detail["mailSender"].count("***") == 2
    assert "smtp.internal:587" in detail["mailSender"], (
        "redaction is targeted, not blanket: the location is what makes the "
        "detail worth reading"
    )
    assert "PostgreSQL" in detail["db"]
    assert detail["rateLimiter"] is not None


@pytest.mark.req("REQ-031")
async def test_a_detail_longer_than_the_ceiling_is_truncated_not_dropped(actuator):
    binding = actuator.bind(HEALTH)
    actuator.state.health = {
        "status": "UP",
        "components": {"cache": {"status": "UP", "details": {"keys": "k" * 5000}}},
    }

    report = await actuator.invoke(binding, "get_health")
    assert len(report.components[0].detail) == 200


@pytest.mark.req("REQ-031")
async def test_a_health_document_of_the_wrong_shape_names_the_endpoint(actuator):
    """Something other than Actuator answering on this path must be an error the
    person reading it can act on, not an empty component list that reads as a
    healthy application."""
    from signalforge.contracts.errors import AdapterError

    binding = actuator.bind(HEALTH)
    actuator.state.health = {"status": "UP", "components": [{"db": "UP"}]}

    with pytest.raises(AdapterError) as refused:
        await actuator.invoke(binding, "get_health")
    assert "/actuator/health" in str(refused.value)


# --- probes --------------------------------------------------------------------


@pytest.mark.req("REQ-031")
async def test_a_probe_reports_its_own_result_rather_than_raising(actuator):
    binding = actuator.bind(HEALTH)
    ok = await actuator.invoke(
        binding, "run_probe", ProbeSpec(probe_id="orders", target="/orders")
    )
    assert ok.ok is True
    assert ok.status_code == 200
    assert ok.latency_ms >= 0

    actuator.state.fail("/orders", 503)
    failed = await actuator.invoke(
        binding, "run_probe", ProbeSpec(probe_id="orders", target="/orders")
    )
    assert failed.ok is False
    assert failed.status_code == 503
    assert "expected 200, got 503" in failed.detail


# --- failure -------------------------------------------------------------------


@pytest.mark.req("REQ-031")
async def test_a_hanging_endpoint_is_a_timeout_not_a_hang_and_not_a_healthy_report(
    actuator,
):
    binding = actuator.bind(HEALTH)
    actuator.manifest(HEALTH).limits.__dict__["timeout_s"] = 0.05
    actuator.harness.times_out("/actuator/health")

    with pytest.raises(AdapterTimeoutError):
        await actuator.invoke(binding, "get_health")


@pytest.mark.req("REQ-031")
async def test_a_hanging_scrape_is_a_timeout_too(actuator):
    binding = actuator.bind(METRICS)
    actuator.manifest(METRICS).limits.__dict__["timeout_s"] = 0.05
    actuator.harness.times_out("/actuator/prometheus")

    with pytest.raises(AdapterTimeoutError):
        await actuator.invoke(binding, "query_instant", "", AT)


@pytest.mark.req("REQ-031", "REQ-030")
async def test_an_application_that_is_down_is_unreachable_not_healthy(actuator):
    """Nothing is listening on this host, so the ping cannot succeed.

    It surfaces as AdapterUnavailableError rather than as `False`, which the
    health loop scores the same - DEGRADED, then DOWN when sustained - while
    carrying the reason. `False` is reserved for "the adapter reached it and it
    said no", and reporting a refused connection that way would put a sentence
    on the integration health screen that is not true.
    """
    binding = actuator.bind(METRICS, base_url=DEAD_URL)
    with pytest.raises(AdapterUnavailableError):
        await actuator.invoke(binding, "ping")

    health = actuator.bind(HEALTH, base_url=DEAD_URL, name="dead health")
    with pytest.raises(AdapterUnavailableError):
        await actuator.invoke(health, "ping")


@pytest.mark.req("REQ-031")
async def test_each_adapter_pings_the_endpoint_it_actually_reads(actuator):
    """Pinging /actuator/health for both would report a metrics binding healthy
    while the scrape returns 500."""
    metrics = actuator.bind(METRICS)
    health = actuator.bind(HEALTH)
    actuator.state.calls.clear()

    assert await actuator.invoke(metrics, "ping") is True
    assert await actuator.invoke(health, "ping") is True
    assert actuator.state.calls == ["/actuator/prometheus", "/actuator/health"]

    actuator.state.fail("/actuator/prometheus", 500)
    with pytest.raises(AdapterUnavailableError):
        await actuator.invoke(metrics, "ping")
    assert await actuator.invoke(health, "ping") is True, (
        "a different endpoint on the same application is unaffected"
    )
