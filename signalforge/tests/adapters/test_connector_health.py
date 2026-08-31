"""The connector health loop (REQ-030, REQ-025).

Every landing in the table in application/adapters/health.py gets a test here,
because "what does the platform say when the integration is weird" is the whole
value of an integration health screen. The loop is driven directly rather than
through the worker so the assertions do not depend on timing.
"""

from __future__ import annotations

import asyncio

import pytest

from signalforge.contracts.canonical import HealthStatus
from signalforge.contracts.errors import AdapterAuthError, AdapterUnavailableError
from signalforge.domain.observability.health import PLATFORM_TENANT_ID
from signalforge.tests.adapters.conftest import (
    PULL_METRICS,
    StubHealthAdapter,
    StubMetricsAdapter,
    write_manifest,
)

pytestmark = pytest.mark.anyio

BASE_URL = "http://app.internal:8080"


async def bind(adapter_api, token, **overrides):
    body = {
        "app_id": "checkout",
        "adapter_id": "probe-metrics",
        "name": "checkout metrics",
        "credential_scope": "read_only",
        "config": {"base_url": BASE_URL},
        "credentials": {"token": "read-only-token"},
    }
    body.update(overrides)
    response = adapter_api.client.post(
        "/connectors", json=body, headers=adapter_api.auth_header(token)
    )
    assert response.status_code == 201, response.text
    return response.json()


class Runtime:
    """A registered adapter factory. The loop builds a fresh adapter every poll -
    credentials must not be cached - so the behaviour a test wants lives here,
    on the factory, and changing it is what "the target went down" looks like."""

    def __init__(self, behaviour="ok"):
        self.behaviour = behaviour
        self.built: list[StubMetricsAdapter] = []

    def __call__(self, context):
        adapter = StubMetricsAdapter(context, self.behaviour)
        self.built.append(adapter)
        return adapter


def install_runtime(adapter_api, adapter_id="probe-metrics", behaviour="ok") -> Runtime:
    runtime = Runtime(behaviour)
    adapter_api.container.adapter_runtimes.register(adapter_id, runtime)
    return runtime


async def poll(adapter_api):
    return await adapter_api.container.health.report()


def connector_component(report, binding_id):
    return next(c for c in report.components if c.component.endswith(binding_id))


@pytest.mark.req("REQ-030", "REQ-025")
async def test_a_reachable_connector_reports_up_and_records_freshness(adapter_api):
    owner = adapter_api.signup()
    token = owner["tokens"]["access_token"]
    install_runtime(adapter_api)
    binding = await bind(adapter_api, token)

    report = await poll(adapter_api)
    component = connector_component(report, binding["id"])
    assert component.status is HealthStatus.UP
    assert component.detail == "reachable"

    health = adapter_api.client.get(
        "/connectors/health", headers=adapter_api.auth_header(token)
    ).json()
    assert health[0]["status"] == "up"
    assert health[0]["last_ok_at"] is not None
    assert health[0]["freshness_s"] == 0.0
    assert health[0]["direction"] == "inbound (SignalForge polls)"
    assert health[0]["consecutive_failures"] == 0


@pytest.mark.req("REQ-030", "REQ-025")
async def test_credentials_reach_the_adapter_and_nothing_else_does(adapter_api):
    owner = adapter_api.signup()
    token = owner["tokens"]["access_token"]
    runtime = install_runtime(adapter_api)
    await bind(adapter_api, token)

    await poll(adapter_api)

    context = runtime.built[0].context
    assert context.credentials == {"token": "read-only-token"}
    assert context.binding.config == {"base_url": BASE_URL}
    assert "read-only-token" not in repr(context), (
        "an AdapterContext must not put a credential into a traceback"
    )


@pytest.mark.req("REQ-030", "REQ-025")
async def test_a_binding_with_no_runtime_installed_is_unknown_not_healthy(adapter_api):
    """Model of every "we cannot measure this" case: an adapter whose interface
    exists and whose runtime does not is UNKNOWN, and a green tick would be the
    single most misleading thing the screen could show."""
    owner = adapter_api.signup()
    token = owner["tokens"]["access_token"]
    binding = await bind(adapter_api, token)

    report = await poll(adapter_api)
    component = connector_component(report, binding["id"])
    assert component.status is HealthStatus.UNKNOWN
    assert "no runtime" in component.detail

    health = adapter_api.client.get(
        "/connectors/health", headers=adapter_api.auth_header(token)
    ).json()
    assert health[0]["status"] == "unknown"
    assert health[0]["last_ok_at"] is None
    assert health[0]["freshness_s"] is None


@pytest.mark.req("REQ-030", "REQ-025")
async def test_a_timeout_degrades_first_and_goes_down_only_when_sustained(adapter_api):
    """Uncertain external state: one timeout says nothing, three in a row do."""

    async def never_answers():
        await asyncio.sleep(3600)
        return True

    owner = adapter_api.signup()
    token = owner["tokens"]["access_token"]
    install_runtime(adapter_api, behaviour=never_answers)
    binding = await bind(adapter_api, token)
    # The manifest's own limit is what bounds the call; shorten it so the test
    # exercises the real code path rather than waiting five seconds.
    adapter_api.container.adapter_registry.get("probe-metrics").limits.__dict__[
        "timeout_s"
    ] = 0.01

    statuses = []
    for _ in range(3):
        report = await poll(adapter_api)
        statuses.append(connector_component(report, binding["id"]).status)
    assert statuses == [
        HealthStatus.DEGRADED,
        HealthStatus.DEGRADED,
        HealthStatus.DOWN,
    ]

    health = adapter_api.client.get(
        "/connectors/health", headers=adapter_api.auth_header(token)
    ).json()
    assert health[0]["consecutive_failures"] == 3
    assert "timed out" in health[0]["last_error"]


@pytest.mark.req("REQ-030", "REQ-025")
async def test_rejected_credentials_report_down_immediately(adapter_api):
    """Waiting three polls to admit a key is wrong helps nobody: an auth failure
    will not heal on its own, so it is terminal on the first observation."""
    owner = adapter_api.signup()
    token = owner["tokens"]["access_token"]
    install_runtime(adapter_api, behaviour=AdapterAuthError("401 from the target"))
    binding = await bind(adapter_api, token)

    report = await poll(adapter_api)
    component = connector_component(report, binding["id"])
    assert component.status is HealthStatus.DOWN
    assert "credentials rejected" in component.detail


@pytest.mark.req("REQ-030", "REQ-025")
async def test_an_unreachable_target_degrades_and_a_false_ping_does_too(adapter_api):
    owner = adapter_api.signup()
    token = owner["tokens"]["access_token"]
    install_runtime(adapter_api, behaviour=AdapterUnavailableError("connection refused"))
    binding = await bind(adapter_api, token)

    report = await poll(adapter_api)
    component = connector_component(report, binding["id"])
    assert component.status is HealthStatus.DEGRADED
    assert "AdapterUnavailableError" in component.detail


@pytest.mark.req("REQ-030", "REQ-025")
async def test_an_adapter_raising_outside_the_taxonomy_does_not_stop_the_loop(
    adapter_api,
):
    owner = adapter_api.signup()
    token = owner["tokens"]["access_token"]
    install_runtime(adapter_api, behaviour=ZeroDivisionError("bug in the adapter"))
    binding = await bind(adapter_api, token)

    report = await poll(adapter_api)
    assert connector_component(report, binding["id"]).status is HealthStatus.DEGRADED
    assert {c.component for c in report.components} >= {"database", "event-bus"}, (
        "the platform's own components are still reported"
    )


@pytest.mark.req("REQ-030", "REQ-025")
async def test_a_runtime_whose_kind_disagrees_with_its_manifest_is_down(adapter_api):
    owner = adapter_api.signup()
    token = owner["tokens"]["access_token"]
    adapter_api.container.adapter_runtimes.register(
        "probe-metrics", lambda context: StubHealthAdapter(context)
    )
    binding = await bind(adapter_api, token)

    report = await poll(adapter_api)
    component = connector_component(report, binding["id"])
    assert component.status is HealthStatus.DOWN
    assert "runtime could not be built" in component.detail


@pytest.mark.req("REQ-030", "REQ-025")
async def test_an_orphaned_binding_reports_down_with_a_readable_reason(
    adapter_api, adapters_root
):
    owner = adapter_api.signup()
    token = owner["tokens"]["access_token"]
    binding = await bind(adapter_api, token)

    (adapters_root / "probe-metrics" / "manifest.yaml").unlink()
    adapter_api.container.adapter_registry.reload()

    report = await poll(adapter_api)
    component = connector_component(report, binding["id"])
    assert component.status is HealthStatus.DOWN
    assert "orphaned" in component.detail


@pytest.mark.req("REQ-030", "REQ-025")
async def test_health_events_land_on_the_owning_tenants_timeline(adapter_api):
    owner = adapter_api.signup()
    token = owner["tokens"]["access_token"]
    tenant_id = owner["org"]["id"]
    install_runtime(adapter_api)
    await bind(adapter_api, token)

    await poll(adapter_api)

    async with adapter_api.container.event_uow_factory() as uow:
        tenant_events = list(await uow.events.read(tenant_id=tenant_id, limit=50))
        platform_events = list(
            await uow.events.read(tenant_id=PLATFORM_TENANT_ID, limit=50)
        )
    connector_health = [
        e for e in tenant_events if e.topic == "system.connector.health"
    ]
    assert len(connector_health) == 1
    assert connector_health[0].app_id == "checkout"
    assert connector_health[0].payload["status"] == "up"
    assert connector_health[0].plane.value == "signal"

    platform_components = {e.payload["connector"] for e in platform_events}
    assert "database" in platform_components
    assert not any(
        c.startswith("connector:") for c in platform_components
    ), "a customer's connector never lands on the platform tenant"


@pytest.mark.req("REQ-030", "REQ-025")
async def test_only_a_transition_is_audited_not_every_poll(adapter_api):
    owner = adapter_api.signup()
    token = owner["tokens"]["access_token"]
    runtime = install_runtime(adapter_api)
    await bind(adapter_api, token)

    await poll(adapter_api)
    await poll(adapter_api)
    entries = [
        row
        for row in adapter_api.audit_rows(owner["org"]["id"])
        if row["topic"] == "system.connector.health"
    ]
    assert len(entries) == 1, "two polls, one verdict, one audit entry"

    runtime.behaviour = "false"
    await poll(adapter_api)
    entries = [
        row
        for row in adapter_api.audit_rows(owner["org"]["id"])
        if row["topic"] == "system.connector.health"
    ]
    assert len(entries) == 2
    assert entries[1]["payload"]["status"] == "degraded"


@pytest.mark.req("REQ-030", "REQ-025")
async def test_a_broken_connector_never_makes_this_node_unready(adapter_api):
    """/health answers "can this process serve", and a customer's dead
    integration is not an answer to that question."""
    owner = adapter_api.signup()
    token = owner["tokens"]["access_token"]
    install_runtime(adapter_api, behaviour=AdapterAuthError("nope"))
    await bind(adapter_api, token)
    await poll(adapter_api)

    response = adapter_api.client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert {c["component"] for c in response.json()["components"]} == {
        "database",
        "event-bus",
    }


@pytest.mark.req("REQ-030", "REQ-025")
async def test_metrics_count_bindings_without_naming_one(adapter_api):
    owner = adapter_api.signup()
    token = owner["tokens"]["access_token"]
    install_runtime(adapter_api)
    binding = await bind(adapter_api, token)
    await poll(adapter_api)

    body = adapter_api.client.get("/metrics").text
    assert 'signalforge_connector_bindings{state="up"} 1' in body
    assert 'signalforge_connector_health{connector="adapter:probe-metrics"} 1' in body
    assert binding["id"] not in body, "/metrics is unauthenticated: no binding ids"
    assert owner["org"]["id"] not in body


@pytest.mark.req("REQ-030", "REQ-025")
async def test_two_bindings_of_one_adapter_collapse_to_their_worst_verdict(
    adapter_api, adapters_root
):
    owner = adapter_api.signup()
    token = owner["tokens"]["access_token"]
    behaviours = iter(["ok", AdapterAuthError("nope")])

    def factory(context):
        return StubMetricsAdapter(context, next(behaviours))

    adapter_api.container.adapter_runtimes.register("probe-metrics", factory)
    await bind(adapter_api, token)
    await bind(adapter_api, token, app_id="payments", name="payments metrics")

    await poll(adapter_api)

    body = adapter_api.client.get("/metrics").text
    assert 'signalforge_connector_health{connector="adapter:probe-metrics"} 0' in body
    assert 'signalforge_connector_bindings{state="up"} 1' in body
    assert 'signalforge_connector_bindings{state="down"} 1' in body


@pytest.mark.req("REQ-030", "REQ-013")
async def test_platform_status_shows_a_tenant_only_its_own_connectors(
    adapter_api, adapters_root
):
    """One poll loop serves the install; the operator screen is still per-tenant."""
    first = adapter_api.signup()
    second = adapter_api.signup(org="Other Corp", email="owner@other.io")
    install_runtime(adapter_api)
    mine = await bind(adapter_api, first["tokens"]["access_token"])
    theirs = await bind(adapter_api, second["tokens"]["access_token"], app_id="theirs")

    await poll(adapter_api)

    status = adapter_api.client.get(
        "/platform/status",
        headers=adapter_api.auth_header(first["tokens"]["access_token"]),
    ).json()
    components = {c["component"] for c in status["components"]}
    assert any(mine["id"] in c for c in components)
    assert not any(theirs["id"] in c for c in components)
    assert {"database", "event-bus"} <= components


@pytest.mark.req("REQ-030", "REQ-025")
async def test_a_newly_bound_connector_joins_the_loop_without_a_restart(adapter_api):
    owner = adapter_api.signup()
    token = owner["tokens"]["access_token"]
    install_runtime(adapter_api)

    before = await poll(adapter_api)
    assert not any(c.component.startswith("connector:") for c in before.components)

    await bind(adapter_api, token)
    after = await poll(adapter_api)
    assert any(c.component.startswith("connector:") for c in after.components)


@pytest.mark.req("REQ-030", "REQ-025")
async def test_the_worker_runs_the_connector_health_job(adapter_api):
    """The loop is not a thing tests call: it is a scheduled worker job."""
    owner = adapter_api.signup()
    token = owner["tokens"]["access_token"]
    install_runtime(adapter_api)
    binding = await bind(adapter_api, token)

    status = await adapter_api.container.worker.run_job_once("connector-health")
    assert status.last_error is None

    health = adapter_api.client.get(
        "/connectors/health", headers=adapter_api.auth_header(token)
    ).json()
    assert health[0]["id"] == binding["id"]
    assert health[0]["status"] == "up"


@pytest.mark.req("REQ-030", "REQ-025")
async def test_a_disabled_binding_stops_being_polled_and_keeps_its_last_verdict(
    adapter_api,
):
    owner = adapter_api.signup()
    token = owner["tokens"]["access_token"]
    install_runtime(adapter_api)
    binding = await bind(adapter_api, token)
    await poll(adapter_api)

    adapter_api.client.patch(
        f"/connectors/{binding['id']}",
        json={"enabled": False},
        headers=adapter_api.auth_header(token),
    )
    report = await poll(adapter_api)
    assert not any(c.component.endswith(binding["id"]) for c in report.components)

    health = adapter_api.client.get(
        "/connectors/health", headers=adapter_api.auth_header(token)
    ).json()
    assert health[0]["enabled"] is False
    assert health[0]["status"] == "up", "the last thing observed is still the last thing"


@pytest.mark.req("REQ-030", "REQ-025")
async def test_a_simulated_adapter_says_so_in_its_health_detail(
    adapter_api, adapters_root
):
    write_manifest(adapters_root, dict(PULL_METRICS, id="probe-sim", simulated=True))
    adapter_api.container.adapter_registry.reload()
    owner = adapter_api.signup()
    token = owner["tokens"]["access_token"]
    install_runtime(adapter_api, adapter_id="probe-sim")
    binding = await bind(adapter_api, token, adapter_id="probe-sim", name="simulated")

    report = await poll(adapter_api)
    assert "SIMULATED" in connector_component(report, binding["id"]).detail

    health = adapter_api.client.get(
        "/connectors/health", headers=adapter_api.auth_header(token)
    ).json()
    assert health[0]["simulated"] is True
