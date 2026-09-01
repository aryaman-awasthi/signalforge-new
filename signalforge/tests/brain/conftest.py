"""Fixtures for the incident-brain suite (S5).

Every test here drives the real API and the real container. An incident is
declared by a real session holding `declare_incident`; a topology is imported
through the onboarding wizard; a deployment reaches the change window through
the frozen ingest path with a real HMAC and the real bus.

**No test in this suite touches the network.** Where a model is involved, the
gateway is exercised through a real `httpx.AsyncClient` over an
`httpx.MockTransport` (`api_with_model`), so request construction, headers, JSON
bodies and status handling are production code paths and only the socket is
replaced. The default `api` fixture resolves its model settings from an *empty*
environment, so a developer with `GENAILAB_API_KEY` exported cannot change what
the suite proves - and the degraded path is what most of these tests run under,
which is exactly right for an AI feature whose floor is "works without a model".
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Optional

import pytest

from signalforge.tests.incidents.conftest import (  # noqa: F401  (re-exported)
    APP_ID,
    Sender,
    announce,
    batch,
    change,
    close_incident,
    commander,
    declare,
    issue_key,
    onboard,
    owner,
    read_incident,
    relogin,
    topics,
)
from signalforge.tests.onboarding.conftest import (  # noqa: F401  (re-exported)
    register_app,
    upload_topology,
)
from signalforge.tests.warroom.conftest import (  # noqa: F401  (re-exported)
    responder,
    say,
    war_room,
    wire_bridge,
    wire_paging,
)

# A three-tier estate: the application at the front, a pool it depends on, and a
# database behind that. Enough that a blast-radius walk has somewhere to go and
# small enough to read in a test failure.
BRAIN_TOPOLOGY = {
    "version": "v1",
    "captured_at": "2026-09-01T11:00:00+00:00",
    "nodes": [
        {
            "node_id": "payments",
            "name": "payments",
            "node_type": "service",
            "region": "eu-west-1",
            "labels": {"app": APP_ID},
        },
        {"node_id": "connection-pool", "name": "connection-pool", "node_type": "service"},
        {"node_id": "ledger-db", "name": "ledger-db", "node_type": "database"},
        {"node_id": "mobile-bff", "name": "mobile-bff", "node_type": "service"},
    ],
    "edges": [
        {"source": "payments", "target": "connection-pool", "edge_type": "depends_on"},
        {"source": "connection-pool", "target": "ledger-db", "edge_type": "depends_on"},
        {"source": "mobile-bff", "target": "payments", "edge_type": "depends_on"},
    ],
}

BRAIN_TOPOLOGY_V2 = {
    **BRAIN_TOPOLOGY,
    "version": "v2",
    "captured_at": "2026-09-01T11:30:00+00:00",
    "nodes": BRAIN_TOPOLOGY["nodes"]
    + [{"node_id": "fraud-check", "name": "fraud-check", "node_type": "service"}],
    "edges": BRAIN_TOPOLOGY["edges"]
    + [{"source": "fraud-check", "target": "payments", "edge_type": "depends_on"}],
}


# --- a scripted gateway --------------------------------------------------------


@dataclass
class ScriptedGateway:
    """Answers queued per prompt-purpose, so a test can make one agent's model
    call succeed and another's fail without caring about call order.

    Order-independence matters: the governor's agent roster is a list, and a test
    that depended on its order would break the first time somebody inserted an
    agent, for no reason connected to what it was testing.
    """

    answers: dict[str, list] = field(default_factory=dict)
    calls: list[dict] = field(default_factory=list)
    default: Optional[Any] = None

    def queue(self, purpose: str, payload) -> "ScriptedGateway":
        self.answers.setdefault(purpose, []).append(payload)
        return self

    def transport(self):
        import httpx

        def handle(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content.decode("utf-8")) if request.content else {}
            self.calls.append({"url": str(request.url), "body": body})
            if str(request.url).endswith("/embeddings"):
                count = len(body.get("input") or [])
                return _embeddings(count)
            purpose = _purpose_of(body)
            queued = self.answers.get(purpose)
            if queued:
                payload = queued.pop(0)
            elif self.default is not None:
                payload = self.default
            else:
                # Nothing queued for this purpose: answer 503 so the provider
                # degrades, which is the honest default for a test that did not
                # arrange a model answer.
                return httpx.Response(503, json={"error": "no answer queued"})
            if isinstance(payload, Exception):
                raise payload
            if isinstance(payload, httpx.Response):
                return payload
            return _chat(payload)

        return httpx.MockTransport(handle)

    def purposes(self) -> list[str]:
        return [_purpose_of(call["body"]) for call in self.calls if call["body"].get("messages")]


PURPOSE_MARKERS = {
    "statement-classification": "were not classified by the deterministic pass",
    "hypothesis-narrative": "has already computed each hypothesis",
    "join-brief": "Rewrite an incident join brief",
    "copilot-answer": "Answer a question about one incident",
}


def _purpose_of(body: dict) -> str:
    """Which call site this request came from.

    Read from the prompt text rather than from a header, deliberately: the
    provider does not put the purpose on the wire (it is provenance, not
    something a gateway needs), and a test that required it to would be pinning
    a detail the product has no reason to carry.
    """
    text = " ".join(
        str(message.get("content") or "") for message in body.get("messages", [])
    )
    for purpose, marker in PURPOSE_MARKERS.items():
        if marker in text:
            return purpose
    return "unknown"


def _chat(payload) -> Any:
    import httpx

    content = payload if isinstance(payload, str) else json.dumps(payload)
    return httpx.Response(
        200,
        json={
            "id": "chatcmpl-test",
            "model": "genailab-maas-gpt-5.4-mini",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": content},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 40, "completion_tokens": 25, "total_tokens": 65},
        },
    )


def _embeddings(count: int):
    import httpx

    # Deterministic unit-ish vectors: enough for a cosine to be computable and
    # stable, without pretending to be a real embedding.
    return httpx.Response(
        200,
        json={
            "object": "list",
            "model": "azure/genailab-maas-text-embedding-3-large",
            "data": [
                {
                    "object": "embedding",
                    "index": i,
                    "embedding": [1.0, 0.5 + i * 0.01, 0.25],
                }
                for i in range(count)
            ],
            "usage": {"prompt_tokens": 5, "total_tokens": 5},
        },
    )


@pytest.fixture
def gateway() -> ScriptedGateway:
    return ScriptedGateway()


@pytest.fixture
def api_with_model(tmp_path, gateway):
    """The API with a credentialled provider whose socket is a MockTransport.

    A separate fixture rather than a flag on `api`, so that the default for this
    whole suite stays "no model" - which is the state the platform has to work
    in, and the state a reviewer should have to opt *out* of rather than into.
    """
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import NullPool

    from signalforge.api import deps
    from signalforge.api.main import app
    from signalforge.infrastructure.config import load_llm_settings
    from signalforge.infrastructure.persistence.models import Base
    from signalforge.tests.helpers import ApiCtx, FakeClock, make_test_settings

    db_path = tmp_path / "brain.db"
    sync_engine = create_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(sync_engine)
    sync_engine.dispose()

    settings = make_test_settings(f"sqlite+aiosqlite:///{db_path}")
    clock = FakeClock()
    engine = create_async_engine(settings.db_url, poolclass=NullPool)
    container = deps.Container(
        settings=settings,
        engine=engine,
        clock=clock,
        # An explicit environment, never the machine's: a key on a developer's
        # laptop must not be able to change what this proves. The cache is off
        # so that a test which queues two different answers for one purpose gets
        # both of them.
        llm_settings=load_llm_settings(
            {"GENAILAB_API_KEY": "test-key-not-a-real-secret", "LLM_CACHE": "false"}
        ),
        llm_transport=gateway.transport(),
    )
    app.dependency_overrides[deps.get_container] = lambda: container
    try:
        with TestClient(app) as client:
            yield ApiCtx(client=client, container=container, clock=clock, db_path=db_path)
    finally:
        app.dependency_overrides.clear()


# --- brain helpers -------------------------------------------------------------


def brain(api, token: str, incident_id: str, *, expect: int = 200) -> dict:
    response = api.client.get(
        f"/incidents/{incident_id}/brain", headers=api.auth_header(token)
    )
    assert response.status_code == expect, response.text
    return response.json()


def refresh(api, token: str, incident_id: str, *, expect: int = 200) -> dict:
    response = api.client.post(
        f"/incidents/{incident_id}/brain/refresh",
        json={},
        headers=api.auth_header(token),
    )
    assert response.status_code == expect, response.text
    return response.json()


def ask(api, token: str, incident_id: str, question: str, *, expect: int = 200) -> dict:
    response = api.client.post(
        f"/incidents/{incident_id}/brain/ask",
        json={"question": question},
        headers=api.auth_header(token),
    )
    assert response.status_code == expect, response.text
    return response.json()


def guidance(api, token: str, incident_id: str, *, expect: int = 200) -> dict:
    response = api.client.get(
        f"/incidents/{incident_id}/brain/guidance", headers=api.auth_header(token)
    )
    assert response.status_code == expect, response.text
    return response.json()


def topology(
    api, token: str, incident_id: str, *, version: Optional[str] = None, expect: int = 200
) -> dict:
    query = f"?version={version}" if version else ""
    response = api.client.get(
        f"/incidents/{incident_id}/brain/topology{query}",
        headers=api.auth_header(token),
    )
    assert response.status_code == expect, response.text
    return response.json()


def node_history(api, token: str, incident_id: str, key: str, *, expect: int = 200) -> dict:
    response = api.client.get(
        f"/incidents/{incident_id}/brain/nodes/{key}", headers=api.auth_header(token)
    )
    assert response.status_code == expect, response.text
    return response.json()


def post_bridge(api, token: str, incident_id: str, text: str, *, expect: int = 201) -> dict:
    response = api.client.post(
        f"/incidents/{incident_id}/bridge",
        json={"text": text},
        headers=api.auth_header(token),
    )
    assert response.status_code == expect, response.text
    return response.json()


def read_brief(api, token: str, incident_id: str, *, expect: int = 200) -> dict:
    response = api.client.get(
        f"/incidents/{incident_id}/brief", headers=api.auth_header(token)
    )
    assert response.status_code == expect, response.text
    return response.json()
