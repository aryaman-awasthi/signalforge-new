"""S3-T04: onboarding is tenant-scoped and permission-gated (REQ-013, REQ-012).

Written failing-first. Two properties are pinned here and neither may ever
regress:

1. **An application belongs to a tenant.** Two tenants may register the same
   `app_id` - it is a slug somebody chose, not a global identifier - and neither
   can read, edit or even distinguish the other's. A cross-tenant read answers
   the same 404 as a read of something that does not exist anywhere.
2. **Onboarding is manage_apps work**, and a per-app manage_apps binding reaches
   exactly one application. That is the case the wizard makes real: its routes
   name the application in the *path*, so the RBAC dependency has to see it
   there or a scoped operator would silently be evaluated org-wide.
"""

import pytest

from signalforge.tests.onboarding.conftest import (  # noqa: F401
    APP_ID,
    SIMPLE_TOPOLOGY,
    SIGNAL_BODY,
    observe,
    owner,
    put_mapping,
    register_app,
    upload_topology,
)


def _second_tenant(api) -> str:
    signed = api.signup(
        org="Beta Industries", email="owner@beta.io", display_name="Beta Owner"
    )
    return signed["tokens"]["access_token"]


@pytest.mark.req("REQ-013")
def test_two_tenants_may_use_the_same_app_id_and_never_see_each_other(api, observe):
    token_a, _tenant_a = owner(api)
    token_b = _second_tenant(api)

    register_app(api, token_a, name="A Checkout")
    register_app(api, token_b, name="B Checkout")

    listing_a = api.client.get("/apps", headers=api.auth_header(token_a)).json()
    listing_b = api.client.get("/apps", headers=api.auth_header(token_b)).json()
    assert [app["name"] for app in listing_a] == ["A Checkout"]
    assert [app["name"] for app in listing_b] == ["B Checkout"]

    put_mapping(api, token_a, **SIGNAL_BODY)
    profile_b = api.client.get(
        f"/apps/{APP_ID}", headers=api.auth_header(token_b)
    ).json()
    assert profile_b["profile"]["signals"] == [], (
        "tenant B's profile is its own, even under the same app_id"
    )


@pytest.mark.req("REQ-013")
def test_a_cross_tenant_read_is_indistinguishable_from_nothing(api, observe):
    token_a, _tenant_a = owner(api)
    token_b = _second_tenant(api)
    register_app(api, token_a)

    theirs = api.client.get(f"/apps/{APP_ID}", headers=api.auth_header(token_b))
    nobodys = api.client.get("/apps/ghost-app", headers=api.auth_header(token_b))
    assert theirs.status_code == 404 and nobodys.status_code == 404
    assert theirs.json()["detail"].replace(APP_ID, "X") == nobodys.json()[
        "detail"
    ].replace("ghost-app", "X")


@pytest.mark.req("REQ-013")
def test_a_cross_tenant_write_cannot_reach_another_tenants_application(api, observe):
    token_a, _tenant_a = owner(api)
    token_b = _second_tenant(api)
    register_app(api, token_a)
    upload_topology(api, token_a, SIMPLE_TOPOLOGY)

    put_mapping(api, token_b, **SIGNAL_BODY, expect=404)
    upload_topology(api, token_b, SIMPLE_TOPOLOGY, expect=404)
    assert (
        api.client.post(
            f"/apps/{APP_ID}/baseline", json={}, headers=api.auth_header(token_b)
        ).status_code
        == 404
    )

    listing = api.client.get(
        f"/apps/{APP_ID}/topology", headers=api.auth_header(token_a)
    ).json()
    assert len(listing["versions"]) == 1, "nothing tenant B did touched tenant A"


@pytest.mark.req("REQ-012")
def test_onboarding_requires_manage_apps_and_denials_are_audited(api, observe):
    token, tenant_id = owner(api)
    register_app(api, token)
    sme = api.invite_and_accept(
        token, email="sme@acme.io", roles=["sme"], display_name="SME"
    )["tokens"]["access_token"]

    denied = [
        api.client.post(
            "/apps", json={"app_id": "other-app", "name": "Other"},
            headers=api.auth_header(sme),
        ),
        api.client.put(
            f"/apps/{APP_ID}/mapping", json=SIGNAL_BODY, headers=api.auth_header(sme)
        ),
        api.client.put(
            f"/apps/{APP_ID}/topology",
            json={"document": SIMPLE_TOPOLOGY},
            headers=api.auth_header(sme),
        ),
        api.client.get(f"/apps/{APP_ID}/readiness", headers=api.auth_header(sme)),
        api.client.post(
            f"/apps/{APP_ID}/discover", json={}, headers=api.auth_header(sme)
        ),
    ]
    assert [response.status_code for response in denied] == [403] * len(denied)
    for response in denied:
        payload = response.json()
        assert payload["detail"] == "permission denied"
        assert payload["permission"] == "manage_apps"

    denials = [
        row for row in api.audit_rows(tenant_id) if row["topic"] == "audit.authz.denied"
    ]
    assert len(denials) == len(denied)


@pytest.mark.req("REQ-012")
def test_a_per_app_manage_apps_binding_reaches_exactly_one_application(api, observe):
    token, _tenant_id = owner(api)
    register_app(api, token, app_id="checkout", name="Checkout")
    register_app(api, token, app_id="ledger", name="Ledger")

    scoped = api.invite_and_accept(
        token,
        email="app-owner@acme.io",
        roles=["org_owner"],
        app_id="checkout",
        display_name="App Owner",
    )["tokens"]["access_token"]

    allowed = api.client.put(
        "/apps/checkout/mapping", json=SIGNAL_BODY, headers=api.auth_header(scoped)
    )
    assert allowed.status_code == 200, allowed.text

    refused = api.client.put(
        "/apps/ledger/mapping", json=SIGNAL_BODY, headers=api.auth_header(scoped)
    )
    assert refused.status_code == 403
    assert refused.json()["permission"] == "manage_apps"


@pytest.mark.req("REQ-012", "REQ-013")
def test_permission_change_mid_flow_stops_the_next_step(api, observe):
    """The operator starts the wizard, their binding is narrowed, and the next
    step is refused - while everything they already saved stays."""
    token, _tenant_id = owner(api)
    register_app(api, token, app_id="checkout")
    register_app(api, token, app_id="ledger")

    member = api.invite_and_accept(
        token, email="ops@acme.io", roles=["org_owner"], display_name="Ops"
    )
    ops_token = member["tokens"]["access_token"]
    put_mapping(api, ops_token, "ledger", **SIGNAL_BODY)

    narrowed = api.client.put(
        f"/users/{member['user']['id']}/roles",
        json={"bindings": [{"role": "org_owner", "app_id": "checkout"}]},
        headers=api.auth_header(token),
    )
    assert narrowed.status_code == 200, narrowed.text

    blocked = api.client.put(
        "/apps/ledger/topology",
        json={"document": SIMPLE_TOPOLOGY},
        headers=api.auth_header(ops_token),
    )
    assert blocked.status_code == 403

    kept = api.client.get("/apps/ledger", headers=api.auth_header(token)).json()
    assert len(kept["profile"]["signals"]) == 2, "what was saved before stays saved"
