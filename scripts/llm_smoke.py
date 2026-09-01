#!/usr/bin/env python
"""Opt-in live check of the GenAI Lab gateway (S5-T02, REQ-051).

Never runs as part of `make test`. It is the one thing in the repository that
talks to a real model, so it is a script an operator runs deliberately after
setting `GENAILAB_API_KEY`:

    python scripts/llm_smoke.py            # config + /v1/models + one tiny call
    python scripts/llm_smoke.py --dry-run  # config only, no network

It answers the three questions that actually go wrong in a new environment:
is the endpoint reachable and trusted (TLS), does the key work, and are the
configured model ids ones this gateway actually serves. It prints the endpoint,
the model ids and the outcome - and never the credential.

Exit codes: 0 usable, 1 not usable, 2 usable but a configured model id is not in
the gateway's catalogue.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from signalforge.domain.llm.entities import (  # noqa: E402
    CompletionRequest,
    Message,
    ModelClass,
)
from signalforge.infrastructure.config import load_llm_settings  # noqa: E402
from signalforge.infrastructure.llm.gateway import GenAiLabGateway  # noqa: E402
from signalforge.infrastructure.llm.provider import (  # noqa: E402
    GatewayLlmProvider,
    build_llm_provider,
)

PING_SCHEMA = {
    "type": "object",
    "required": ["ok"],
    "properties": {"ok": {"type": "boolean"}},
    "additionalProperties": False,
}


def report(settings) -> None:
    print("SignalForge model path")
    for key, value in settings.redacted.items():
        print(f"  {key:18} {value}")


async def check_catalogue(provider: GatewayLlmProvider) -> int:
    """Compare the configured model ids against `GET /v1/models`.

    A mismatch is reported rather than fatal: gateways do not always list every
    deployment they route, and the authoritative answer is whether a call
    succeeds - which is what the rest of this script tries.
    """
    gateway = GenAiLabGateway(provider.settings)
    try:
        served = set(await gateway.list_models())
    except Exception as exc:
        print(f"  models          could not be listed ({type(exc).__name__})")
        return 0
    finally:
        await gateway.aclose()
    print(f"  catalogue       {len(served)} model ids served")
    missing = [
        f"{kind.value}={name}"
        for kind, name in provider.settings.models.items()
        if name not in served
    ]
    if missing:
        print(f"  MISMATCH        not in the catalogue: {', '.join(missing)}")
        return 2
    print("  catalogue check every configured model id is served")
    return 0


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run", action="store_true", help="resolve configuration, call nothing"
    )
    args = parser.parse_args()

    settings = load_llm_settings()
    report(settings)

    if not settings.endpoint_approved:
        print("FAIL: the configured endpoint is not an approved gateway.")
        return 1
    if not settings.api_key:
        print("SKIP: no GENAILAB_API_KEY; the platform runs deterministically.")
        return 1
    if args.dry_run:
        print("OK (dry run): configuration resolves to the approved gateway.")
        return 0

    provider = build_llm_provider(settings)
    status = await check_catalogue(provider)

    result = await provider.complete(
        CompletionRequest(
            purpose="smoke",
            messages=(Message(role="user", content="Reply with {\"ok\": true}."),),
            model_class=ModelClass.GENERAL,
            schema=PING_SCHEMA,
            fallback={"ok": False},
            max_tokens=32,
            cacheable=False,
        )
    )
    print(
        f"  chat            model={result.model} degraded={result.degraded} "
        f"reason={result.reason.value if result.reason else '-'} "
        f"tokens={result.usage.total}"
    )

    vectors = await provider.embed(["SignalForge gateway smoke test"], purpose="smoke")
    dimensions = len(vectors.vectors[0]) if vectors.vectors else 0
    print(
        f"  embeddings      model={vectors.model} degraded={vectors.degraded} "
        f"dimensions={dimensions}"
    )
    await provider.aclose()

    if result.degraded or vectors.degraded:
        print("FAIL: the gateway did not serve a live answer.")
        return 1
    print("OK: chat and embeddings both served by the approved gateway.")
    return status


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
