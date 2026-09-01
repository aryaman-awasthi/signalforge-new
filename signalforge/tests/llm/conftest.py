"""Fixtures for the model-path suite (S5-T02).

Nothing here touches the network. The gateway is exercised through a real
`httpx.AsyncClient` over an `httpx.MockTransport`, so request construction,
headers, URL joining, JSON bodies and status handling are all the production
code paths - only the socket is replaced. A test that "calls the model" is
therefore testing the same client a deployment uses.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Optional, Sequence

import httpx
import pytest

from signalforge.domain.llm.entities import DegradedNotice
from signalforge.domain.llm.ports import DegradationListener, ResponseCache
from signalforge.infrastructure.config import load_llm_settings

pytestmark = pytest.mark.anyio


@dataclass
class RecordedCall:
    url: str
    headers: dict[str, str]
    body: dict[str, Any]

    @property
    def host(self) -> str:
        return httpx.URL(self.url).host


@dataclass
class FakeGatewayTransport:
    """Records every request and answers with whatever the test queued.

    `responses` may hold `httpx.Response` objects or exceptions; an exception is
    raised instead of answered, which is how the transient-failure and timeout
    paths are driven without waiting for a real timeout.
    """

    responses: list = field(default_factory=list)
    calls: list[RecordedCall] = field(default_factory=list)

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode("utf-8")) if request.content else {}
        self.calls.append(
            RecordedCall(str(request.url), dict(request.headers), body)
        )
        if not self.responses:
            raise AssertionError(f"unexpected gateway call to {request.url}")
        nxt = self.responses.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return nxt

    @property
    def hosts(self) -> set[str]:
        return {call.host for call in self.calls}


def chat_response(
    content: str,
    *,
    prompt_tokens: int = 11,
    completion_tokens: int = 7,
    model: str = "genailab-maas-gpt-5.4-mini",
) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "id": "chatcmpl-test",
            "model": model,
            "choices": [
                {"index": 0, "message": {"role": "assistant", "content": content},
                 "finish_reason": "stop"}
            ],
            "usage": {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": prompt_tokens + completion_tokens,
            },
        },
    )


def embedding_response(
    vectors: Sequence[Sequence[float]],
    *,
    model: str = "azure/genailab-maas-text-embedding-3-large",
    prompt_tokens: int = 5,
) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "object": "list",
            "model": model,
            "data": [
                {"object": "embedding", "index": i, "embedding": list(v)}
                for i, v in enumerate(vectors)
            ],
            "usage": {"prompt_tokens": prompt_tokens, "total_tokens": prompt_tokens},
        },
    )


class MemoryCache(ResponseCache):
    """The cache port without a database, so cache-behaviour tests stay about
    the key rather than about SQLAlchemy."""

    def __init__(self) -> None:
        self.rows: dict[tuple[str, str], dict] = {}
        self.writes = 0

    async def get(self, tenant_id: str, key: str) -> Optional[dict]:
        return self.rows.get((tenant_id, key))

    async def put(self, tenant_id, key, *, model, purpose, payload) -> None:
        self.writes += 1
        self.rows[(tenant_id, key)] = payload


class RecordingListener(DegradationListener):
    def __init__(self) -> None:
        self.notices: list[DegradedNotice] = []

    async def on_degraded(self, notice: DegradedNotice) -> None:
        self.notices.append(notice)

    @property
    def reasons(self) -> list[str]:
        return [n.reason.value for n in self.notices]


def settings(**env: str):
    """LLM settings from an explicit environment, never the machine's.

    Every test states the whole environment it means, so a developer with
    GENAILAB_API_KEY exported cannot change what the suite proves.
    """
    return load_llm_settings(env)


def provider(
    transport: Optional[FakeGatewayTransport] = None,
    *,
    cache: Optional[ResponseCache] = None,
    listener: Optional[DegradationListener] = None,
    ledger=None,
    metrics=None,
    env: Optional[dict[str, str]] = None,
):
    from signalforge.infrastructure.llm.provider import build_llm_provider

    base = {"GENAILAB_API_KEY": "test-key-not-a-real-secret"}
    base.update(env or {})
    return build_llm_provider(
        load_llm_settings(base),
        cache=cache,
        ledger=ledger,
        listener=listener,
        metrics=metrics,
        transport=transport.transport() if transport else None,
        # Retry *counts* are what these tests assert; the sleep between them is
        # the production default and would only make the suite slower.
        backoff_s=0.0,
    )


@pytest.fixture
def gateway() -> FakeGatewayTransport:
    return FakeGatewayTransport()


@pytest.fixture
def listener() -> RecordingListener:
    return RecordingListener()


@pytest.fixture
def cache() -> MemoryCache:
    return MemoryCache()


def user(text: str) -> tuple:
    from signalforge.domain.llm.entities import Message

    return (Message(role="user", content=text),)


__all__ = [
    "FakeGatewayTransport",
    "MemoryCache",
    "RecordingListener",
    "chat_response",
    "embedding_response",
    "provider",
    "settings",
    "user",
]
