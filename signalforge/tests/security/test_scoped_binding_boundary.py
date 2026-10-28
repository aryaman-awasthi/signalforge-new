"""A per-application binding stays per-application (REQ-012, REQ-013).

The RBAC guard on routes that do not name an application in their path used to
read `?app_id=` from the caller and let a binding scoped to *that* application
satisfy the check - while the handler behind it acted on a resource the guard
never compared to that application. Every role can be bound per application,
so an `org_owner` scoped to `payments` could write itself an org-wide binding
by naming `payments` in the query string, and a `manage_apps` holder on one
application could rotate another application's ingest key or delete its
connector. These tests pin the repaired rule:

- a route whose resource belongs to the tenant is decided org-wide, whatever
  the query string says;
- a per-application binding counts only where the handler acts on the very
  `app_id` the guard read (declaring an incident, listing one application's
  connectors or keys);
- a route that names an incident is decided against that incident's
  application, as the war-room routes already were.
"""

from __future__ import annotations

import pytest

from signalforge.tests.adapters.conftest import connect, observe  # noqa: F401
from signalforge.tests.incidents.conftest import commander, declare
from signalforge.tests.ingest.conftest import issue_key
from signalforge.tests.onboarding.conftest import register_app

MINE = "payments"
THEIRS = "billing"


def _scoped(api, owner_token: str, *, role: str, email: str) -> tuple[str, str]:
    """A person holding `role` on MINE only. Returns (token, user id)."""
    joined = api.invite_and_accept(
        owner_token, email=email, roles=[role], app_id=MINE, display_name=email
    )
    return joined["tokens"]["access_token"], joined["user"]["id"]


def _denied(response) -> None:
    assert response.status_code == 403, response.text
    assert response.json().get("detail") == "permission denied"


@pytest.mark.req("REQ-012")
def test_an_owner_scoped_to_one_application_cannot_grant_itself_the_organisation(api):
    owner = api.signup()
    owner_token = owner["tokens"]["access_token"]
    register_app(api, owner_token, app_id=MINE, name="Payments")
    token, user_id = _scoped(api, owner_token, role="org_owner", email="scoped@acme.io")
    headers = api.auth_header(token)

    # The three organisation writes, each with the query string that used to
    # unlock them. None of them is about an application.
    _denied(
        api.client.put(
            f"/users/{user_id}/roles?app_id={MINE}",
            json={"bindings": [{"role": "org_owner", "app_id": None}]},
            headers=headers,
        )
    )
    _denied(
        api.client.post(
            f"/invites?app_id={MINE}",
            json={"email": "friend@acme.io", "roles": ["org_owner"], "app_id": None},
            headers=headers,
        )
    )
    _denied(
        api.client.patch(
            f"/orgs/current?app_id={MINE}", json={"name": "Taken"}, headers=headers
        )
    )

    # The bindings the person holds are exactly what the owner granted.
    me = api.client.get("/auth/me", headers=headers)
    assert me.status_code == 200, me.text
    assert [(b["role"], b["app_id"]) for b in me.json()["bindings"]] == [
        ("org_owner", MINE)
    ]


@pytest.mark.req("REQ-012", "REQ-013")
def test_manage_apps_on_one_application_does_not_reach_another_applications_integrations(
    api, observe
):
    owner = api.signup()
    owner_token = owner["tokens"]["access_token"]
    register_app(api, owner_token, app_id=MINE, name="Payments")
    register_app(api, owner_token, app_id=THEIRS, name="Billing")
    mine = connect(api, owner_token, "itsm-lite", app_id=MINE)
    theirs = connect(api, owner_token, "itsm-lite", app_id=THEIRS)
    their_key = issue_key(api, owner_token, stream="alerts", app_id=THEIRS)
    token, _user_id = _scoped(api, owner_token, role="org_owner", email="ops@acme.io")
    headers = api.auth_header(token)

    # Where the handler acts on the application the guard read, the scoped
    # binding applies - and only to that application.
    listed = api.client.get(f"/connectors?app_id={MINE}", headers=headers)
    assert listed.status_code == 200, listed.text
    assert {row["id"] for row in listed.json()} == {mine["id"]}
    _denied(api.client.get("/connectors", headers=headers))
    _denied(api.client.get(f"/connectors?app_id={THEIRS}", headers=headers))

    # Resources named by id belong to the tenant as far as the guard can tell,
    # so the query string buys nothing.
    _denied(api.client.get(f"/connectors/{theirs['id']}?app_id={MINE}", headers=headers))
    _denied(
        api.client.patch(
            f"/connectors/{theirs['id']}?app_id={MINE}",
            json={"enabled": False},
            headers=headers,
        )
    )
    _denied(api.client.delete(f"/connectors/{theirs['id']}?app_id={MINE}", headers=headers))
    _denied(
        api.client.post(
            f"/ingest-keys/{their_key['id']}/rotate?app_id={MINE}", headers=headers
        )
    )
    _denied(api.client.delete(f"/ingest-keys/{their_key['id']}?app_id={MINE}", headers=headers))
    _denied(
        api.client.post(
            f"/connectors?app_id={MINE}",
            json={
                "app_id": THEIRS,
                "adapter_id": "itsm-lite",
                "name": "planted",
                "credential_scope": "read_only",
                "config": {},
                "credentials": {},
            },
            headers=headers,
        )
    )
    _denied(
        api.client.post(
            f"/ingest-keys?app_id={MINE}",
            json={"app_id": THEIRS, "stream": "alerts", "name": "planted"},
            headers=headers,
        )
    )
    # Nothing moved.
    still = api.client.get(
        f"/connectors/{theirs['id']}", headers=api.auth_header(owner_token)
    )
    assert still.status_code == 200 and still.json()["enabled"] is True
    _denied(api.client.get(f"/platform/dead-letters?app_id={MINE}", headers=headers))


@pytest.mark.req("REQ-012", "REQ-072")
def test_an_auditor_scoped_to_one_application_replays_only_its_incidents(api):
    owner = api.signup()
    owner_token = owner["tokens"]["access_token"]
    register_app(api, owner_token, app_id=MINE, name="Payments")
    register_app(api, owner_token, app_id=THEIRS, name="Billing")
    ic_token = commander(api, owner_token)["tokens"]["access_token"]
    ours = declare(api, ic_token, app_id=MINE, title="Payments down", severity="P1")
    theirs = declare(api, ic_token, app_id=THEIRS, title="Billing down", severity="P1")
    token, _user_id = _scoped(api, owner_token, role="auditor", email="audit@acme.io")
    headers = api.auth_header(token)

    own = api.client.get(f"/incidents/{ours['id']}/replay", headers=headers)
    assert own.status_code == 200, own.text
    # The query string that used to unlock the other application's record.
    _denied(api.client.get(f"/incidents/{theirs['id']}/replay?app_id={MINE}", headers=headers))
    _denied(api.client.get(f"/incidents/{theirs['id']}/pir?app_id={MINE}", headers=headers))
    _denied(api.client.get(f"/events?app_id={MINE}", headers=headers))


@pytest.mark.req("REQ-012")
def test_a_commander_scoped_to_one_application_declares_only_there(api):
    """The one place a per-application binding is meant to apply through the
    query string: the handler declares on exactly the `app_id` the guard read."""
    owner = api.signup()
    owner_token = owner["tokens"]["access_token"]
    register_app(api, owner_token, app_id=MINE, name="Payments")
    register_app(api, owner_token, app_id=THEIRS, name="Billing")
    ic = commander(api, owner_token, app_id=MINE)
    token = ic["tokens"]["access_token"]

    declared = declare(api, token, app_id=MINE, title="Payments down", severity="P1")
    assert declared["app_id"] == MINE
    declare(api, token, app_id=THEIRS, title="Billing down", severity="P1", expect=403)
