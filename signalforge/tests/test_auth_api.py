"""S1-T03: /auth flows - signup, login, refresh rotation, revocation (REQ-010)."""

import pytest


@pytest.mark.req("REQ-010")
def test_signup_creates_org_owner_with_working_tokens(api):
    body = api.signup()
    assert body["org"]["slug"] == "acme-corp"
    assert [b["role"] for b in body["user"]["bindings"]] == ["org_owner"]
    me = api.client.get("/auth/me", headers=api.auth_header(body["tokens"]["access_token"]))
    assert me.status_code == 200
    assert "manage_org" in me.json()["permissions"]
    assert body["tokens"]["expires_in"] == 900
    assert "audit.auth.signup" in api.audit_topics(body["org"]["id"])


@pytest.mark.req("REQ-010")
def test_login_success_and_generic_failure(api):
    body = api.signup()
    ok = api.client.post(
        "/auth/login",
        json={"org_slug": "acme-corp", "email": "owner@acme.io",
              "password": "a-strong-password"},
    )
    assert ok.status_code == 200

    bad = api.client.post(
        "/auth/login",
        json={"org_slug": "acme-corp", "email": "owner@acme.io", "password": "wrong"},
    )
    assert bad.status_code == 401
    assert bad.json() == {"detail": "authentication failed"}, "message must stay generic"

    unknown_org = api.client.post(
        "/auth/login",
        json={"org_slug": "no-such-org", "email": "owner@acme.io", "password": "x"},
    )
    assert unknown_org.status_code == 401, "unknown org must look like bad credentials"

    topics = api.audit_topics(body["org"]["id"])
    assert "audit.auth.login_succeeded" in topics
    assert "audit.auth.login_failed" in topics


@pytest.mark.req("REQ-010")
def test_login_rate_limited_with_retry_after(api):
    body = api.signup()
    for _ in range(5):
        api.client.post(
            "/auth/login",
            json={"org_slug": "acme-corp", "email": "owner@acme.io", "password": "wrong"},
        )
    blocked = api.client.post(
        "/auth/login",
        json={"org_slug": "acme-corp", "email": "owner@acme.io",
              "password": "a-strong-password"},
    )
    assert blocked.status_code == 429
    assert int(blocked.headers["Retry-After"]) > 0
    assert "audit.auth.login_rate_limited" in api.audit_topics(body["org"]["id"])


@pytest.mark.req("REQ-010")
def test_refresh_rotates_and_reuse_revokes_the_family(api):
    body = api.signup()
    first_refresh = body["tokens"]["refresh_token"]

    rotated = api.client.post("/auth/refresh", json={"refresh_token": first_refresh})
    assert rotated.status_code == 200
    second_refresh = rotated.json()["refresh_token"]
    assert second_refresh != first_refresh, "refresh must rotate"

    reuse = api.client.post("/auth/refresh", json={"refresh_token": first_refresh})
    assert reuse.status_code == 401, "reusing a rotated token must fail"

    family_dead = api.client.post("/auth/refresh", json={"refresh_token": second_refresh})
    assert family_dead.status_code == 401, "reuse must revoke the whole session family"
    assert "audit.auth.refresh_reuse_detected" in api.audit_topics(body["org"]["id"])


@pytest.mark.req("REQ-010")
def test_logout_revokes_access_immediately(api):
    body = api.signup()
    headers = api.auth_header(body["tokens"]["access_token"])
    assert api.client.get("/auth/me", headers=headers).status_code == 200
    assert api.client.post("/auth/logout", headers=headers).status_code == 204
    assert api.client.get("/auth/me", headers=headers).status_code == 401, (
        "the session is the revocation store: a live JWT dies with it"
    )
    refresh_dead = api.client.post(
        "/auth/refresh", json={"refresh_token": body["tokens"]["refresh_token"]}
    )
    assert refresh_dead.status_code == 401


@pytest.mark.req("REQ-010")
def test_access_token_expires_after_15_minutes(api):
    body = api.signup()
    headers = api.auth_header(body["tokens"]["access_token"])
    api.clock.advance(minutes=14)
    assert api.client.get("/auth/me", headers=headers).status_code == 200
    api.clock.advance(minutes=2)
    assert api.client.get("/auth/me", headers=headers).status_code == 401


@pytest.mark.req("REQ-010")
def test_missing_or_garbage_token_is_401(api):
    assert api.client.get("/auth/me").status_code == 401
    assert api.client.get("/auth/me", headers=api.auth_header("garbage")).status_code == 401
    assert api.client.get("/users").status_code == 401


@pytest.mark.req("REQ-010")
def test_weak_password_rejected_on_signup(api):
    response = api.client.post(
        "/auth/signup",
        json={"org_name": "Acme", "email": "a@b.co", "password": "short",
              "display_name": "A"},
    )
    assert response.status_code == 422
