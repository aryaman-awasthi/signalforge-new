"""Security properties of the incident lifecycle surface (artifact 18, S4-T02).

Written failing-first for the two categories CLAUDE.md names: RBAC and tenant
isolation.

The lifecycle is the first surface in this platform where *reading* and
*writing* are guarded differently, and the reason is in the frozen roles matrix:
the SME - the person most likely to be paged - holds no permission at all
(`ROLE_PERMISSIONS[Role.SME] == frozenset()`). Any permission on the read path
would exclude exactly the people an incident exists to reach. So the reads are
session-scoped to the caller's own tenant with no permission, on the same
reasoning as `/ws` (DR-031), and every write names one of the three incident
permissions. Both halves are asserted here rather than described.
"""

from __future__ import annotations

import json

import pytest

from signalforge.api.main import app as api_app
from signalforge.contracts.roles import ROLE_PERMISSIONS, Permission, Role
from signalforge.tests.incidents.conftest import (
    APP_ID,
    close_incident,
    commander,
    declare,
    lifecycle,
    onboard,
    owner,
    read_incident,
    set_severity,
)

pytestmark = pytest.mark.anyio

WRITE_ROUTES = {
    ("POST", "/incidents"): Permission.DECLARE_INCIDENT,
    ("PATCH", "/incidents/{incident_id}/severity"): Permission.CHANGE_SEVERITY,
    ("POST", "/incidents/{incident_id}/state"): Permission.DECLARE_INCIDENT,
    ("POST", "/incidents/{incident_id}/close"): Permission.CLOSE_INCIDENT,
    ("POST", "/incidents/{incident_id}/itsm-sync"): Permission.DECLARE_INCIDENT,
}

READ_ROUTES = {
    ("GET", "/incidents"),
    ("GET", "/incidents/{incident_id}"),
}


@pytest.mark.req("REQ-012", "REQ-043")
async def test_every_incident_write_names_the_permission_the_matrix_gives_it(api):
    """Pinned as an exact map, so a new write with the wrong permission - or with
    none - fails the build rather than a review."""
    from signalforge.tests.security.test_authz_matrix import _walk_api_routes
    from signalforge.tests.security.test_onboarding_boundary import _dependency_names

    found: dict[tuple[str, str], set[str]] = {}
    for route in _walk_api_routes(api_app.routes):
        path = getattr(route, "path", "")
        for method in getattr(route, "methods", set()) or set():
            if (method, path) in WRITE_ROUTES or (method, path) in READ_ROUTES:
                found[(method, path)] = _dependency_names(route.dependant)

    missing = set(WRITE_ROUTES) - set(found)
    assert not missing, f"incident write routes missing: {sorted(missing)}"
    for key, permission in WRITE_ROUTES.items():
        assert permission.value in found[key], f"{key} is not behind {permission.value}"

    for key in READ_ROUTES:
        assert key in found, f"incident read route missing: {key}"
        assert not found[key], (
            f"{key} carries a permission. The roles matrix gives the SME none "
            "(ROLE_PERMISSIONS[Role.SME] is empty), so any permission on the read "
            "path excludes the people who get paged."
        )
    assert ROLE_PERMISSIONS[Role.SME] == frozenset(), (
        "the reasoning above depends on this being true in the frozen matrix"
    )


@pytest.mark.req("REQ-013", "REQ-043")
async def test_another_tenants_incident_is_indistinguishable_from_one_that_does_not_exist(
    api,
):
    """Written failing-first. Both tenants call their application `payments`."""
    owner_a, _tenant_a = owner(api)
    onboard(api, owner_a)
    ic_a = commander(api, owner_a)
    incident = declare(api, ic_a["tokens"]["access_token"], severity="P0")

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
    owner_b = signed.json()["tokens"]["access_token"]

    read_incident(api, owner_b, incident["id"], expect=404)
    listing = api.client.get("/incidents", headers=api.auth_header(owner_b)).json()
    assert listing["count"] == 0 and listing["incidents"] == []

    # Even holding the permission in its own tenant, B cannot move A's incident.
    ic_b = commander(api, owner_b, email="ic@other.io")
    set_severity(
        api, ic_b["tokens"]["access_token"], incident["id"], "P2", expect=404
    )
    close_incident(api, ic_b["tokens"]["access_token"], incident["id"], expect=404)

    # And A's incident is untouched.
    assert read_incident(api, owner_a, incident["id"])["severity"] == "P0"


@pytest.mark.req("REQ-012", "REQ-013")
async def test_a_commander_narrowed_to_one_application_is_refused_on_another(api):
    """Permission change mid-flow. What was already declared stays declared; the
    next write against an application they no longer cover is refused."""
    owner_token, _tenant = owner(api)
    onboard(api, owner_token, app_id=APP_ID)
    onboard(api, owner_token, app_id="settlement")

    ic = commander(api, owner_token, app_id=APP_ID)
    token = ic["tokens"]["access_token"]

    incident = declare(api, token, app_id=APP_ID, severity="P1")
    declare(api, token, app_id="settlement", severity="P1", expect=403)

    rebind = api.client.put(
        f"/users/{ic['user']['id']}/roles",
        json={"bindings": [{"role": "incident_commander", "app_id": "settlement"}]},
        headers=api.auth_header(owner_token),
    )
    assert rebind.status_code == 200, rebind.text

    set_severity(api, token, incident["id"], "P0", expect=403)
    # Nothing was lost: the incident is intact and still readable by them.
    assert read_incident(api, token, incident["id"])["severity"] == "P1"
    assert declare(api, token, app_id="settlement", severity="P2")["app_id"] == "settlement"


@pytest.mark.req("REQ-012", "REQ-043")
async def test_an_sme_can_read_the_incident_they_will_be_paged_to_and_write_nothing(api):
    """The role with no permissions is the role that answers the page."""
    owner_token, _tenant = owner(api)
    onboard(api, owner_token)
    ic = commander(api, owner_token)
    incident = declare(api, ic["tokens"]["access_token"], severity="P0")

    sme = api.invite_and_accept(
        owner_token, email="sme@acme.io", roles=["sme"], display_name="Sam"
    )
    token = sme["tokens"]["access_token"]

    body = read_incident(api, token, incident["id"])
    assert body["reference"] == incident["reference"]
    assert body["severity"] == "P0"

    set_severity(api, token, incident["id"], "P2", expect=403)
    close_incident(api, token, incident["id"], expect=403)
    declare(api, token, expect=403)
    # The application's own view is a read, so the SME gets it: they were paged
    # to this application and the first question anybody asks is "what is it
    # doing". Reading is not deciding, and there is nothing here to decide with
    # (DR-089).
    reading = lifecycle(api, token)
    assert reading["app_id"] == APP_ID
    assert reading["declare_requires_human"] is True
    # The operator half of the same screen stays operator work: an SME cannot
    # force an evaluation or read the raw buckets.
    assert (
        api.client.post(
            f"/apps/{APP_ID}/detection/evaluate", json={}, headers=api.auth_header(token)
        ).status_code
        == 403
    )
    assert (
        api.client.get(
            f"/apps/{APP_ID}/rollups", headers=api.auth_header(token)
        ).status_code
        == 403
    )


@pytest.mark.req("REQ-025", "REQ-043")
async def test_the_scrape_counts_incidents_and_can_name_none_of_them(api):
    owner_token, tenant_id = owner(api)
    onboard(api, owner_token)
    ic = commander(api, owner_token)
    incident = declare(api, ic["tokens"]["access_token"], severity="P0")

    scrape = api.client.get("/metrics").text
    assert "signalforge_incident_transitions_total" in scrape
    assert "signalforge_incidents_open" in scrape
    for secret in (tenant_id, incident["id"], incident["reference"], APP_ID):
        assert secret not in scrape, (
            "/metrics has no session; an incident id belongs to one tenant"
        )


@pytest.mark.req("REQ-015", "REQ-043")
async def test_nothing_the_lifecycle_writes_carries_a_connector_credential(api):
    """The ITSM sync decrypts a binding's credential on every attempt."""
    from signalforge.tests.adapters.conftest import connect

    owner_token, tenant_id = owner(api)
    onboard(api, owner_token)
    connect(api, owner_token, "itsm-lite", app_id=APP_ID)
    connect(
        api,
        owner_token,
        "http-observe-metrics",
        app_id=APP_ID,
        credentials={"token": "s3cr3t-metrics-token"},
    )
    ic = commander(api, owner_token)
    token = ic["tokens"]["access_token"]
    incident = declare(api, token, severity="P0")

    surfaces = [
        json.dumps(read_incident(api, token, incident["id"])),
        api.client.get("/events?limit=300", headers=api.auth_header(owner_token)).text,
        json.dumps(api.audit_rows(tenant_id)),
        json.dumps(lifecycle(api, owner_token)),
        api.client.get("/metrics").text,
    ]
    for surface in surfaces:
        assert "s3cr3t-metrics-token" not in surface
