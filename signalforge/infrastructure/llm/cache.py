"""Answers already paid for, keyed by everything that could change one (REQ-051).

The cache is what makes a rehearsed drill cheap and a repeated question free,
but it is only safe if the key is honest. `cache_key` therefore hashes the whole
of the input surface:

- the **provider and model id**, so swapping `LLM_MODEL_GENERAL` cannot serve an
  answer produced by the previous model;
- the **prompt/config version**, so changing how prompts are built or parsed
  invalidates warm entries rather than serving answers from rules that are gone;
- the **classification, purpose and sampling parameters**, because the same text
  asked of the reasoning model, or at a different temperature, is a different
  question;
- the **normalised messages**, so cosmetic whitespace differences hit the same
  entry while a single changed word does not;
- the **response schema**, because an answer shaped for one schema is not an
  answer for another.

Tenant isolation is a property of the *store*, not of the key: rows are written
and read under `(tenant_id, key)`, so one tenant can never be served a cache
entry produced by another's call even though identical input hashes identically.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Optional, Sequence

from signalforge.domain.llm.entities import CompletionRequest, ModelClass

_WHITESPACE = re.compile(r"\s+")

CACHE_KEY_VERSION = 1


def _normalize(text: str) -> str:
    return _WHITESPACE.sub(" ", (text or "").strip())


def _digest(material: dict[str, Any]) -> str:
    canonical = json.dumps(
        material, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def cache_key(
    request: CompletionRequest,
    *,
    provider: str,
    model: str,
    config_version: str,
) -> str:
    return _digest(
        {
            "v": CACHE_KEY_VERSION,
            "kind": "chat",
            "provider": provider,
            "model": model,
            "model_class": request.model_class.value,
            "purpose": request.purpose,
            "prompt_version": request.prompt_version,
            "config_version": config_version,
            "max_tokens": request.max_tokens,
            "temperature": request.temperature,
            "messages": [
                {"role": m.role, "content": _normalize(m.content)} for m in request.messages
            ],
            "schema": request.schema,
        }
    )


def embedding_cache_key(
    texts: Sequence[str],
    *,
    provider: str,
    model: str,
    config_version: str,
) -> str:
    return _digest(
        {
            "v": CACHE_KEY_VERSION,
            "kind": "embeddings",
            "provider": provider,
            "model": model,
            "model_class": ModelClass.EMBEDDINGS.value,
            "config_version": config_version,
            "inputs": [_normalize(text) for text in texts],
        }
    )


def encode_completion(*, text: str, data: Any, model: str, usage) -> dict:
    """What a cache row holds for a completion: the answer and the provenance
    needed to replay it honestly, never the prompt."""
    return {
        "kind": "chat",
        "text": text,
        "data": data,
        "model": model,
        "usage": {"prompt": usage.prompt, "completion": usage.completion},
    }


def encode_embeddings(*, vectors: Sequence[Sequence[float]], model: str, usage) -> dict:
    return {
        "kind": "embeddings",
        "vectors": [list(vector) for vector in vectors],
        "model": model,
        "usage": {"prompt": usage.prompt, "completion": usage.completion},
    }


def decode_usage(payload: Optional[dict]) -> tuple[int, int]:
    usage = (payload or {}).get("usage") or {}
    return int(usage.get("prompt", 0) or 0), int(usage.get("completion", 0) or 0)
