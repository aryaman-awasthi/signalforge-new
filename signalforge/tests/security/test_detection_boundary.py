"""Security properties of the detection surface (artifact 18, S4-T01).

Detection is the first thing in this platform that reaches an opinion on its
own. Three properties keep that safe, and each one is asserted rather than
designed:

- **It proposes and never declares.** Every event it writes is on the signal
  plane with a system actor, the proposal carries `requires_human` true and
  `declared` false, and the value object refuses to be constructed any other way.
- **It reads only the asking tenant's measurements.** A second database is still
  a database with tenant data in it, and a cross-tenant read of a rollup would
  be as bad as a cross-tenant read of an incident.
- **It handles credentials it never shows.** The poller decrypts a connector's
  token on every scrape; nothing it writes - rollup, state, event, audit entry
  or scrape - may carry it.
"""

from __future__ import annotations

import json

import pytest

from signalforge.api.main import app as api_app
from signalforge.contracts.roles import Permission
from signalforge.tests.adapters.conftest import (  # noqa: F401  (fixture re-exported)
    connect,
    observe,
)
from signalforge.tests.detection.conftest import (
    APP_ID,
    REGION_A,
    REGION_B,
    detection,
    exposition,
    onboard,
    owner,
    rollups,
    steady,
    tick,
    topics,
)
from signalforge.tests.onboarding.conftest import register_app

pytestmark = pytest.mark.anyio

APP_TOKEN = "s3cr3t-metrics-token"

DETECTION_ROUTES = {
    ("GET", "/apps/{app_id}/detection"),
    ("POST", "/apps/{app_id}/detection/evaluate"),
    ("GET", "/apps/{app_id}/rollups"),
}


@pytest.mark.req("REQ-012", "REQ-040")
async def test_every_detection_route_is_behind_manage_apps(api):
    """Detection reads an application's telemetry and can be made to run a
    scrape. It is the same operator job the rest of `/apps` is."""
    from signalforge.tests.security.test_authz_matrix import _walk_api_routes
    from signalforge.tests.security.test_onboarding_boundary import _dependency_names

    found = set()
    for route in _walk_api_routes(api_app.routes):
        path = getattr(route, "path", "")
        for method in getattr(route, "methods", set()) or set():
            if (method, path) not in DETECTION_ROUTES:
                continue
            found.add((method, path))
            names = _dependency_names(route.dependant)
            assert Permission.MANAGE_APPS.value in names, f"{method} {path}"
    assert found == DETECTION_ROUTES, f"missing detection routes: {DETECTION_ROUTES - found}"


@pytest.mark.req("REQ-013", "REQ-040")
async def test_another_tenants_measurements_are_never_visible(api, observe):
    """Written failing-first. Both tenants call their application `payments`;
    a cross-tenant read answers the way a read of nothing does."""
    token_a, tenant_a = owner(api)
    onboard(api, token_a)
    await steady(api, observe, tenant_a, 3, evaluate=True)

    signed = api.client.post(
        "/auth/signup",
        json={
            "org_name": "Other Corp",
            "email": "other@other.io",
            "password": "another-strong-password",
            "display_name": "Other",
        },
    )
    assert signed.status_code == 201, signed.text
    token_b = signed.json()["tokens"]["access_token"]

    # Before tenant B registers anything of its own, the id it can see nothing
    # about answers 404 rather than confirming that it exists elsewhere.
    assert (
        api.client.get(
            f"/apps/{APP_ID}/detection", headers=api.auth_header(token_b)
        ).status_code
        == 404
    )
    assert (
        api.client.get(
            f"/apps/{APP_ID}/rollups", headers=api.auth_header(token_b)
        ).status_code
        == 404
    )

    onboard(api, token_b)
    assert rollups(api, token_b)["count"] == 0, "none of tenant A's buckets"
    body = detection(api, token_b)
    assert all(signal["status"] in ("unknown", "stale") for signal in body["signals"])
    assert rollups(api, token_a)["count"] == 18


@pytest.mark.req("REQ-012", "REQ-013")
async def test_an_operator_narrowed_to_one_application_is_refused_on_another(
    api, observe
):
    """Permission change mid-flow. What was already measured stays measured; the
    next read against an application the operator no longer covers is refused."""
    owner_token, _tenant = owner(api)
    onboard(api, owner_token, app_id=APP_ID)
    onboard(api, owner_token, app_id="settlement")

    member = api.invite_and_accept(
        owner_token, email="app-op@acme.io", roles=["org_owner"], app_id=APP_ID
    )
    token = member["tokens"]["access_token"]

    assert detection(api, token, APP_ID)["app_id"] == APP_ID
    assert (
        api.client.get(
            "/apps/settlement/detection", headers=api.auth_header(token)
        ).status_code
        == 403
    )

    # The owner re-binds them to the other application while they are working.
    rebind = api.client.put(
        f"/users/{member['user']['id']}/roles",
        json={"bindings": [{"role": "org_owner", "app_id": "settlement"}]},
        headers=api.auth_header(owner_token),
    )
    assert rebind.status_code == 200, rebind.text

    assert (
        api.client.get(
            f"/apps/{APP_ID}/detection", headers=api.auth_header(token)
        ).status_code
        == 403
    )
    assert detection(api, token, "settlement")["app_id"] == "settlement"
    # Nothing the narrowed operator could no longer read was destroyed by the
    # change: the owner still sees it.
    assert detection(api, owner_token, APP_ID)["app_id"] == APP_ID


@pytest.mark.req("REQ-015", "REQ-040")
async def test_nothing_detection_writes_carries_the_connectors_credential(
    api, observe
):
    """The poller decrypts a connector token on every scrape. Asserted
    empirically against every surface the token could reach."""
    token, tenant_id = owner(api)
    observe.state.token = APP_TOKEN
    register_app(api, token, app_id=APP_ID, name="Payments")
    connect(
        api,
        token,
        "http-observe-metrics",
        app_id=APP_ID,
        credentials={"token": APP_TOKEN},
    )
    api.client.put(
        f"/apps/{APP_ID}/mapping",
        json=__import__(
            "signalforge.tests.detection.conftest", fromlist=["MAPPING"]
        ).MAPPING,
        headers=api.auth_header(token),
    )
    await steady(api, observe, tenant_id, 12, evaluate=True)
    observe.state.metrics_text = exposition(
        success={REGION_A: 0.4, REGION_B: 0.4}, errors={REGION_A: 0.6, REGION_B: 0.6}
    )
    for _ in range(3):
        await tick(api, tenant_id, evaluate=True)

    surfaces = [
        api.client.get("/events?limit=300", headers=api.auth_header(token)).text,
        json.dumps(api.audit_rows(tenant_id)),
        json.dumps(detection(api, token)),
        json.dumps(rollups(api, token)),
        api.client.get("/metrics").text,
    ]
    for surface in surfaces:
        assert APP_TOKEN not in surface
    scrape = surfaces[-1]
    assert tenant_id not in scrape
    assert APP_ID not in scrape, (
        "an app_id is a tenant's own vocabulary and /metrics has no session"
    )
    assert "signalforge_detection_signals" in scrape
    assert "signalforge_detection_polls_total" in scrape


@pytest.mark.req("REQ-043", "REQ-046")
async def test_detection_proposes_on_the_signal_plane_and_declares_nothing(
    api, observe
):
    token, tenant_id = owner(api)
    onboard(api, token)
    await steady(api, observe, tenant_id, 10, evaluate=True)
    observe.state.metrics_text = exposition(
        success={REGION_A: 0.45, REGION_B: 0.45}, errors={REGION_A: 0.55, REGION_B: 0.55}
    )
    for _ in range(3):
        await tick(api, tenant_id, evaluate=True)

    machine = topics(
        api,
        token,
        "detection.stance.changed",
        "detection.anomaly.opened",
        "detection.severity.proposed",
        "detection.health.changed",
        "detection.slo.burn",
    )
    assert machine, "the escalation is on the timeline"
    for event in machine:
        assert event["plane"] == "signal", (
            "detection observes; the incident plane is what a declaration writes to"
        )
        assert event["actor"]["type"] == "system"
        assert event["source"]["authority"] == "observed"

    proposal = next(
        event
        for event in machine
        if event["topic"] == "detection.severity.proposed"
    )
    assert proposal["payload"]["declared"] is False
    assert proposal["payload"]["requires_human"] is True
    assert proposal["incident_id"] is None, "a proposal belongs to no incident"

    body = detection(api, token)
    assert body["severity"]["declared"] is False
    assert body["severity"]["requires_human"] is True
