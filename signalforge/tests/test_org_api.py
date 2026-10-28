"""S1-T03: org management - invites, role matrix, on-call, escalation
(REQ-010, REQ-012, REQ-014)."""

import pytest


@pytest.mark.req("REQ-010")
def test_invite_flow_end_to_end(api):
    owner = api.signup()
    token = owner["tokens"]["access_token"]

    member = api.invite_and_accept(token, email="sme@acme.io", roles=["sme"])
    assert [b["role"] for b in member["user"]["bindings"]] == ["sme"]

    listing = api.client.get("/invites", headers=api.auth_header(token))
    assert listing.status_code == 200
    (row,) = listing.json()
    assert row["status"] == "accepted"
    assert "token" not in row, "the raw token is shown exactly once, at creation"

    users = api.client.get("/users", headers=api.auth_header(token)).json()
    assert sorted(u["email"] for u in users) == ["owner@acme.io", "sme@acme.io"]

    duplicate = api.client.post(
        "/invites", json={"email": "sme@acme.io", "roles": ["sme"]},
        headers=api.auth_header(token),
    )
    assert duplicate.status_code == 409

    topics = api.audit_topics(owner["org"]["id"])
    assert "audit.org.invite_created" in topics
    assert "audit.org.invite_accepted" in topics


@pytest.mark.req("REQ-010")
def test_invite_revocation_and_expiry(api):
    owner = api.signup()
    headers = api.auth_header(owner["tokens"]["access_token"])
    created = api.client.post(
        "/invites", json={"email": "late@acme.io", "roles": ["sme"]}, headers=headers
    ).json()

    api.clock.advance(days=8)  # invite TTL is 7 days
    stale = api.client.post(
        "/auth/accept-invite",
        json={"token": created["token"], "password": "another-strong-pw",
              "display_name": "Late"},
    )
    assert stale.status_code == 404, "expired invites cannot be accepted"

    # 8 days later the owner's 15-minute access token is long dead - re-login.
    relogin = api.client.post(
        "/auth/login",
        json={"org_slug": "acme-corp", "email": "owner@acme.io",
              "password": "a-strong-password"},
    )
    headers = api.auth_header(relogin.json()["tokens"]["access_token"])

    fresh = api.client.post(
        "/invites", json={"email": "late@acme.io", "roles": ["sme"]}, headers=headers
    ).json()
    revoked = api.client.delete(f"/invites/{fresh['id']}", headers=headers)
    assert revoked.status_code == 200 and revoked.json()["status"] == "revoked"
    dead = api.client.post(
        "/auth/accept-invite",
        json={"token": fresh["token"], "password": "another-strong-pw",
              "display_name": "Late"},
    )
    assert dead.status_code == 404


@pytest.mark.req("REQ-012")
def test_role_assignment_changes_permissions(api):
    owner = api.signup()
    token = owner["tokens"]["access_token"]
    member = api.invite_and_accept(token, email="ic@acme.io", roles=["sme"])
    member_id = member["user"]["id"]

    updated = api.client.put(
        f"/users/{member_id}/roles",
        json={"bindings": [{"role": "incident_commander"}]},
        headers=api.auth_header(token),
    )
    assert updated.status_code == 200

    me = api.client.get(
        "/auth/me", headers=api.auth_header(member["tokens"]["access_token"])
    ).json()
    assert "declare_incident" in me["permissions"]
    assert "audit.org.roles_changed" in api.audit_topics(owner["org"]["id"])


@pytest.mark.req("REQ-012")
def test_cannot_remove_the_last_org_owner(api):
    owner = api.signup()
    response = api.client.put(
        f"/users/{owner['user']['id']}/roles",
        json={"bindings": [{"role": "sme"}]},
        headers=api.auth_header(owner["tokens"]["access_token"]),
    )
    assert response.status_code == 409


@pytest.mark.req("REQ-012")
def test_roles_matrix_reflects_the_frozen_contract(api):
    owner = api.signup()
    matrix = api.client.get(
        "/roles/matrix", headers=api.auth_header(owner["tokens"]["access_token"])
    ).json()["roles"]
    assert "approve_high_risk" in matrix["incident_commander"]
    assert matrix["auditor"] == ["read_audit"]
    assert "approve_high_risk" not in matrix["executive"]


@pytest.mark.req("REQ-014")
def test_oncall_rotation_crud_with_member_validation(api):
    owner = api.signup()
    token = owner["tokens"]["access_token"]
    member = api.invite_and_accept(token, email="ic@acme.io", roles=["incident_commander"])
    ids = [owner["user"]["id"], member["user"]["id"]]

    bad = api.client.post(
        "/oncall/rotations",
        json={"name": "primary", "user_ids": ids + ["ghost"]},
        headers=api.auth_header(token),
    )
    assert bad.status_code == 404, "rotation members must exist in the tenant"

    created = api.client.post(
        "/oncall/rotations",
        json={"name": "primary", "user_ids": ids, "handoff_interval_hours": 24},
        headers=api.auth_header(token),
    )
    assert created.status_code == 201
    rotation = created.json()
    assert rotation["user_ids"] == ids, "order is the on-call order"

    reordered = api.client.put(
        f"/oncall/rotations/{rotation['id']}",
        json={"name": "primary", "user_ids": ids[::-1], "handoff_interval_hours": 24},
        headers=api.auth_header(token),
    )
    assert reordered.status_code == 200
    assert reordered.json()["user_ids"] == ids[::-1]

    gone = api.client.delete(
        f"/oncall/rotations/{rotation['id']}", headers=api.auth_header(token)
    )
    assert gone.status_code == 204
    assert api.client.get("/oncall/rotations", headers=api.auth_header(token)).json() == []


@pytest.mark.req("REQ-014")
def test_escalation_policy_with_timers_and_test_fire(api):
    owner = api.signup()
    token = owner["tokens"]["access_token"]
    headers = api.auth_header(token)
    member = api.invite_and_accept(token, email="ic@acme.io", roles=["incident_commander"])

    rotation = api.client.post(
        "/oncall/rotations",
        json={"name": "primary", "user_ids": [member["user"]["id"]]},
        headers=headers,
    ).json()

    policy = api.client.post(
        "/escalation/policies",
        json={
            "name": "sev1",
            "steps": [
                {"target_type": "rotation", "target_id": rotation["id"], "timeout_s": 300},
                {"target_type": "user", "target_id": owner["user"]["id"], "timeout_s": 600},
            ],
        },
        headers=headers,
    )
    assert policy.status_code == 201
    assert policy.json()["page_offsets_s"] == [0, 300], "escalation timers are cumulative"

    fired = api.client.post(
        f"/escalation/policies/{policy.json()['id']}/test", headers=headers
    )
    assert fired.status_code == 200, "org owner holds run_drills"
    plan = fired.json()
    assert plan["simulated"] is True
    assert plan["plan"][0]["resolved_user_id"] == member["user"]["id"]
    assert plan["plan"][1]["page_at_offset_s"] == 300
    assert "audit.escalation.test_fired" in api.audit_topics(owner["org"]["id"])

    unknown_target = api.client.post(
        "/escalation/policies",
        json={"name": "bad", "steps": [
            {"target_type": "rotation", "target_id": "ghost", "timeout_s": 300}
        ]},
        headers=headers,
    )
    assert unknown_target.status_code == 404


@pytest.mark.req("REQ-012")
def test_executive_denied_ic_only_endpoint_with_clean_403_and_audit(api):
    """S1 acceptance demo: an Executive session hits an incident-command-only
    endpoint and gets a clean 403 plus an audit event."""
    owner = api.signup()
    token = owner["tokens"]["access_token"]
    executive = api.invite_and_accept(token, email="exec@acme.io", roles=["executive"])

    rotation = api.client.post(
        "/oncall/rotations",
        json={"name": "primary", "user_ids": [owner["user"]["id"]]},
        headers=api.auth_header(token),
    ).json()
    policy = api.client.post(
        "/escalation/policies",
        json={"name": "sev1", "steps": [
            {"target_type": "rotation", "target_id": rotation["id"], "timeout_s": 300}
        ]},
        headers=api.auth_header(token),
    ).json()

    denied = api.client.post(
        f"/escalation/policies/{policy['id']}/test",
        headers=api.auth_header(executive["tokens"]["access_token"]),
    )
    assert denied.status_code == 403
    body = denied.json()
    assert body["detail"] == "permission denied"
    assert body["permission"] == "run_drills"

    rows = [
        r for r in api.audit_rows(owner["org"]["id"])
        if r["topic"] == "audit.authz.denied"
    ]
    assert rows, "the denial must land in the audit chain"
    assert rows[-1]["actor_id"] == executive["user"]["id"]
    assert rows[-1]["payload"]["permission"] == "run_drills"


@pytest.mark.req("REQ-013")
def test_api_level_cross_tenant_isolation(api):
    org_a = api.signup(org="Org A", email="a@a.io")
    org_b = api.signup(org="Org B", email="b@b.io")
    headers_b = api.auth_header(org_b["tokens"]["access_token"])

    users_seen_by_b = api.client.get("/users", headers=headers_b).json()
    assert [u["email"] for u in users_seen_by_b] == ["b@b.io"]

    foreign_roles = api.client.put(
        f"/users/{org_a['user']['id']}/roles",
        json={"bindings": [{"role": "sme"}]},
        headers=headers_b,
    )
    assert foreign_roles.status_code == 404, "another tenant's user does not exist here"
