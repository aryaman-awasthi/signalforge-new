"""The boundary around an onboarded application and its pipeline (REQ-038).

Onboarding the reference harness introduces two credentials that did not exist
before: the keys SignalForge holds *towards* an application (read-only,
remediation, drill), and the key an application's deployment pipeline holds
*towards* SignalForge. These tests pin what each one can reach.

The shape of the question is deliberately asymmetric, because the trust is:
SignalForge is given credentials that can change a production system, so they
must never come back out of it; the pipeline is given a credential that can only
add to one stream of one application of one tenant, so its blast radius must be
provably that small.
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from signalforge.tests.ingest.conftest import batch, change, events_of, issue_key

HARNESS_READ_ONLY = "ps-ro-4f1c8a2b6d9e"
HARNESS_REMEDIATION = "ps-rem-7a3d5e0c1b84"
HARNESS_DRILL = "ps-drill-2c9f6b1a7e35"
HARNESS_CREDENTIALS = (HARNESS_READ_ONLY, HARNESS_REMEDIATION, HARNESS_DRILL)

APP_ID = "payments"
APP_URL = "http://127.0.0.1:18080"

BINDINGS = [
    ("http-observe-metrics", "read_only", HARNESS_READ_ONLY),
    ("http-observe-actions", "remediation", HARNESS_REMEDIATION),
    ("http-observe-chaos", "drill", HARNESS_DRILL),
]


def _owner(api, email: str = "owner@novapay.example", org: str = "NovaPay") -> dict:
    signed = api.signup(org=org, email=email)
    return {"token": signed["tokens"]["access_token"], "tenant": signed["org"]["id"]}


def _bind_all(api, token: str) -> None:
    for adapter_id, scope, credential in BINDINGS:
        created = api.client.post(
            "/connectors",
            json={
                "app_id": APP_ID,
                "adapter_id": adapter_id,
                "name": f"payments {adapter_id}",
                "credential_scope": scope,
                "config": {"base_url": APP_URL},
                "credentials": {"token": credential},
            },
            headers=api.auth_header(token),
        )
        assert created.status_code == 201, created.text


@pytest.mark.req("REQ-038", "REQ-015")
def test_the_credentials_held_towards_an_application_never_come_back_out(api):
    """Three separate credentials, each able to do real damage to a production
    payments system, go in. Nothing that can be read afterwards contains any of
    them - not a response, not an event, not the audit chain, not the
    unauthenticated scrape, and not the stored ciphertext."""
    owner = _owner(api)
    api.client.post(
        "/apps",
        json={"app_id": APP_ID, "name": "Payments", "base_url": APP_URL},
        headers=api.auth_header(owner["token"]),
    )
    _bind_all(api, owner["token"])

    readable = [
        "/adapters",
        "/connectors",
        "/connectors/health",
        f"/apps/{APP_ID}",
        "/apps",
    ]
    for path in readable:
        response = api.client.get(path, headers=api.auth_header(owner["token"]))
        assert response.status_code == 200, response.text
        for credential in HARNESS_CREDENTIALS:
            assert credential not in response.text, f"{path} returned a credential"

    timeline = api.client.get(
        "/events?limit=500", headers=api.auth_header(owner["token"])
    )
    assert timeline.status_code == 200
    for credential in HARNESS_CREDENTIALS:
        assert credential not in timeline.text

    audit = json.dumps(api.audit_rows(owner["tenant"]))
    for credential in HARNESS_CREDENTIALS:
        assert credential not in audit

    scrape = api.client.get("/metrics")
    assert scrape.status_code == 200
    for credential in HARNESS_CREDENTIALS:
        assert credential not in scrape.text

    # What is on disk is ciphertext. This is the one place the value exists at
    # rest, and it is unreadable without the Fernet key.
    connection = sqlite3.connect(api.db_path)
    try:
        rows = connection.execute("select name, ciphertext from secrets").fetchall()
    finally:
        connection.close()
    assert rows, "no connector credential was stored at all"
    stored = " ".join(str(row[1]) for row in rows)
    for credential in HARNESS_CREDENTIALS:
        assert credential not in stored
    assert all(name.startswith("connector/") for name, _ in rows)


@pytest.mark.req("REQ-038", "REQ-032")
def test_the_pipeline_credential_reaches_one_stream_and_nothing_else(api):
    """A deployment pipeline is the least trusted thing that holds a SignalForge
    credential: it runs on build agents, its environment is readable by anyone
    who can edit a job. So its key names the tenant, the application and the
    stream, and the request body carries none of the three."""
    owner = _owner(api)
    key = issue_key(api, owner["token"], stream="changes", app_id=APP_ID, name="config pipeline")
    from signalforge.tests.ingest.conftest import Sender

    sender = Sender(api=api, key=key)

    accepted = sender.post(batch(change("v12-region-b")))
    assert accepted.status_code == 202, accepted.text

    # The same credential, correctly signed, against a different stream.
    other_stream = sender.post(batch(change("v12-region-a")), stream="alerts")
    assert other_stream.status_code in (403, 422), other_stream.text
    assert not events_of(api, owner["token"], ("alert.fired",))

    # And against the platform's own session-authenticated surface: an ingest
    # token is a machine credential and resolves to no principal at all.
    for path in ("/events", "/apps", "/connectors", "/ingest-keys"):
        response = api.client.get(path, headers={"Authorization": f"Bearer {key['token']}"})
        assert response.status_code == 401, f"{path} accepted an ingest token: {response.text}"


@pytest.mark.req("REQ-038", "REQ-013")
def test_the_pipeline_credential_cannot_reach_another_tenant(api):
    """Two organisations both running an application called `payments`. The
    pipeline of one cannot put a deployment on the other's timeline, and the two
    are not even able to observe each other's existence."""
    from signalforge.tests.ingest.conftest import Sender

    first = _owner(api)
    second = _owner(api, email="owner@other.example", org="Other Corp")

    key = issue_key(api, first["token"], stream="changes", app_id=APP_ID)
    sender = Sender(api=api, key=key)
    posted = sender.post(batch(change("v12-region-b")))
    assert posted.status_code == 202, posted.text

    assert len(events_of(api, first["token"], ("deployment.started",))) == 1
    assert events_of(api, second["token"], ("deployment.started",)) == []

    # The second tenant cannot see, rotate or revoke the first tenant's key.
    listed = api.client.get("/ingest-keys", headers=api.auth_header(second["token"]))
    assert listed.status_code == 200
    assert listed.json() == []
    for method, path in (
        ("get", f"/ingest-keys/{key['id']}"),
        ("delete", f"/ingest-keys/{key['id']}"),
    ):
        response = getattr(api.client, method)(path, headers=api.auth_header(second["token"]))
        assert response.status_code == 404, response.text


@pytest.mark.req("REQ-038", "REQ-032")
def test_the_pipeline_credential_is_shown_once_and_never_again(api):
    """The pipeline is handed a token and a secret exactly once. Everything the
    platform can show afterwards is a prefix, which is what makes "I lost it" an
    instruction to rotate rather than a reason to weaken the store."""
    owner = _owner(api)
    key = issue_key(api, owner["token"], stream="changes", app_id=APP_ID)
    token, secret = key["token"], key["secret"]

    listed = api.client.get("/ingest-keys", headers=api.auth_header(owner["token"]))
    assert listed.status_code == 200
    body = listed.text
    assert token not in body and secret not in body
    assert listed.json()[0]["token_prefix"] in token
    assert "token" not in listed.json()[0] and "secret" not in listed.json()[0]

    single = api.client.get(
        f"/ingest-keys/{key['id']}", headers=api.auth_header(owner["token"])
    )
    assert single.status_code == 200
    assert token not in single.text and secret not in single.text

    connection = sqlite3.connect(api.db_path)
    try:
        hashes = connection.execute(
            "select token_hash from ingest_keys where id = ?", (key["id"],)
        ).fetchall()
        stored_secret = connection.execute(
            "select ciphertext from secrets where name like 'ingest/%'"
        ).fetchall()
    finally:
        connection.close()
    assert hashes and token not in str(hashes[0][0])
    assert stored_secret and secret not in str(stored_secret[0][0])
