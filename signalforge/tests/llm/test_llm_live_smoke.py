"""The one test that may touch the network - and only when asked (REQ-051).

The normal suite must never require gateway connectivity: it runs on a build
agent, on a laptop on a train and in the middle of the outage this platform
exists to manage. So everything else in `tests/llm` drives a mock transport, and
the live path is gated behind `GENAILAB_API_KEY` being present *and*
`SIGNALFORGE_LIVE_LLM=1` being set deliberately - a key exported for another
purpose should not silently start spending tokens during `make test`.

What it proves is the thing a mock cannot: that the endpoint is reachable, the
TLS chain is trusted, the credential is accepted and the configured model ids
are ones the gateway actually serves.
"""

from __future__ import annotations

import os

import pytest

from signalforge.domain.llm.entities import (
    CompletionRequest,
    Message,
    ModelClass,
)
from signalforge.infrastructure.config import load_llm_settings
from signalforge.infrastructure.llm.provider import build_llm_provider

pytestmark = [
    pytest.mark.anyio,
    pytest.mark.skipif(
        not (os.environ.get("GENAILAB_API_KEY") and os.environ.get("SIGNALFORGE_LIVE_LLM")),
        reason="live gateway check: set GENAILAB_API_KEY and SIGNALFORGE_LIVE_LLM=1",
    ),
]

PING_SCHEMA = {
    "type": "object",
    "required": ["ok"],
    "properties": {"ok": {"type": "boolean"}},
    "additionalProperties": False,
}


@pytest.mark.req("REQ-051")
async def test_the_live_gateway_serves_a_structured_answer():
    settings = load_llm_settings()
    assert settings.endpoint_approved and settings.configured
    provider = build_llm_provider(settings)
    try:
        result = await provider.complete(
            CompletionRequest(
                purpose="smoke",
                messages=(Message(role="user", content='Reply with {"ok": true}.'),),
                schema=PING_SCHEMA,
                fallback={"ok": False},
                max_tokens=32,
                cacheable=False,
            )
        )
        assert not result.degraded, result.reason
        assert result.data == {"ok": True}
        assert result.usage.total > 0
    finally:
        await provider.aclose()


@pytest.mark.req("REQ-051", "REQ-035")
async def test_the_live_gateway_serves_embeddings():
    provider = build_llm_provider(load_llm_settings())
    try:
        result = await provider.embed(["SignalForge gateway smoke test"], purpose="smoke")
        assert not result.degraded, result.reason
        assert len(result.vectors) == 1 and len(result.vectors[0]) > 100
    finally:
        await provider.aclose()


@pytest.mark.req("REQ-051")
async def test_every_configured_model_id_is_one_the_gateway_serves():
    from signalforge.infrastructure.llm.gateway import GenAiLabGateway

    settings = load_llm_settings()
    gateway = GenAiLabGateway(settings)
    try:
        served = set(await gateway.list_models())
    finally:
        await gateway.aclose()
    missing = [
        name for kind, name in settings.models.items() if name not in served
    ]
    assert not missing, f"configured model ids not served by the gateway: {missing}"
    assert settings.model_for(ModelClass.GENERAL) in served
