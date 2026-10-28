"""Fixtures for the push-ingest suite (S3-T03).

Nothing here fakes the security path. A test that wants to post telemetry
issues a real ingest key through the real API, gets the token and the shared
secret back exactly once, and then signs its own body the way a sending
application has to: HMAC-SHA256 over "<unix seconds>.<raw bytes>" with the
timestamp inside the replay window. The signature is computed over the *exact*
bytes that go on the wire, so a test cannot accidentally prove the platform
right by re-serialising the body on both sides.
"""

from __future__ import annotations

import hashlib
import hmac as hmac_mod
import json
import uuid
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Optional

import pytest

from signalforge.contracts.envelope import utc_now
from signalforge.contracts.ingest import (
    HDR_KEY_ID,
    HDR_SIGNATURE,
    HDR_TIMESTAMP,
    INGEST_PATHS,
)


def issue_key(
    api,
    token: str,
    *,
    stream: str,
    app_id: str = "checkout",
    name: Optional[str] = None,
    binding_id: Optional[str] = None,
    expect: int = 201,
) -> dict:
    body: dict[str, Any] = {
        "app_id": app_id,
        "stream": stream,
        "name": name or f"{app_id} {stream}",
    }
    if binding_id is not None:
        body["binding_id"] = binding_id
    response = api.client.post(
        "/ingest-keys", json=body, headers=api.auth_header(token)
    )
    assert response.status_code == expect, response.text
    return response.json()


@dataclass
class Sender:
    """One sending application, holding the credentials it was issued."""

    api: Any
    key: dict

    @property
    def key_id(self) -> str:
        return self.key["id"]

    @property
    def token(self) -> str:
        return self.key["token"]

    @property
    def secret(self) -> str:
        return self.key["secret"]

    @property
    def stream(self) -> str:
        return self.key["stream"]

    def now(self) -> int:
        return int(self.api.clock.now().timestamp())

    def sign(self, raw: bytes, timestamp: int, secret: Optional[str] = None) -> str:
        material = f"{timestamp}.".encode("utf-8") + raw
        return hmac_mod.new(
            (secret or self.secret).encode("utf-8"), material, hashlib.sha256
        ).hexdigest()

    def post(
        self,
        body,
        *,
        stream: Optional[str] = None,
        skew: int = 0,
        secret: Optional[str] = None,
        signature: Optional[str] = None,
        token: Optional[str] = None,
        key_id: Optional[str] = None,
        timestamp: Optional[str] = None,
        omit: tuple[str, ...] = (),
    ):
        raw = body if isinstance(body, (bytes, bytearray)) else json.dumps(body).encode()
        raw = bytes(raw)
        stamp = self.now() + skew
        headers = {
            "Authorization": f"Bearer {token or self.token}",
            HDR_TIMESTAMP: timestamp if timestamp is not None else str(stamp),
            HDR_SIGNATURE: signature or self.sign(raw, stamp, secret),
            "Content-Type": "application/json",
        }
        if key_id is not None:
            headers[HDR_KEY_ID] = key_id
        for name in omit:
            headers.pop(name, None)
        return self.api.client.post(
            INGEST_PATHS[stream or self.stream], content=raw, headers=headers
        )


def batch(*payloads, source_system: str = "ref-harness", simulated: bool = False) -> dict:
    """A well-formed batch envelope around already-canonical payloads."""
    return {
        "source_system": source_system,
        "simulated": simulated,
        "items": [
            {
                "event_id": str(payload.pop("_event_id", uuid.uuid4())),
                "occurred_at": payload.pop("_occurred_at", utc_now().isoformat()),
                "payload": payload,
            }
            for payload in [dict(p) for p in payloads]
        ],
    }


def change(change_id: str = "deploy-1", **overrides) -> dict:
    document = {
        "change_id": change_id,
        "change_type": "deployment",
        "service": "checkout",
        "status": "started",
        "started_at": utc_now().isoformat(),
        "version": "1.4.2",
    }
    document.update(overrides)
    return document


def alert(alert_id: str = "a-1", **overrides) -> dict:
    document = {
        "alert_id": alert_id,
        "name": "CheckoutErrorRate",
        "severity": "critical",
        "status": "firing",
        "starts_at": utc_now().isoformat(),
        "labels": {"service": "checkout"},
        "annotations": {},
    }
    document.update(overrides)
    return document


def message(message_id: str = "m-1", **overrides) -> dict:
    document = {
        "message_id": message_id,
        "channel": "bridge",
        "author": {"type": "human", "id": "u-7", "display": "Ada"},
        "text": "checkout is throwing 500s",
        "sent_at": utc_now().isoformat(),
    }
    document.update(overrides)
    return document


def ticket(ticket_id: str = "INC-1", **overrides) -> dict:
    document = {
        "ticket_id": ticket_id,
        "title": "Checkout errors",
        "status": "open",
        "created_at": utc_now().isoformat(),
        "description": "raised by the on-call",
    }
    document.update(overrides)
    return document


def topology(**overrides) -> dict:
    document = {
        "version": "2026-09-01",
        "captured_at": utc_now().isoformat(),
        "nodes": [
            {"node_id": "checkout", "name": "checkout", "node_type": "service"},
            {"node_id": "ledger", "name": "ledger", "node_type": "database"},
        ],
        "edges": [{"source": "checkout", "target": "ledger", "edge_type": "calls"}],
    }
    document.update(overrides)
    return document


@pytest.fixture
def owner(api):
    """A signed-up org owner: the only role holding manage_apps."""
    signup = api.signup()
    return {
        "token": signup["tokens"]["access_token"],
        "tenant_id": signup["org"]["id"],
        "user_id": signup["user"]["id"],
    }


@pytest.fixture
def changes_sender(api, owner) -> Sender:
    return Sender(api=api, key=issue_key(api, owner["token"], stream="changes"))


@pytest.fixture
def alerts_sender(api, owner) -> Sender:
    return Sender(api=api, key=issue_key(api, owner["token"], stream="alerts"))


def events_of(api, token: str, topics: tuple[str, ...] = ()) -> list[dict]:
    """Replay the tenant's own timeline through the API, as an auditor would."""
    params = [("topics", topic) for topic in topics] + [("limit", "200")]
    response = api.client.get(
        "/events", params=params, headers=api.auth_header(token)
    )
    assert response.status_code == 200, response.text
    return response.json()["events"]


def advance(api, **kwargs) -> None:
    api.clock.current += timedelta(**kwargs)
