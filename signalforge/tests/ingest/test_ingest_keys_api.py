"""The ingest key management API (REQ-032, REQ-012, REQ-013, REQ-015).

Deciding which application may post telemetry into a tenant is manage_apps work,
so the whole surface sits behind that permission and behind the tenant
predicate. The other half of this file is the operator's view: what a key screen
can show about a sender - freshness, counters, the last refusal - and what it
can never show, which is the credential itself.
"""

from __future__ import annotations

import pytest

from signalforge.tests.ingest.conftest import (
    Sender,
    advance,
    batch,
    change,
    issue_key,
)

MANAGEMENT_CALLS = [
    ("GET", "/ingest-keys", None),
    ("POST", "/ingest-keys", {"app_id": "checkout", "stream": "changes", "name": "k"}),
]


@pytest.mark.req("REQ-032", "REQ-012")
def test_every_management_call_needs_manage_apps(api, owner):
    member = api.invite_and_accept(
        owner["token"], email="ic@acme.io", roles=["incident_commander"]
    )
    token = member["tokens"]["access_token"]
    for method, path, body in MANAGEMENT_CALLS:
        response = api.client.request(
            method, path, json=body, headers=api.auth_header(token)
        )
        assert response.status_code == 403, f"{method} {path}"
        assert response.json()["permission"] == "manage_apps"

    key = issue_key(api, owner["token"], stream="changes")
    for method, path in [
        ("GET", f"/ingest-keys/{key['id']}"),
        ("POST", f"/ingest-keys/{key['id']}/rotate"),
        ("DELETE", f"/ingest-keys/{key['id']}"),
    ]:
        response = api.client.request(method, path, headers=api.auth_header(token))
        assert response.status_code == 403, f"{method} {path}"


@pytest.mark.req("REQ-032", "REQ-013")
def test_a_key_is_invisible_and_unreachable_from_another_tenant(api):
    first = api.signup(org="One", email="one@acme.io")
    second = api.signup(org="Two", email="two@beta.io")
    key = issue_key(api, first["tokens"]["access_token"], stream="changes")
    other = api.auth_header(second["tokens"]["access_token"])

    assert api.client.get("/ingest-keys", headers=other).json() == []
    missing = api.client.get(f"/ingest-keys/{key['id']}", headers=other)
    assert missing.status_code == 404
    # The same message as an id that never existed: a 404 must not confirm that
    # one exists elsewhere.
    assert missing.json()["detail"] == f"ingest key {key['id']} not found"
    assert api.client.delete(f"/ingest-keys/{key['id']}", headers=other).status_code == 404
    assert (
        api.client.post(
            f"/ingest-keys/{key['id']}/rotate", headers=other
        ).status_code
        == 404
    )


@pytest.mark.req("REQ-032", "REQ-015")
def test_the_credential_is_returned_once_and_never_again(api, owner):
    issued = issue_key(api, owner["token"], stream="changes")
    assert issued["token"].startswith("sfi_")
    assert issued["secret"]

    fetched = api.client.get(
        f"/ingest-keys/{issued['id']}", headers=api.auth_header(owner["token"])
    ).json()
    assert "token" not in fetched and "secret" not in fetched
    assert fetched["token_prefix"] == issued["token"][:12]

    listed = api.client.get(
        "/ingest-keys", headers=api.auth_header(owner["token"])
    ).json()
    assert all("token" not in key and "secret" not in key for key in listed)


@pytest.mark.req("REQ-032", "REQ-015")
def test_the_token_is_stored_hashed_and_the_secret_encrypted(api, owner):
    import sqlite3

    issued = issue_key(api, owner["token"], stream="changes")
    conn = sqlite3.connect(api.db_path)
    try:
        stored = conn.execute(
            "select token_hash, token_prefix from ingest_keys where id = ?",
            (issued["id"],),
        ).fetchone()
        ciphertext = conn.execute(
            "select ciphertext from secrets where tenant_id = ? and name = ?",
            (owner["tenant_id"], f"ingest/{issued['id']}/secret"),
        ).fetchone()[0]
    finally:
        conn.close()

    assert issued["token"] not in stored[0]
    assert len(stored[0]) == 64
    assert issued["secret"].encode() not in ciphertext


@pytest.mark.req("REQ-032")
def test_the_listing_is_what_an_integration_health_screen_reads(api, owner):
    sender = Sender(api=api, key=issue_key(api, owner["token"], stream="changes"))
    listed = api.client.get(
        "/ingest-keys", headers=api.auth_header(owner["token"])
    ).json()[0]
    assert listed["path"] == "/api/v1/ingest/changes"
    assert listed["freshness_s"] is None, "nothing has arrived yet"
    assert (listed["accepted_count"], listed["last_event_at"]) == (0, None)

    sender.post(batch(change()))
    advance(api, minutes=5)
    listed = api.client.get(
        "/ingest-keys", headers=api.auth_header(owner["token"])
    ).json()[0]
    assert listed["accepted_count"] == 1
    assert listed["freshness_s"] == 300.0
    assert listed["last_error"] is None


@pytest.mark.req("REQ-032")
def test_revoked_keys_are_hidden_unless_asked_for(api, owner):
    key = issue_key(api, owner["token"], stream="changes")
    api.client.delete(
        f"/ingest-keys/{key['id']}", headers=api.auth_header(owner["token"])
    )
    header = api.auth_header(owner["token"])
    assert api.client.get("/ingest-keys", headers=header).json() == []
    history = api.client.get(
        "/ingest-keys", params={"include_revoked": "true"}, headers=header
    ).json()
    assert [k["id"] for k in history] == [key["id"]]
    assert history[0]["active"] is False and history[0]["revoked_by"] == owner["user_id"]


@pytest.mark.req("REQ-032")
def test_keys_filter_by_app_and_stream(api, owner):
    issue_key(api, owner["token"], stream="changes", app_id="checkout")
    issue_key(api, owner["token"], stream="alerts", app_id="checkout")
    issue_key(api, owner["token"], stream="changes", app_id="ledger")
    header = api.auth_header(owner["token"])

    by_app = api.client.get(
        "/ingest-keys", params={"app_id": "checkout"}, headers=header
    ).json()
    assert {k["stream"] for k in by_app} == {"changes", "alerts"}
    by_stream = api.client.get(
        "/ingest-keys", params={"stream": "changes"}, headers=header
    ).json()
    assert {k["app_id"] for k in by_stream} == {"checkout", "ledger"}


@pytest.mark.req("REQ-032")
def test_an_unknown_stream_is_refused_by_the_schema(api, owner):
    response = api.client.post(
        "/ingest-keys",
        json={"app_id": "checkout", "stream": "invoices", "name": "k"},
        headers=api.auth_header(owner["token"]),
    )
    assert response.status_code == 422


@pytest.mark.req("REQ-032")
def test_a_revoked_key_cannot_be_rotated(api, owner):
    key = issue_key(api, owner["token"], stream="changes")
    header = api.auth_header(owner["token"])
    api.client.delete(f"/ingest-keys/{key['id']}", headers=header)
    response = api.client.post(f"/ingest-keys/{key['id']}/rotate", headers=header)
    assert response.status_code == 422
    assert "revoked" in response.json()["detail"]


@pytest.mark.req("REQ-032", "REQ-016")
def test_the_key_lifecycle_is_audited_and_on_the_timeline(api, owner):
    key = issue_key(api, owner["token"], stream="changes")
    header = api.auth_header(owner["token"])
    api.client.post(f"/ingest-keys/{key['id']}/rotate", headers=header)
    api.client.delete(f"/ingest-keys/{key['id']}", headers=header)

    topics = [
        row["topic"]
        for row in api.audit_rows(owner["tenant_id"])
        if row["topic"].startswith("system.ingest.key")
    ]
    assert topics == [
        "system.ingest.key.issued",
        "system.ingest.key.rotated",
        "system.ingest.key.revoked",
    ]
    payloads = [
        row["payload"]
        for row in api.audit_rows(owner["tenant_id"])
        if row["topic"].startswith("system.ingest.key")
    ]
    for payload in payloads:
        assert "token" not in payload and "secret" not in payload
        assert payload["token_prefix"].startswith("sfi_")

    replayed = api.client.get(
        "/events",
        params=[("topics", "system.ingest.key.issued"), ("limit", "50")],
        headers=header,
    ).json()["events"]
    assert len(replayed) == 1
    assert replayed[0]["payload"]["stream"] == "changes"


@pytest.mark.req("REQ-032")
def test_a_partially_written_key_leaves_nothing_behind(api, owner, monkeypatch):
    """Partial completion: the key row, its encrypted secret and its audit entry
    are one commit. If the secret write fails there must be no key, or a sender
    would hold a token that can never produce a valid signature."""
    import sqlite3

    from signalforge.infrastructure.security.secrets import FernetSecretStore

    async def explode(self, tenant_id, name, value):
        raise RuntimeError("secret store unavailable")

    monkeypatch.setattr(FernetSecretStore, "put", explode)
    with pytest.raises(RuntimeError):
        api.client.post(
            "/ingest-keys",
            json={"app_id": "checkout", "stream": "changes", "name": "doomed"},
            headers=api.auth_header(owner["token"]),
        )

    conn = sqlite3.connect(api.db_path)
    try:
        keys = conn.execute("select count(*) from ingest_keys").fetchone()[0]
        audits = conn.execute(
            "select count(*) from audit_log where topic = 'system.ingest.key.issued'"
        ).fetchone()[0]
    finally:
        conn.close()
    assert (keys, audits) == (0, 0)
