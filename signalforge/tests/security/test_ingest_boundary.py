"""S3-T03: the push-ingest security boundary (REQ-032, REQ-017, REQ-013, REQ-015).

The ingest API is the one product surface an unauthenticated caller can reach
and get further than a 401 - so everything that stands between the open internet
and a tenant's timeline is asserted here rather than left to the feature tests.

Five properties:

1. The credential is two independent halves, and neither alone is enough.
2. What is stored is not usable: the token as a sha256, the secret as Fernet
   ciphertext.
3. A verified sender is still bounded - to its tenant, its app and its stream.
4. Every refusal against a known key is evidence in that tenant's audit chain.
5. The unauthenticated scrape learns nothing about who is posting what.
"""

from __future__ import annotations

import hashlib
import hmac as hmac_mod
import json
import sqlite3
import uuid

import pytest

from signalforge.contracts.envelope import utc_now
from signalforge.contracts.ingest import (
    HDR_KEY_ID,
    HDR_SIGNATURE,
    HDR_TIMESTAMP,
    INGEST_PATHS,
    REPLAY_WINDOW_S,
)
from signalforge.domain.ingest.keys import TOKEN_PREFIX

CHANGE = {
    "change_id": "deploy-sec-1",
    "change_type": "deployment",
    "service": "checkout",
    "status": "started",
    "started_at": "2026-09-01T12:00:00+00:00",
}


def issue(api, token: str, *, stream: str = "changes", app_id: str = "checkout") -> dict:
    response = api.client.post(
        "/ingest-keys",
        json={"app_id": app_id, "stream": stream, "name": f"{app_id} {stream}"},
        headers=api.auth_header(token),
    )
    assert response.status_code == 201, response.text
    return response.json()


def body(event_id: str | None = None) -> bytes:
    return json.dumps(
        {
            "source_system": "ci",
            "items": [
                {
                    "event_id": event_id or str(uuid.uuid4()),
                    "occurred_at": utc_now().isoformat(),
                    "payload": CHANGE,
                }
            ],
        }
    ).encode()


def sign(secret: str, stamp: int, raw: bytes) -> str:
    return hmac_mod.new(
        secret.encode(), f"{stamp}.".encode() + raw, hashlib.sha256
    ).hexdigest()


def post(api, key: dict, raw: bytes, *, stream: str = "changes", **headers):
    stamp = int(api.clock.now().timestamp())
    request_headers = {
        "Authorization": f"Bearer {key['token']}",
        HDR_TIMESTAMP: str(stamp),
        HDR_SIGNATURE: sign(key["secret"], stamp, raw),
        "Content-Type": "application/json",
    }
    request_headers.update(headers)
    return api.client.post(INGEST_PATHS[stream], content=raw, headers=request_headers)


@pytest.mark.req("REQ-032", "REQ-017")
def test_neither_half_of_the_credential_is_sufficient_alone(api):
    """A valid token with no signature, and a valid signature with no token,
    are both refused. That is the point of asking for two."""
    owner = api.signup()
    key = issue(api, owner["tokens"]["access_token"])
    raw = body()
    stamp = int(api.clock.now().timestamp())

    token_only = api.client.post(
        INGEST_PATHS["changes"],
        content=raw,
        headers={"Authorization": f"Bearer {key['token']}"},
    )
    assert token_only.status_code == 401

    signature_only = api.client.post(
        INGEST_PATHS["changes"],
        content=raw,
        headers={
            HDR_TIMESTAMP: str(stamp),
            HDR_SIGNATURE: sign(key["secret"], stamp, raw),
        },
    )
    assert signature_only.status_code == 401

    assert post(api, key, raw).status_code == 202


@pytest.mark.req("REQ-032", "REQ-015")
def test_what_is_stored_cannot_be_used_to_post(api):
    """A read of the database yields a hash and a ciphertext, and neither
    authenticates: the exfiltrated material is replayed here and refused."""
    owner = api.signup()
    key = issue(api, owner["tokens"]["access_token"])

    conn = sqlite3.connect(api.db_path)
    try:
        token_hash = conn.execute(
            "select token_hash from ingest_keys where id = ?", (key["id"],)
        ).fetchone()[0]
        ciphertext = conn.execute(
            "select ciphertext from secrets where name = ?",
            (f"ingest/{key['id']}/secret",),
        ).fetchone()[0]
    finally:
        conn.close()

    assert token_hash == hashlib.sha256(key["token"].encode()).hexdigest()
    raw = body()
    stamp = int(api.clock.now().timestamp())
    stolen = api.client.post(
        INGEST_PATHS["changes"],
        content=raw,
        headers={
            "Authorization": f"Bearer {token_hash}",
            HDR_TIMESTAMP: str(stamp),
            HDR_SIGNATURE: sign(ciphertext.decode("latin-1"), stamp, raw),
        },
    )
    assert stolen.status_code == 401


@pytest.mark.req("REQ-032", "REQ-013")
def test_a_verified_sender_cannot_reach_past_its_own_key(api):
    """The key names the tenant, the app and the stream. There is no header,
    body field or query parameter through which a sender could widen any of the
    three - the request body carries no tenant, app or incident at all."""
    first = api.signup(org="One", email="one@acme.io")
    second = api.signup(org="Two", email="two@beta.io")
    key = issue(api, first["tokens"]["access_token"], app_id="checkout")

    accepted = post(api, key, body())
    assert accepted.status_code == 202

    # Its own stream only.
    assert post(api, key, body(), stream="alerts").status_code == 403

    events = api.client.get(
        "/events",
        params=[("topics", "deployment.started"), ("limit", "50")],
        headers=api.auth_header(first["tokens"]["access_token"]),
    ).json()["events"]
    assert len(events) == 1
    assert events[0]["tenant_id"] == first["org"]["id"]
    assert events[0]["app_id"] == "checkout"

    other = api.client.get(
        "/events",
        params=[("topics", "deployment.started"), ("limit", "50")],
        headers=api.auth_header(second["tokens"]["access_token"]),
    ).json()["events"]
    assert other == []


@pytest.mark.req("REQ-032", "REQ-016")
def test_every_refusal_against_a_known_key_is_in_the_audit_chain(api):
    """Forged signature, stale timestamp, wrong key id, wrong stream and a
    revoked key: five different ways in, one place they all land."""
    owner = api.signup()
    tenant_id = owner["org"]["id"]
    key = issue(api, owner["tokens"]["access_token"])
    raw = body()
    stamp = int(api.clock.now().timestamp())

    api.client.post(
        INGEST_PATHS["changes"],
        content=raw,
        headers={
            "Authorization": f"Bearer {key['token']}",
            HDR_TIMESTAMP: str(stamp),
            HDR_SIGNATURE: sign("guessed", stamp, raw),
        },
    )
    stale = stamp - (REPLAY_WINDOW_S + 1)
    api.client.post(
        INGEST_PATHS["changes"],
        content=raw,
        headers={
            "Authorization": f"Bearer {key['token']}",
            HDR_TIMESTAMP: str(stale),
            HDR_SIGNATURE: sign(key["secret"], stale, raw),
        },
    )
    post(api, key, raw, **{HDR_KEY_ID: "not-this-key"})
    post(api, key, raw, stream="alerts")
    api.client.delete(
        f"/ingest-keys/{key['id']}",
        headers=api.auth_header(owner["tokens"]["access_token"]),
    )
    post(api, key, raw)

    codes = [
        row["payload"]["code"]
        for row in api.audit_rows(tenant_id)
        if row["topic"] == "audit.ingest.rejected"
    ]
    assert codes == [
        "bad_signature",
        "replay_window",
        "key_id_mismatch",
        "other",  # stream mismatch: named in `reason`, not a metric label
        "revoked",
    ]
    for row in api.audit_rows(tenant_id):
        if row["topic"] == "audit.ingest.rejected":
            # The prefix identifies which key without being usable as one.
            assert row["actor_id"].startswith(TOKEN_PREFIX)
            assert key["token"] not in json.dumps(row["payload"])
            assert key["secret"] not in json.dumps(row["payload"])


@pytest.mark.req("REQ-032", "REQ-025")
def test_the_unauthenticated_scrape_names_nobody(api):
    owner = api.signup()
    key = issue(api, owner["tokens"]["access_token"])
    post(api, key, body())
    api.client.post(
        INGEST_PATHS["changes"],
        content=body(),
        headers={
            "Authorization": f"Bearer {key['token']}",
            HDR_TIMESTAMP: str(int(api.clock.now().timestamp())),
            HDR_SIGNATURE: "00" * 32,
        },
    )

    scrape = api.client.get("/metrics")
    assert scrape.status_code == 200
    text = scrape.text
    assert 'signalforge_ingest_items_total{stream="changes",outcome="accepted"} 1' in text
    assert 'signalforge_ingest_refused_total{stream="changes",reason="bad_signature"} 1' in text
    for forbidden in (
        owner["org"]["id"],
        owner["user"]["id"],
        key["id"],
        key["token"],
        key["secret"],
        key["token_prefix"],
        "checkout",
    ):
        assert forbidden not in text, forbidden


@pytest.mark.req("REQ-032", "REQ-014")
def test_free_text_is_masked_before_it_reaches_the_stored_event(api):
    """Asserted against the stored row, not the API response: a policy applied
    on the way out could always be bypassed by a later reader of the same row."""
    owner = api.signup()
    key = issue(api, owner["tokens"]["access_token"], stream="messages")
    raw = json.dumps(
        {
            "source_system": "bridge",
            "items": [
                {
                    "event_id": str(uuid.uuid4()),
                    "occurred_at": utc_now().isoformat(),
                    "payload": {
                        "message_id": "m-1",
                        "channel": "bridge",
                        "author": {"type": "human", "id": "u-1"},
                        "text": "call ada@example.com about 4111 1111 1111 1111",
                        "sent_at": utc_now().isoformat(),
                    },
                }
            ],
        }
    ).encode()
    assert post(api, key, raw, stream="messages").status_code == 202

    conn = sqlite3.connect(api.db_path)
    try:
        envelopes = [
            row[0]
            for row in conn.execute(
                "select envelope from events where topic = ?",
                ("collaboration.message.received",),
            )
        ]
    finally:
        conn.close()
    assert len(envelopes) == 1
    assert "ada@example.com" not in envelopes[0]
    assert "4111" not in envelopes[0]
    assert '"policy_id":"ingest-default"' in envelopes[0].replace(", ", ",")
