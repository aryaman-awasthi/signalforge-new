"""The http-observe set against an application that serves the contract (REQ-031).

Every test here goes the whole way: sign up, bind the adapter through the API
(so the config is schema-validated and the credential is Fernet-encrypted), then
call it through the ConnectorInvoker (so the credential-scope floor, the timeout
and the metrics are all real). The application on the other end is a Starlette
app implementing only what `contracts/observe.py` freezes, which is the claim
being tested: serving the contract is enough to be onboarded.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from signalforge.contracts.canonical import (
    ExecutionState,
    HealthStatus,
    ProbeSpec,
    SpanStatus,
)
from signalforge.contracts.errors import (
    AdapterAuthError,
    AdapterError,
    AdapterTimeoutError,
    NotSupportedError,
)
from signalforge.infrastructure.adapters.limits import RateLimitExceeded
from signalforge.tests.adapters.conftest import connect

pytestmark = pytest.mark.anyio

AT = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)
WINDOW = (AT - timedelta(minutes=5), AT)


@pytest.fixture
def bound(api, observe):
    """Signup plus a helper that binds a shipped adapter and calls it."""
    signup = api.signup()
    token = signup["tokens"]["access_token"]
    tenant_id = signup["org"]["id"]

    class Bound:
        def __init__(self):
            self.token = token
            self.tenant_id = tenant_id

        def bind(self, adapter_id, **kwargs):
            return connect(api, token, adapter_id, **kwargs)

        async def invoke(self, binding, capability, *args, **kwargs):
            return await api.container.invoker.invoke(
                tenant_id, binding["id"], capability, *args, **kwargs
            )

        def manifest(self, adapter_id):
            return api.container.adapter_registry.get(adapter_id)

        async def runtime(self, binding, capability="ping"):
            """The adapter instance itself, for the few assertions that are
            about what the runtime does rather than about what the invoker
            allows."""
            resolved = await api.container.invoker.resolve(
                tenant_id, binding["id"], capability
            )
            return resolved.adapter

    return Bound()


# --- metrics ------------------------------------------------------------------


@pytest.mark.req("REQ-031")
async def test_metrics_are_parsed_from_prometheus_text_and_selected(bound):
    binding = bound.bind("http-observe-metrics")
    series = await bound.invoke(binding, "query_instant", "http_requests_total", AT)

    assert {one.name for one in series} == {"http_requests_total"}
    by_region = {one.labels["region"]: one.points[0].value for one in series if one.labels.get("route") == "/checkout"}
    assert by_region == {"eu-west-1": 1200.0, "us-east-1": 800.0}
    assert all(point.ts == AT for one in series for point in one.points)


@pytest.mark.req("REQ-031")
async def test_a_label_matcher_narrows_the_selection(bound):
    binding = bound.bind("http-observe-metrics")
    exact = await bound.invoke(
        binding, "query_instant", 'http_requests_total{route="/checkout"}', AT
    )
    assert len(exact) == 2

    regex = await bound.invoke(
        binding, "query_instant", 'http_requests_total{region=~"eu-.*"}', AT
    )
    assert {one.labels["route"] for one in regex} == {"/checkout", "/health"}

    negated = await bound.invoke(
        binding, "query_instant", 'http_requests_total{route!="/health"}', AT
    )
    assert len(negated) == 2


@pytest.mark.req("REQ-031")
async def test_an_unsupported_selector_is_refused_rather_than_half_applied(bound):
    binding = bound.bind("http-observe-metrics")
    with pytest.raises(AdapterError) as refused:
        await bound.invoke(binding, "query_instant", "rate(http_requests_total[5m])", AT)
    assert "selector" in str(refused.value)


@pytest.mark.req("REQ-031")
async def test_observe_metrics_refuses_a_range_query_and_names_the_alternative(bound):
    """A scrape has no history. Answering a range from it would be invention.

    Refused twice over: the invoker will not route an undeclared capability at
    all, and the runtime itself raises NotSupportedError as the SPI prescribes.
    """
    from signalforge.domain.adapters.errors import CredentialScopeError

    binding = bound.bind("http-observe-metrics")
    with pytest.raises(CredentialScopeError):
        await bound.invoke(binding, "query_range", "x", WINDOW[0], WINDOW[1], 15.0)

    adapter = await bound.runtime(binding)
    with pytest.raises(NotSupportedError) as refused:
        await adapter.query_range("x", WINDOW[0], WINDOW[1], 15.0)
    assert "prometheus" in str(refused.value)


# --- logs ---------------------------------------------------------------------


@pytest.mark.req("REQ-031")
async def test_log_search_applies_the_manifest_redaction(bound):
    binding = bound.bind("http-observe-logs")
    records = await bound.invoke(binding, "search", "", WINDOW[0], WINDOW[1], 50)

    first = records[0]
    assert "ada@example.com" not in first.message
    assert "4111 1111 1111 1111" not in first.message
    assert "***" in first.message
    assert first.attributes["customer"] == "***"
    assert first.attributes["region"] == "eu-west-1", "redaction is targeted, not blanket"


@pytest.mark.req("REQ-031")
async def test_log_aggregation_counts_what_it_read(bound):
    binding = bound.bind("http-observe-logs")
    by_level = await bound.invoke(binding, "aggregate", "", WINDOW[0], WINDOW[1], "level")
    assert by_level == {"error": 2, "info": 1, "warning": 1}

    by_region = await bound.invoke(
        binding, "aggregate", "", WINDOW[0], WINDOW[1], "attributes.region"
    )
    assert by_region == {"eu-west-1": 2, "us-east-1": 2}


@pytest.mark.req("REQ-031")
async def test_aggregating_by_something_that_is_not_a_field_is_refused(bound):
    binding = bound.bind("http-observe-logs")
    with pytest.raises(AdapterError) as refused:
        await bound.invoke(binding, "aggregate", "", WINDOW[0], WINDOW[1], "nonsense")
    assert "attributes.<key>" in str(refused.value)


@pytest.mark.req("REQ-031")
async def test_sampling_spreads_across_the_window_rather_than_taking_the_first(bound):
    """The first n records of an incident window are all from its first second."""
    binding = bound.bind("http-observe-logs")
    sample = await bound.invoke(binding, "sample", "", WINDOW[0], WINDOW[1], 2)
    assert len(sample) == 2
    assert sample[0].service == "checkout"
    assert sample[1].service == "cart", "a stride sample reaches the end of the window"


# --- traces -------------------------------------------------------------------


@pytest.mark.req("REQ-031")
async def test_traces_search_filters_and_redacts(bound):
    binding = bound.bind("http-observe-traces")
    spans = await bound.invoke(
        binding, "search", WINDOW[0], WINDOW[1], 50, None, None, True
    )
    assert [span.span_id for span in spans] == ["span-1"]
    assert spans[0].status is SpanStatus.ERROR
    assert spans[0].attributes["user.email"] == "***"


@pytest.mark.req("REQ-031")
async def test_get_trace_returns_the_whole_trace(bound):
    binding = bound.bind("http-observe-traces")
    spans = await bound.invoke(binding, "get_trace", "trace-1")
    assert {span.span_id for span in spans} == {"span-1", "span-2"}


# --- health and probes --------------------------------------------------------


@pytest.mark.req("REQ-031")
async def test_health_is_translated_into_the_canonical_report(bound):
    binding = bound.bind("http-observe-health")
    report = await bound.invoke(binding, "get_health")
    assert report.status is HealthStatus.DEGRADED
    assert {c.component for c in report.components} == {"database", "payment-gateway"}


@pytest.mark.req("REQ-031")
async def test_a_probe_reports_its_own_result_rather_than_raising(bound, observe):
    """A non-2xx is a probe result. A probe that raised on 500 could never
    assert that a page correctly returns 500."""
    binding = bound.bind("http-observe-health")
    ok = await bound.invoke(
        binding, "run_probe", ProbeSpec(probe_id="checkout", target="/checkout")
    )
    assert ok.ok is True
    assert ok.status_code == 200
    assert ok.latency_ms >= 0

    observe.state.fail("/checkout", 503)
    failed = await bound.invoke(
        binding, "run_probe", ProbeSpec(probe_id="checkout", target="/checkout")
    )
    assert failed.ok is False
    assert failed.status_code == 503
    assert "expected 200, got 503" in failed.detail


@pytest.mark.req("REQ-031")
async def test_a_probe_that_times_out_is_a_result_not_an_exception(bound, observe):
    binding = bound.bind("http-observe-health")
    observe.times_out("/checkout")
    result = await bound.invoke(
        binding,
        "run_probe",
        ProbeSpec(probe_id="checkout", target="/checkout", timeout_s=0.05),
    )
    assert result.ok is False
    assert result.status_code is None
    assert "within" in result.detail


# --- actions ------------------------------------------------------------------


@pytest.mark.req("REQ-031")
async def test_the_action_manifest_is_validated_against_the_frozen_schema(bound, observe):
    binding = bound.bind("http-observe-actions")
    actions = await bound.invoke(binding, "manifest")
    assert {action.action_id for action in actions} == {
        "restart-billing",
        "collect-diagnostics",
    }
    restart = next(a for a in actions if a.action_id == "restart-billing")
    assert restart.approver_roles == ["incident_commander"]

    # A high-risk action with no approver roles never becomes executable here.
    observe.state.actions = {
        "schema_version": 1,
        "actions": [
            {
                "action_id": "restart-billing",
                "name": "Restart",
                "description": "no approvers",
                "action_class": "restart",
                "risk": "high",
            }
        ],
    }
    with pytest.raises(AdapterError) as refused:
        await bound.invoke(binding, "manifest")
    assert "approver_roles" in str(refused.value)


@pytest.mark.req("REQ-031")
async def test_executing_an_action_returns_the_execution(bound):
    binding = bound.bind("http-observe-actions")
    execution = await bound.invoke(
        binding, "execute", "collect-diagnostics", {"depth": 2}, "key-1"
    )
    assert execution.state is ExecutionState.SUCCEEDED
    assert execution.idempotency_key == "key-1"
    assert execution.output == {"params": {"depth": 2}}


@pytest.mark.req("REQ-031", "REQ-062")
async def test_the_same_idempotency_key_never_executes_twice(bound, observe):
    binding = bound.bind("http-observe-actions")
    first = await bound.invoke(
        binding, "execute", "collect-diagnostics", {}, "key-dupe"
    )
    second = await bound.invoke(
        binding, "execute", "collect-diagnostics", {}, "key-dupe"
    )
    assert first.execution_id == second.execution_id
    assert observe.state.count("/observe/actions/collect-diagnostics/execute") == 1, (
        "the adapter must ask whether the key already ran before sending anything"
    )


@pytest.mark.req("REQ-031", "REQ-062")
async def test_a_timed_out_execution_is_uncertain_not_failed(bound, observe):
    """The request may still be running and the change may already have
    happened. Reporting failure would invite a retry that double-applies it."""
    binding = bound.bind("http-observe-actions")
    bound.manifest("http-observe-actions").limits.__dict__["timeout_s"] = 0.05
    observe.times_out("/observe/actions/restart-billing/execute")

    execution = await bound.invoke(
        binding, "execute", "restart-billing", {}, "key-uncertain"
    )
    assert execution.state is ExecutionState.UNCERTAIN
    assert "AdapterTimeoutError" in execution.error
    assert execution.idempotency_key == "key-uncertain"


@pytest.mark.req("REQ-031", "REQ-062")
async def test_get_execution_says_none_when_a_key_never_ran(bound):
    binding = bound.bind("http-observe-actions")
    assert await bound.invoke(binding, "get_execution", "never-used") is None


# --- chaos --------------------------------------------------------------------


@pytest.mark.req("REQ-031")
async def test_chaos_manifest_inject_status_and_abort(bound):
    binding = bound.bind("http-observe-chaos")
    scenarios = await bound.invoke(binding, "manifest")
    assert [s.scenario_id for s in scenarios] == ["billing-latency"]
    assert scenarios[0].max_duration_s == 300

    injection = await bound.invoke(
        binding, "inject", "billing-latency", {"ms": 400}, "drill:d-1", "idem-1"
    )
    assert injection.state.value == "active"
    assert injection.scope == "drill:d-1"

    same = await bound.invoke(binding, "status", injection.injection_id)
    assert same.injection_id == injection.injection_id

    aborted = await bound.invoke(binding, "abort", injection.injection_id)
    assert aborted.state.value == "aborted"


@pytest.mark.req("REQ-031")
async def test_chaos_refuses_a_scope_that_is_not_a_drill(bound, observe):
    binding = bound.bind("http-observe-chaos")
    with pytest.raises(AdapterError) as refused:
        await bound.invoke(binding, "inject", "billing-latency", {}, "incident:i-1", "k")
    assert "drill:" in str(refused.value)
    assert observe.state.injections == {}, "nothing reached the target"


# --- failure and limits -------------------------------------------------------


@pytest.mark.req("REQ-031")
async def test_a_rejected_credential_is_an_auth_error_not_a_generic_failure(bound):
    binding = bound.bind(
        "http-observe-metrics", credentials={"token": "the-wrong-token"}
    )
    with pytest.raises(AdapterAuthError):
        await bound.invoke(binding, "query_instant", "", AT)


@pytest.mark.req("REQ-031")
async def test_a_server_error_reads_as_unavailable(bound, observe):
    from signalforge.contracts.errors import AdapterUnavailableError

    binding = bound.bind("http-observe-metrics")
    observe.state.fail("/observe/metrics", 502)
    with pytest.raises(AdapterUnavailableError):
        await bound.invoke(binding, "query_instant", "", AT)


@pytest.mark.req("REQ-031", "REQ-030")
async def test_an_oversized_response_is_abandoned_at_the_manifest_limit(bound):
    binding = bound.bind("http-observe-metrics")
    bound.manifest("http-observe-metrics").limits.__dict__["max_result_bytes"] = 32
    with pytest.raises(AdapterError) as refused:
        await bound.invoke(binding, "query_instant", "", AT)
    assert "max_result_bytes" in str(refused.value)


@pytest.mark.req("REQ-031", "REQ-030")
async def test_the_manifests_call_budget_is_enforced_per_binding(bound):
    binding = bound.bind("http-observe-metrics")
    bound.manifest("http-observe-metrics").limits.__dict__["max_calls_per_minute"] = 1

    await bound.invoke(binding, "query_instant", "", AT)
    with pytest.raises(RateLimitExceeded) as spent:
        await bound.invoke(binding, "query_instant", "", AT)
    assert "call budget" in str(spent.value)


@pytest.mark.req("REQ-031")
async def test_a_timeout_is_reported_as_a_timeout(bound, observe):
    binding = bound.bind("http-observe-logs")
    bound.manifest("http-observe-logs").limits.__dict__["timeout_s"] = 0.05
    observe.times_out("/observe/logs")
    with pytest.raises(AdapterTimeoutError):
        await bound.invoke(binding, "search", "", WINDOW[0], WINDOW[1], 10)


@pytest.mark.req("REQ-031")
async def test_each_adapter_pings_the_endpoint_it_actually_uses(bound, observe):
    """Pinging /observe/health for all six would report a metrics binding
    healthy while /observe/metrics returns 500."""
    metrics = bound.bind("http-observe-metrics")
    observe.state.calls.clear()
    assert await bound.invoke(metrics, "ping") is True
    assert observe.state.calls == ["/observe/metrics"]

    observe.state.fail("/observe/metrics", 500)
    from signalforge.contracts.errors import AdapterUnavailableError

    with pytest.raises(AdapterUnavailableError):
        await bound.invoke(metrics, "ping")

    health = bound.bind("http-observe-health")
    assert await bound.invoke(health, "ping") is True, (
        "a different capability on the same application is unaffected"
    )
