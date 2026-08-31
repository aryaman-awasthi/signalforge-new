"""The registry and connector HTTP surface (REQ-030, REQ-012).

Covers the shapes the S3 wizard and the S23 integration health screen read, and
the RBAC that guards them.
"""

from __future__ import annotations

import pytest

from signalforge.tests.adapters.conftest import PULL_METRICS, write_manifest

pytestmark = pytest.mark.anyio


@pytest.mark.req("REQ-030")
async def test_the_registry_lists_every_adapter_with_what_it_can_do(adapter_api):
    owner = adapter_api.signup()
    token = owner["tokens"]["access_token"]

    response = adapter_api.client.get("/adapters", headers=adapter_api.auth_header(token))
    assert response.status_code == 200, response.text
    body = response.json()
    by_id = {a["id"]: a for a in body["adapters"]}

    metrics = by_id["probe-metrics"]
    assert metrics["kind"] == "metrics"
    assert metrics["mode"] == "pull"
    assert metrics["direction"] == "inbound (SignalForge polls)"
    assert metrics["capabilities"] == ["query_instant", "query_range"]
    assert metrics["credential_scopes"] == ["read_only"]
    assert metrics["auth"]["credential_fields"] == ["token"]
    assert metrics["config_schema"]["required"] == ["base_url"]
    assert metrics["limits"]["timeout_s"] == 5
    assert metrics["status"] == "runtime missing", "nothing is registered in this test"

    health = by_id["probe-health"]
    assert health["unsupported"] == ["run_probe"], (
        "the registry has to show what an adapter cannot do, not only what it can"
    )

    enterprise = by_id["probe-enterprise-logs"]
    assert enterprise["availability"] == "available"
    assert enterprise["status"] == "available"
    assert enterprise["runnable"] is False

    chaos = by_id["probe-chaos"]
    assert chaos["simulated"] is True
    assert chaos["credential_scopes"] == ["drill"]
    assert chaos["mutating_capabilities"] == ["abort", "inject"]

    assert body["errors"] == []


@pytest.mark.req("REQ-030")
async def test_a_registered_runtime_shows_the_adapter_as_installed(adapter_api):
    from signalforge.tests.adapters.conftest import StubMetricsAdapter

    owner = adapter_api.signup()
    token = owner["tokens"]["access_token"]
    adapter_api.container.adapter_runtimes.register(
        "probe-metrics", lambda context: StubMetricsAdapter(context)
    )

    one = adapter_api.client.get(
        "/adapters/probe-metrics", headers=adapter_api.auth_header(token)
    ).json()
    assert one["status"] == "installed"
    assert one["runnable"] is True


@pytest.mark.req("REQ-030")
async def test_the_registry_reports_manifests_it_refused_to_load(
    adapter_api, adapters_root
):
    """A rejected manifest is invisible everywhere else, so the list carries its
    own errors rather than quietly being shorter."""
    write_manifest(adapters_root, dict(PULL_METRICS, id="probe-bad", capabilities=["nope"]))
    adapter_api.container.adapter_registry.reload()

    owner = adapter_api.signup()
    token = owner["tokens"]["access_token"]
    body = adapter_api.client.get(
        "/adapters", headers=adapter_api.auth_header(token)
    ).json()

    assert "probe-bad" not in {a["id"] for a in body["adapters"]}
    assert body["errors"][0]["path"] == "probe-bad/manifest.yaml"
    assert "nope" in body["errors"][0]["error"]


@pytest.mark.req("REQ-030")
async def test_registry_filters(adapter_api):
    owner = adapter_api.signup()
    token = owner["tokens"]["access_token"]
    headers = adapter_api.auth_header(token)

    def ids(query: str) -> list[str]:
        return [
            a["id"]
            for a in adapter_api.client.get(
                f"/adapters?{query}", headers=headers
            ).json()["adapters"]
        ]

    assert ids("kind=metrics") == ["probe-metrics"]
    assert ids("mode=push") == ["probe-alert-hook"]
    assert ids("scope=drill") == ["probe-chaos"]
    assert ids("simulated=true") == ["probe-chaos"]


@pytest.mark.req("REQ-030")
async def test_an_unknown_adapter_is_a_404(adapter_api):
    owner = adapter_api.signup()
    response = adapter_api.client.get(
        "/adapters/nope", headers=adapter_api.auth_header(owner["tokens"]["access_token"])
    )
    assert response.status_code == 404


@pytest.mark.req("REQ-030")
async def test_the_capability_matrix_is_rows_and_per_kind_coverage(adapter_api):
    owner = adapter_api.signup()
    token = owner["tokens"]["access_token"]

    matrix = adapter_api.client.get(
        "/adapters/capability-matrix", headers=adapter_api.auth_header(token)
    )
    assert matrix.status_code == 200, matrix.text
    body = matrix.json()

    rows = {row["adapter_id"]: row for row in body["rows"]}
    health_cells = {c["capability"]: c["supported"] for c in rows["probe-health"]["cells"]}
    assert health_cells == {"get_health": True, "run_probe": False}

    coverage = {c["kind"]: c for c in body["coverage"]}
    assert len(coverage) == 13, "every adapter kind appears, covered or not"
    assert coverage["metrics"]["complete"] is True
    assert coverage["traces"]["adapters"] == []
    assert set(coverage["traces"]["uncovered"]) == {"search", "get_trace"}
    assert coverage["logs"]["covered"] == [], (
        "an interface-only adapter is listed but does not count as coverage"
    )


@pytest.mark.req("REQ-030", "REQ-012")
async def test_the_whole_surface_needs_manage_apps(adapter_api):
    owner = adapter_api.signup()
    owner_token = owner["tokens"]["access_token"]
    commander = adapter_api.invite_and_accept(
        owner_token, email="ic@acme.io", roles=["incident_commander"]
    )
    token = commander["tokens"]["access_token"]

    calls = [
        ("GET", "/adapters", None),
        ("GET", "/adapters/capability-matrix", None),
        ("GET", "/adapters/probe-metrics", None),
        ("GET", "/connectors", None),
        ("GET", "/connectors/health", None),
        (
            "POST",
            "/connectors",
            {
                "app_id": "checkout",
                "adapter_id": "probe-metrics",
                "name": "x",
                "credential_scope": "read_only",
                "config": {"base_url": "http://app"},
                "credentials": {"token": "t"},
            },
        ),
        ("PATCH", "/connectors/any", {"enabled": False}),
        ("DELETE", "/connectors/any", None),
    ]
    for method, path, body in calls:
        response = adapter_api.client.request(
            method, path, json=body, headers=adapter_api.auth_header(token)
        )
        assert response.status_code == 403, f"{method} {path} -> {response.status_code}"
        assert response.json()["permission"] == "manage_apps"

    denied = [
        row
        for row in adapter_api.audit_rows(owner["org"]["id"])
        if row["topic"] == "audit.authz.denied"
    ]
    assert len(denied) == len(calls), "every denial lands in the audit chain"


@pytest.mark.req("REQ-030", "REQ-012")
async def test_a_permission_removed_mid_flow_stops_the_next_write(adapter_api):
    """Permission change mid-flow (CLAUDE.md rule 6). The binding an operator
    already made keeps working - it belongs to the app, not to the person - but
    their next write is refused, and the connector is still visible to whoever
    does hold manage_apps."""
    owner = adapter_api.signup()
    owner_token = owner["tokens"]["access_token"]
    second = adapter_api.invite_and_accept(
        owner_token, email="ops@acme.io", roles=["org_owner"], display_name="Ops"
    )
    ops_token = second["tokens"]["access_token"]

    created = adapter_api.client.post(
        "/connectors",
        json={
            "app_id": "checkout",
            "adapter_id": "probe-metrics",
            "name": "checkout metrics",
            "credential_scope": "read_only",
            "config": {"base_url": "http://app.internal"},
            "credentials": {"token": "t"},
        },
        headers=adapter_api.auth_header(ops_token),
    )
    assert created.status_code == 201, created.text

    demoted = adapter_api.client.put(
        f"/users/{second['user']['id']}/roles",
        json={"bindings": [{"role": "auditor"}]},
        headers=adapter_api.auth_header(owner_token),
    )
    assert demoted.status_code == 200, demoted.text

    refused = adapter_api.client.patch(
        f"/connectors/{created.json()['id']}",
        json={"enabled": False},
        headers=adapter_api.auth_header(ops_token),
    )
    assert refused.status_code == 403

    still_there = adapter_api.client.get(
        "/connectors", headers=adapter_api.auth_header(owner_token)
    ).json()
    assert [c["id"] for c in still_there] == [created.json()["id"]]
    assert still_there[0]["enabled"] is True
