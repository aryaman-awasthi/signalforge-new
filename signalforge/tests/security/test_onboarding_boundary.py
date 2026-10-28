"""Security properties of the onboarding boundary (artifact 18).

Three things arrived with S3-T04 and each brings a way to be wrong:

- an **unauthenticated outbound probe** that is pointed at a URL an operator
  typed - which is how a text field becomes a local file read if the scheme is
  not checked, and how a credential leaks if the probe sends one;
- a **stored policy**, which means the floor is now data as well as code: a row
  is only as safe as what refuses to load it;
- a **wizard** that handles credentials on the way in and reports on whether
  they worked on the way out, without ever reporting what they were.
"""

from __future__ import annotations

import json
import sqlite3

import pytest
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route

from signalforge.api.main import app as api_app
from signalforge.contracts.roles import Permission
from signalforge.domain.onboarding.discovery import LocalService
from signalforge.tests.adapters.conftest import ObserveHarness, connect
from signalforge.tests.onboarding.conftest import (
    APP_ID,
    SIGNAL_BODY,
    register_app,
)

pytestmark = pytest.mark.anyio

APP_TOKEN = "s3cr3t-observe-token"


def _harness(api) -> ObserveHarness:
    from signalforge.tests.adapters.observe_app import build_observe_app

    harness = ObserveHarness()
    app, state = build_observe_app()
    harness.mount(ObserveHarness.BASE_URL, app)
    harness.state = state
    harness.attach(api.container)
    return harness


# --- the outbound probe -------------------------------------------------------


@pytest.mark.req("REQ-018", "REQ-036")
async def test_the_discovery_probe_refuses_anything_but_http(api):
    """Discovery runs before any binding exists, so it has no manifest to take
    limits from - which means the scheme check has to live in the probe itself.
    A base URL is operator input this process then fetches."""
    probe = api.container.endpoint_probe
    for url in (
        "file:///etc/passwd",
        "unix:///var/run/docker.sock",
        "ftp://example.test/x",
        "/etc/passwd",
        "",
    ):
        outcome = await probe.probe(url)
        assert outcome.reachable is False
        assert outcome.present is False
        assert "http(s)" in outcome.detail


@pytest.mark.req("REQ-018", "REQ-036")
async def test_the_discovery_probe_carries_no_credential(api):
    """It is an unauthenticated GET by design. Sending a credential to whatever
    an operator typed into the Basics field is exactly the mistake this must not
    make, and it never has one to send: discovery runs before a binding exists."""
    seen: list[dict] = []

    async def record(request):
        seen.append({key.lower(): value for key, value in request.headers.items()})
        return JSONResponse({"ok": True})

    harness = _harness(api)
    harness.mount("http://probe-target.test", Starlette(routes=[Route("/x", record)]))

    outcome = await api.container.endpoint_probe.probe("http://probe-target.test/x")
    assert outcome.served is True
    assert seen and "authorization" not in seen[0]
    assert "cookie" not in seen[0]
    assert not any("token" in value.lower() for value in seen[0].values())


# --- the floor as stored data -------------------------------------------------


@pytest.mark.req("REQ-018", "REQ-060")
async def test_a_doctored_profile_row_cannot_be_loaded_as_the_floor_would_refuse(api):
    """The floor is code *and* data now. Writing `auto` against a high-risk class
    straight into the database - past every route, service and validator - must
    still not produce a profile that says a rollback may auto-execute."""
    _harness(api)
    signup = api.signup()
    token = signup["tokens"]["access_token"]
    register_app(api, token)
    connect(api, token, "http-observe-actions", app_id=APP_ID)
    assert (
        api.client.post(
            f"/apps/{APP_ID}/actions/import", json={}, headers=api.auth_header(token)
        ).status_code
        == 200
    )

    connection = sqlite3.connect(api.db_path)
    try:
        raw = connection.execute(
            "select actions from onboarding_profiles where app_id = ?", (APP_ID,)
        ).fetchone()[0]
        actions = json.loads(raw)
        for action in actions:
            if action["action_class"] == "restart":
                action["execution_mode"] = "auto"
        connection.execute(
            "update onboarding_profiles set actions = ? where app_id = ?",
            (json.dumps(actions), APP_ID),
        )
        connection.commit()
    finally:
        connection.close()

    response = api.client.get(f"/apps/{APP_ID}", headers=api.auth_header(token))
    assert response.status_code == 422
    assert "auto-execute" in response.json()["detail"]


# --- credentials never come back out ------------------------------------------


@pytest.mark.req("REQ-015", "REQ-036")
async def test_nothing_the_wizard_writes_carries_a_credential(api):
    harness = _harness(api)
    harness.state.token = APP_TOKEN
    signup = api.signup()
    token = signup["tokens"]["access_token"]
    tenant_id = signup["org"]["id"]

    register_app(api, token)
    binding = connect(
        api,
        token,
        "http-observe-metrics",
        app_id=APP_ID,
        credentials={"token": APP_TOKEN},
    )
    api.client.put(
        f"/apps/{APP_ID}/mapping", json=SIGNAL_BODY, headers=api.auth_header(token)
    )
    api.client.post(
        f"/apps/{APP_ID}/connection-tests", json={}, headers=api.auth_header(token)
    )
    api.client.post(
        f"/apps/{APP_ID}/discover", json={}, headers=api.auth_header(token)
    )
    api.client.post(
        f"/apps/{APP_ID}/baseline", json={"samples": 3}, headers=api.auth_header(token)
    )

    events = api.client.get(
        "/events?limit=200", headers=api.auth_header(token)
    ).json()["events"]
    assert events, "the wizard writes to the timeline"
    assert APP_TOKEN not in json.dumps(events)
    assert APP_TOKEN not in json.dumps(api.audit_rows(tenant_id))

    detail = api.client.get(
        f"/apps/{APP_ID}", headers=api.auth_header(token)
    ).text
    assert APP_TOKEN not in detail
    health = api.client.get("/connectors/health", headers=api.auth_header(token)).text
    assert APP_TOKEN not in health

    scrape = api.client.get("/metrics").text
    assert tenant_id not in scrape
    assert APP_TOKEN not in scrape
    assert binding["id"] not in scrape


# Three GETs under /apps are not the wizard: they are what the app-detail screen
# and the proposal sheet are made of, read by the person who declares - who by
# the frozen roles matrix never holds manage_apps. Pinned as a literal list so a
# fourth cannot join them without somebody deciding it here, and asserted from
# the other side in tests/security/test_detection_boundary.py (DR-089).
RESPONDER_READS_UNDER_APPS = {
    # The list of applications and the application page are membership
    # reads (S10-T21): an invited Incident Commander holds no manage_apps
    # and still has to be able to open the application they were paged for.
    ("GET", "/apps"),
    ("GET", "/apps/{app_id}"),
    ("GET", "/apps/{app_id}/detection"),
    ("GET", "/apps/{app_id}/lifecycle"),
    ("GET", "/apps/{app_id}/change-window"),
}


# The execution policy also lives under /apps and is deliberately *not*
# manage_apps (S6-T01). The frozen roles matrix names `edit_policies` separately
# from `manage_apps` precisely because they are different jobs: pointing the
# platform at an application's URLs and holding its credentials is one, and
# deciding what the platform will let anybody do to that application is another.
# Both currently sit with the org owner, so nothing widens today - and pinning
# them here means a future org that splits the two roles gets the split it asked
# for rather than silently re-merging it.
#
# The reads split three ways for reasons the matrix already carries:
#   - GET .../policy carries no permission. Reading the rules is not changing
#     them, and a responder who cannot see why an action needs approval will ask
#     in chat instead of reading the screen.
#   - GET .../policy/versions carries `read_audit`, not `edit_policies`. An audit
#     trail only the editor can see is not an audit trail, and the Auditor holds
#     read_audit and nothing else.
#   - The two writes carry `edit_policies`.
#
# Asserted from the other side in tests/governance/test_policy_editor.py, where
# an incident commander is refused an edit (DR-107).
POLICY_ROUTES_UNDER_APPS = {
    ("GET", "/apps/{app_id}/policy"),
    ("GET", "/apps/{app_id}/policy/versions"),
    ("POST", "/apps/{app_id}/policy"),
    ("POST", "/apps/{app_id}/policy/dry-run"),
}

POLICY_ROUTE_PERMISSIONS = {
    ("GET", "/apps/{app_id}/policy"): None,
    ("GET", "/apps/{app_id}/policy/versions"): Permission.READ_AUDIT,
    ("POST", "/apps/{app_id}/policy"): Permission.EDIT_POLICIES,
    ("POST", "/apps/{app_id}/policy/dry-run"): Permission.EDIT_POLICIES,
}


@pytest.mark.req("REQ-012", "REQ-036")
async def test_every_onboarding_route_is_manage_apps(api):
    """The wizard holds credentials, edits policy and points the platform at
    URLs. All of it is one operator job, and the roles matrix gives manage_apps
    to the org owner alone."""
    from signalforge.tests.security.test_authz_matrix import _walk_api_routes

    unguarded = []
    covered = 0
    seen_reads = set()
    seen_policy = set()
    for route in _walk_api_routes(api_app.routes):
        path = getattr(route, "path", "")
        if not path.startswith("/apps"):
            continue
        methods = getattr(route, "methods", set()) or set()
        keys = {(method, path) for method in methods}
        if keys & RESPONDER_READS_UNDER_APPS:
            seen_reads |= keys
            continue
        if keys & POLICY_ROUTES_UNDER_APPS:
            seen_policy |= keys & POLICY_ROUTES_UNDER_APPS
            names = _dependency_names(route.dependant)
            for key in keys & POLICY_ROUTES_UNDER_APPS:
                expected = POLICY_ROUTE_PERMISSIONS[key]
                if expected is None:
                    if names:
                        unguarded.append(
                            f"{key} carries {names} and is documented as a read"
                        )
                elif expected.value not in names:
                    unguarded.append(
                        f"{key} should carry {expected.value}, carries {names}"
                    )
            continue
        covered += 1
        names = _dependency_names(route.dependant)
        if Permission.MANAGE_APPS.value not in names:
            unguarded.append(f"{route.methods} {path}")
    assert covered >= 12, "the walk must cover the wizard's routes"
    assert not unguarded, f"routes under /apps guarded wrongly: {unguarded}"
    assert seen_reads == RESPONDER_READS_UNDER_APPS, (
        "a responder read named here no longer exists; the exemption must not "
        f"outlive its route: {RESPONDER_READS_UNDER_APPS - seen_reads}"
    )
    assert seen_policy == POLICY_ROUTES_UNDER_APPS, (
        "a policy route named here no longer exists; the exemption must not "
        f"outlive its route: {POLICY_ROUTES_UNDER_APPS - seen_policy}"
    )


def _dependency_names(dependant) -> set[str]:
    """The permission a route's RBAC dependency closes over.

    Read from the factory's closure rather than from a registry, so a route that
    is guarded by the wrong permission is caught rather than merely a route that
    is unguarded.
    """
    found: set[str] = set()
    closure = getattr(dependant.call, "__closure__", None) or ()
    for cell in closure:
        value = cell.cell_contents
        if isinstance(value, Permission):
            found.add(value.value)
    for sub in dependant.dependencies:
        found |= _dependency_names(sub)
    return found


@pytest.mark.req("REQ-018", "REQ-036")
async def test_a_local_discovery_target_cannot_be_pointed_off_http(api):
    """The well-known local services are configuration, and configuration is
    input too: a vendor URL that is not http(s) is refused by the probe rather
    than followed."""
    _harness(api)
    signup = api.signup()
    token = signup["tokens"]["access_token"]
    register_app(api, token)
    api.container.discovery.local_services = (
        LocalService(
            vendor="prometheus",
            label="Prometheus",
            url="file:///etc",
            health_path="/passwd",
        ),
    )

    report = api.client.post(
        f"/apps/{APP_ID}/discover", json={}, headers=api.auth_header(token)
    ).json()
    local = next(finding for finding in report["findings"] if finding["source"] == "local")
    assert local["present"] is False
    assert local["candidates"] == []
