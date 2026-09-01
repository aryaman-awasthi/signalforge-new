"""Ingest authentication: bearer token, HMAC and the replay window (REQ-032, REQ-017).

The frozen contract specifies both halves - a bearer token that says who is
posting and a signature over "<timestamp>.<raw body>" that says the body has not
been touched - and every one of the ways they can be wrong has to land in the
same place: a bare 401 for the sender, a named reason for the operator, and an
audit entry whenever the key is known.
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from signalforge.contracts.ingest import HDR_SIGNATURE, HDR_TIMESTAMP, REPLAY_WINDOW_S
from signalforge.tests.ingest.conftest import Sender, batch, change, issue_key


def refusals(api, tenant_id: str) -> list[dict]:
    return [
        row
        for row in api.audit_rows(tenant_id)
        if row["topic"] == "audit.ingest.rejected"
    ]


@pytest.mark.req("REQ-032", "REQ-017")
def test_a_correctly_signed_batch_is_accepted(changes_sender):
    response = changes_sender.post(batch(change()))
    assert response.status_code == 202, response.text
    assert response.json()["accepted"] == 1
    assert response.headers["X-Request-Id"]


@pytest.mark.req("REQ-032")
def test_no_bearer_token_is_refused(api, changes_sender):
    response = api.client.post(
        "/api/v1/ingest/changes",
        json=batch(change()),
        headers={HDR_TIMESTAMP: "1", HDR_SIGNATURE: "00"},
    )
    assert response.status_code == 401
    assert response.json() == {"detail": "authentication failed"}


@pytest.mark.req("REQ-032")
def test_an_unknown_token_is_refused_and_audits_nobody(api, owner, changes_sender):
    """An unknown token names no tenant, so there is no chain to write to - and
    letting an anonymous caller pick one would be the leak, not the fix."""
    response = changes_sender.post(batch(change()), token="sfi_not-a-real-token")
    assert response.status_code == 401
    assert refusals(api, owner["tenant_id"]) == []


@pytest.mark.req("REQ-032", "REQ-017")
def test_a_forged_signature_is_refused_with_an_audit_event(api, owner, changes_sender):
    response = changes_sender.post(batch(change()), secret="guessed-secret")
    assert response.status_code == 401
    assert response.json() == {"detail": "authentication failed"}

    entries = refusals(api, owner["tenant_id"])
    assert len(entries) == 1
    assert entries[0]["payload"]["code"] == "bad_signature"
    assert entries[0]["payload"]["stream"] == "changes"
    assert entries[0]["payload"]["phase"] == "request"


@pytest.mark.req("REQ-032", "REQ-017")
def test_a_tampered_body_is_refused(api, owner, changes_sender):
    """The signature covers the exact bytes. Signing one body and sending
    another is the attack the HMAC exists for."""
    honest = json.dumps(batch(change("deploy-a"))).encode()
    tampered = json.dumps(batch(change("deploy-b"))).encode()
    stamp = changes_sender.now()
    response = changes_sender.post(
        tampered, signature=changes_sender.sign(honest, stamp)
    )
    assert response.status_code == 401
    assert refusals(api, owner["tenant_id"])[0]["payload"]["code"] == "bad_signature"


@pytest.mark.req("REQ-032", "REQ-017")
def test_a_stale_timestamp_is_outside_the_replay_window(api, owner, changes_sender):
    response = changes_sender.post(batch(change()), skew=-(REPLAY_WINDOW_S + 1))
    assert response.status_code == 401
    assert refusals(api, owner["tenant_id"])[0]["payload"]["code"] == "replay_window"


@pytest.mark.req("REQ-032", "REQ-017")
def test_a_future_timestamp_is_outside_the_replay_window(changes_sender):
    assert changes_sender.post(
        batch(change()), skew=REPLAY_WINDOW_S + 1
    ).status_code == 401


@pytest.mark.req("REQ-032", "REQ-017")
def test_the_window_boundary_itself_is_inside(changes_sender):
    assert changes_sender.post(
        batch(change()), skew=-REPLAY_WINDOW_S
    ).status_code == 202


@pytest.mark.req("REQ-032", "REQ-017")
def test_a_replayed_request_is_authentic_but_idempotent(changes_sender):
    """Inside the window a captured request still verifies - the signature is
    not a nonce - which is exactly why `event_id`, and not the signature, is
    what stops it from being applied twice."""
    raw = json.dumps(batch(change("deploy-replay"))).encode()
    stamp = changes_sender.now()
    signature = changes_sender.sign(raw, stamp)

    first = changes_sender.post(raw, signature=signature, timestamp=str(stamp))
    replayed = changes_sender.post(raw, signature=signature, timestamp=str(stamp))
    assert (first.status_code, replayed.status_code) == (202, 202)
    assert (first.json()["accepted"], replayed.json()["duplicates"]) == (1, 1)


@pytest.mark.req("REQ-032")
def test_missing_signature_headers_are_refused(api, owner, changes_sender):
    for header in (HDR_SIGNATURE, HDR_TIMESTAMP):
        response = changes_sender.post(batch(change()), omit=(header,))
        assert response.status_code == 401, header
    codes = {entry["payload"]["code"] for entry in refusals(api, owner["tenant_id"])}
    assert codes == {"other"}


@pytest.mark.req("REQ-032")
def test_a_malformed_timestamp_is_refused(api, owner, changes_sender):
    assert changes_sender.post(batch(change()), timestamp="soon").status_code == 401
    assert (
        refusals(api, owner["tenant_id"])[0]["payload"]["code"] == "malformed_timestamp"
    )


@pytest.mark.req("REQ-032")
def test_a_key_may_not_post_to_another_stream(api, owner, changes_sender):
    """A token leaked from a deploy script cannot be used to inject alerts."""
    response = changes_sender.post(batch(change()), stream="alerts")
    assert response.status_code == 403
    assert response.json()["detail"] == "permission denied"

    entry = refusals(api, owner["tenant_id"])[0]
    assert entry["payload"]["code"] == "other"
    assert "alerts" in entry["payload"]["reason"]


@pytest.mark.req("REQ-032")
def test_the_key_id_header_must_match_the_token(api, owner, changes_sender):
    """The contract's optional key-id header names the secret the sender
    believes it signed with. During a rotation two secrets are live, so a
    mismatch is refused rather than silently verified against the other one."""
    ok = changes_sender.post(batch(change("deploy-k1")), key_id=changes_sender.key_id)
    assert ok.status_code == 202

    wrong = changes_sender.post(batch(change("deploy-k2")), key_id="some-other-key")
    assert wrong.status_code == 401
    assert (
        refusals(api, owner["tenant_id"])[0]["payload"]["code"] == "key_id_mismatch"
    )


@pytest.mark.req("REQ-032")
def test_a_revoked_key_stops_working_immediately(api, owner, changes_sender):
    """Human override mid-flow: what was already accepted stays, and the very
    next post is refused."""
    assert changes_sender.post(batch(change("deploy-before"))).status_code == 202

    revoked = api.client.delete(
        f"/ingest-keys/{changes_sender.key_id}",
        headers=api.auth_header(owner["token"]),
    )
    assert revoked.status_code == 200, revoked.text
    assert revoked.json()["active"] is False

    after = changes_sender.post(batch(change("deploy-after")))
    assert after.status_code == 401
    codes = {entry["payload"]["code"] for entry in refusals(api, owner["tenant_id"])}
    assert codes == {"revoked"}


@pytest.mark.req("REQ-032", "REQ-015")
def test_revoking_destroys_the_shared_secret(api, owner, changes_sender):
    """A revoked key that could still produce a valid signature would be
    revoked on paper only."""
    api.client.delete(
        f"/ingest-keys/{changes_sender.key_id}",
        headers=api.auth_header(owner["token"]),
    )
    conn = sqlite3.connect(api.db_path)
    try:
        names = [
            row[0]
            for row in conn.execute(
                "select name from secrets where tenant_id = ?",
                (owner["tenant_id"],),
            )
        ]
    finally:
        conn.close()
    assert f"ingest/{changes_sender.key_id}/secret" not in names


@pytest.mark.req("REQ-032")
def test_rotation_keeps_both_keys_live_until_the_old_one_is_revoked(api, owner):
    old = Sender(api=api, key=issue_key(api, owner["token"], stream="changes"))
    rotated = api.client.post(
        f"/ingest-keys/{old.key_id}/rotate", headers=api.auth_header(owner["token"])
    )
    assert rotated.status_code == 201, rotated.text
    new = Sender(api=api, key=rotated.json())

    assert new.key["rotated_from"] == old.key_id
    assert new.token != old.token and new.secret != old.secret
    assert old.post(batch(change("deploy-old"))).status_code == 202
    assert new.post(batch(change("deploy-new"))).status_code == 202

    # A sender that switched its token but not its secret is refused, which is
    # the whole reason the two keys hold two secrets.
    assert new.post(batch(change("deploy-x")), secret=old.secret).status_code == 401

    api.client.delete(
        f"/ingest-keys/{old.key_id}", headers=api.auth_header(owner["token"])
    )
    assert old.post(batch(change("deploy-old-2"))).status_code == 401
    assert new.post(batch(change("deploy-new-2"))).status_code == 202


@pytest.mark.req("REQ-032")
def test_a_flood_on_one_key_is_rate_limited(api, changes_sender):
    """Bounded before the body is hashed, and deliberately not audited: an audit
    row per refused request would let the flood write the disk it was refused
    from touching."""
    limit = api.container.settings.ingest_rate_limit
    statuses = [
        changes_sender.post(batch(change(f"deploy-{n}"))).status_code
        for n in range(limit + 2)
    ]
    assert statuses[:limit] == [202] * limit
    assert statuses[limit:] == [429, 429]

    refused = api.client.post(
        "/api/v1/ingest/changes",
        json=batch(change("deploy-z")),
        headers={"Authorization": f"Bearer {changes_sender.token}"},
    )
    assert refused.status_code == 429
    assert refused.headers["Retry-After"]


@pytest.mark.req("REQ-032")
def test_an_oversized_body_is_refused_before_it_is_hashed(api, changes_sender):
    limit = api.container.settings.ingest_max_body_bytes
    huge = b"x" * (limit + 1)
    response = changes_sender.post(huge)
    assert response.status_code == 413


@pytest.mark.req("REQ-032")
def test_the_refusal_reason_is_readable_by_the_operator_not_the_sender(
    api, owner, changes_sender
):
    changes_sender.post(batch(change()), secret="wrong")
    listed = api.client.get(
        "/ingest-keys", headers=api.auth_header(owner["token"])
    ).json()
    key = next(k for k in listed if k["id"] == changes_sender.key_id)
    assert key["last_error"] == "signature mismatch"
    assert key["rejected_count"] == 1
    assert "token" not in key and "secret" not in key
