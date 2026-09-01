"""The one LLM provider (REQ-051, S5-T02).

Application code holds `signalforge.domain.llm.ports.LlmProvider` and nothing
else; this is the only implementation, and everything the playbook asks of the
model path lives here rather than being re-solved per agent:

- the OpenAI-compatible GenAI Lab gateway, and only that host (`hosts.py`);
- structured JSON output against a schema, validated here before anyone sees it;
- retries for transient failures only, inside a bounded timeout (`gateway.py`);
- a cache keyed by model + prompt/config version + normalised input + schema;
- per-incident token accounting, checked before a call and charged after it;
- model selection by classification, never by a model id at a call site;
- a deterministic fallback and `system.llm.degraded` when a model is not usable.

**The provider never raises for a model failure.** Every path returns a
`CompletionResult`, and a caller reads `degraded` rather than catching. That is
what makes "revoke the key mid-run and the system keeps working" a property of
every call site instead of a discipline each one has to remember.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Mapping, Optional, Sequence

import httpx
from jsonschema import Draft202012Validator
from jsonschema import ValidationError as SchemaValidationError

from signalforge.domain.llm.budget import (
    InMemoryTokenLedger,
    budget_key,
    estimate_tokens,
)
from signalforge.domain.llm.entities import (
    CompletionRequest,
    CompletionResult,
    DegradedNotice,
    DegradedReason,
    EmbeddingResult,
    Message,
    ModelClass,
    TokenUsage,
)
from signalforge.domain.llm.ports import (
    DegradationListener,
    LlmProvider,
    ResponseCache,
    TokenLedger,
)
from signalforge.domain.observability.health import PLATFORM_TENANT_ID
from signalforge.infrastructure.config import LlmSettings, load_llm_settings
from signalforge.infrastructure.llm.cache import (
    cache_key,
    decode_usage,
    embedding_cache_key,
    encode_completion,
    encode_embeddings,
)
from signalforge.infrastructure.llm.gateway import GatewayFailure, GenAiLabGateway
from signalforge.infrastructure.llm.hosts import describe_endpoint

logger = logging.getLogger("signalforge.llm")

SCHEMA_RETRY_STATUS = frozenset({400, 404, 415, 422})
"""Statuses that mean "this gateway or model does not take that response_format"
rather than "your request was wrong" - the one case worth a second attempt with
a weaker structured-output mode."""


class GatewayLlmProvider(LlmProvider):
    def __init__(
        self,
        settings: LlmSettings,
        gateway: GenAiLabGateway,
        *,
        cache: Optional[ResponseCache] = None,
        ledger: Optional[TokenLedger] = None,
        listener: Optional[DegradationListener] = None,
        metrics: Optional[Any] = None,
    ):
        self._settings = settings
        self._gateway = gateway
        self._cache = cache if settings.cache_enabled else None
        self._ledger = ledger or InMemoryTokenLedger(settings.incident_token_budget)
        self._listener = listener
        self._metrics = metrics
        if not settings.endpoint_approved:
            # Loud, once, at wiring time - and then the platform runs
            # deterministically rather than refusing to start (CLAUDE.md rule 6).
            logger.error(
                "llm endpoint is not an approved gateway; the model path is disabled",
                extra={"endpoint": describe_endpoint(settings.base_url)},
            )

    def __repr__(self) -> str:
        return (
            f"<GatewayLlmProvider {describe_endpoint(self._settings.base_url)} "
            f"configured={self.configured}>"
        )

    # -- introspection --------------------------------------------------------

    @property
    def settings(self) -> LlmSettings:
        return self._settings

    @property
    def configured(self) -> bool:
        return self._settings.configured

    @property
    def models(self) -> Mapping[ModelClass, str]:
        return dict(self._settings.models)

    @property
    def status(self) -> dict[str, object]:
        """What `/status` and the cockpit's DEGRADED badge read. Credential-free
        by construction: it is `LlmSettings.redacted`."""
        return self._settings.redacted

    async def aclose(self) -> None:
        await self._gateway.aclose()

    # -- completions ----------------------------------------------------------

    async def complete(self, request: CompletionRequest) -> CompletionResult:
        model = self._settings.model_for(request.model_class)
        tenant = request.tenant_id or PLATFORM_TENANT_ID
        key = self._completion_key(request, model)

        if key is not None:
            hit = await self._cache.get(tenant, key)
            if hit is not None:
                prompt, completion = decode_usage(hit)
                self._count("cache_hit", request.model_class)
                return CompletionResult(
                    purpose=request.purpose,
                    text=str(hit.get("text", "")),
                    data=hit.get("data"),
                    model_class=request.model_class,
                    model=str(hit.get("model", model)),
                    provider=self._settings.provider,
                    cached=True,
                    usage=TokenUsage(prompt, completion),
                )

        if not self.configured:
            return await self._degrade(
                request,
                model,
                DegradedReason.UNCONFIGURED,
                f"no approved, credentialled gateway "
                f"({describe_endpoint(self._settings.base_url)})",
            )

        estimate = _estimate(request)
        lane = budget_key(request.incident_id)
        if self._ledger.would_exceed(lane, estimate):
            return await self._degrade(
                request,
                model,
                DegradedReason.BUDGET_EXHAUSTED,
                f"token budget for this incident is spent "
                f"({self._ledger.spent(lane).total} used)",
            )

        try:
            payload = await self._chat(request, model)
        except GatewayFailure as failure:
            return await self._degrade(request, model, failure.reason, failure.detail)

        try:
            text, usage, served_model = _read_completion(payload, model)
            data = self._parse_structured(request, text)
        except GatewayFailure as failure:
            self._ledger.charge(lane, _usage_of(payload))
            return await self._degrade(request, model, failure.reason, failure.detail)

        self._ledger.charge(lane, usage)
        self._count("ok", request.model_class)
        self._tokens(request.model_class, usage)

        if key is not None:
            await self._cache.put(
                tenant,
                key,
                model=served_model,
                purpose=request.purpose,
                payload={
                    **encode_completion(
                        text=text, data=data, model=served_model, usage=usage
                    ),
                    "provider": self._settings.provider,
                },
            )
        return CompletionResult(
            purpose=request.purpose,
            text=text,
            data=data,
            model_class=request.model_class,
            model=served_model,
            provider=self._settings.provider,
            usage=usage,
        )

    async def _chat(self, request: CompletionRequest, model: str) -> dict:
        """One call, with a single retry in a weaker structured-output mode.

        The gateway fronts a mixed fleet - Azure-hosted, Gemini, DeepSeek - and
        not all of them accept `response_format: json_schema`. Rather than
        pinning a per-model capability table in configuration, an explicit
        "I don't take that" answer is retried once as a plain JSON object; the
        schema is in the prompt either way and is validated here regardless, so
        the guarantee the caller gets does not depend on which mode succeeded.
        """
        messages = _wire_messages(request)
        try:
            return await self._gateway.chat(
                model=model,
                messages=messages,
                max_tokens=request.max_tokens,
                temperature=request.temperature,
                response_format=_json_schema_format(request.schema),
            )
        except GatewayFailure as failure:
            if request.schema is None or failure.status_code not in SCHEMA_RETRY_STATUS:
                raise
            logger.info(
                "gateway refused json_schema output; retrying as a json object",
                extra={"purpose": request.purpose, "model": model},
            )
            return await self._gateway.chat(
                model=model,
                messages=messages,
                max_tokens=request.max_tokens,
                temperature=request.temperature,
                response_format={"type": "json_object"},
            )

    def _parse_structured(self, request: CompletionRequest, text: str) -> Optional[Any]:
        """Unstructured calls pass through; structured ones are validated.

        An off-schema object is treated as no answer at all. A half-parsed
        object reaching the incident graph would be worse than the deterministic
        fallback, because it would arrive wearing the model's authority.
        """
        if request.schema is None:
            return None
        try:
            data = json.loads(_strip_code_fence(text))
        except ValueError:
            raise GatewayFailure(
                DegradedReason.INVALID_RESPONSE, "model did not return JSON"
            ) from None
        try:
            Draft202012Validator(request.schema).validate(data)
        except SchemaValidationError as exc:
            raise GatewayFailure(
                DegradedReason.INVALID_RESPONSE,
                f"model answer did not match the requested schema: {exc.message[:160]}",
            ) from None
        return data

    def _completion_key(self, request: CompletionRequest, model: str) -> Optional[str]:
        if self._cache is None or not request.cacheable:
            return None
        return cache_key(
            request,
            provider=self._settings.provider,
            model=model,
            config_version=self._settings.prompt_version,
        )

    # -- embeddings -----------------------------------------------------------

    async def embed(
        self,
        texts: Sequence[str],
        *,
        tenant_id: Optional[str] = None,
        incident_id: Optional[str] = None,
        purpose: str = "embed",
    ) -> EmbeddingResult:
        model = self._settings.model_for(ModelClass.EMBEDDINGS)
        tenant = tenant_id or PLATFORM_TENANT_ID
        wanted = [text for text in texts]
        if not wanted:
            return EmbeddingResult(vectors=(), model=model, provider=self._settings.provider)

        key = (
            embedding_cache_key(
                wanted,
                provider=self._settings.provider,
                model=model,
                config_version=self._settings.prompt_version,
            )
            if self._cache is not None
            else None
        )
        if key is not None:
            hit = await self._cache.get(tenant, key)
            if hit is not None:
                prompt, completion = decode_usage(hit)
                self._count("cache_hit", ModelClass.EMBEDDINGS)
                return EmbeddingResult(
                    vectors=tuple(tuple(v) for v in hit.get("vectors", [])),
                    model=str(hit.get("model", model)),
                    provider=self._settings.provider,
                    cached=True,
                    usage=TokenUsage(prompt, completion),
                )

        if not self.configured:
            return await self._degrade_embeddings(
                model,
                purpose,
                DegradedReason.UNCONFIGURED,
                f"no approved, credentialled gateway "
                f"({describe_endpoint(self._settings.base_url)})",
                tenant_id=tenant_id,
                incident_id=incident_id,
            )

        lane = budget_key(incident_id)
        estimate = sum(estimate_tokens(text) for text in wanted)
        if self._ledger.would_exceed(lane, estimate):
            return await self._degrade_embeddings(
                model,
                purpose,
                DegradedReason.BUDGET_EXHAUSTED,
                f"token budget for this incident is spent "
                f"({self._ledger.spent(lane).total} used)",
                tenant_id=tenant_id,
                incident_id=incident_id,
            )

        try:
            payload = await self._gateway.embeddings(model=model, texts=wanted)
            vectors, usage, served_model = _read_embeddings(payload, model, len(wanted))
        except GatewayFailure as failure:
            return await self._degrade_embeddings(
                model,
                purpose,
                failure.reason,
                failure.detail,
                tenant_id=tenant_id,
                incident_id=incident_id,
            )

        self._ledger.charge(lane, usage)
        self._count("ok", ModelClass.EMBEDDINGS)
        self._tokens(ModelClass.EMBEDDINGS, usage)
        if key is not None:
            await self._cache.put(
                tenant,
                key,
                model=served_model,
                purpose=purpose,
                payload={
                    **encode_embeddings(vectors=vectors, model=served_model, usage=usage),
                    "provider": self._settings.provider,
                },
            )
        return EmbeddingResult(
            vectors=vectors,
            model=served_model,
            provider=self._settings.provider,
            usage=usage,
        )

    # -- degradation ----------------------------------------------------------

    async def _degrade(
        self,
        request: CompletionRequest,
        model: str,
        reason: DegradedReason,
        detail: str,
    ) -> CompletionResult:
        await self._announce(
            DegradedNotice(
                reason=reason,
                purpose=request.purpose,
                model_class=request.model_class,
                model=model,
                provider=self._settings.provider,
                detail=self._scrub(detail),
                tenant_id=request.tenant_id,
                incident_id=request.incident_id,
            )
        )
        text, data = _fallback_of(request.fallback)
        return CompletionResult(
            purpose=request.purpose,
            text=text,
            data=data,
            model_class=request.model_class,
            model=model,
            provider=self._settings.provider,
            degraded=True,
            reason=reason,
        )

    async def _degrade_embeddings(
        self,
        model: str,
        purpose: str,
        reason: DegradedReason,
        detail: str,
        *,
        tenant_id: Optional[str],
        incident_id: Optional[str],
    ) -> EmbeddingResult:
        await self._announce(
            DegradedNotice(
                reason=reason,
                purpose=purpose,
                model_class=ModelClass.EMBEDDINGS,
                model=model,
                provider=self._settings.provider,
                detail=self._scrub(detail),
                tenant_id=tenant_id,
                incident_id=incident_id,
            )
        )
        # Empty vectors rather than an exception: kb-index blends what it has
        # and falls back to BM25 when there is nothing to blend (REQ-035).
        return EmbeddingResult(
            vectors=(),
            model=model,
            provider=self._settings.provider,
            degraded=True,
            reason=reason,
        )

    async def _announce(self, notice: DegradedNotice) -> None:
        self._count("degraded", notice.model_class, reason=notice.reason)
        logger.warning(
            "llm call degraded",
            extra={
                "purpose": notice.purpose,
                "reason": notice.reason.value,
                "model_class": notice.model_class.value,
                "endpoint": describe_endpoint(self._settings.base_url),
            },
        )
        if self._listener is None:
            return
        try:
            await self._listener.on_degraded(notice)
        except Exception as exc:
            # Announcing a degradation must not itself be able to fail the call
            # that degraded: the deterministic answer is still owed to a human.
            logger.warning(
                "could not publish system.llm.degraded",
                extra={"error": f"{type(exc).__name__}: {exc}"},
            )

    def _scrub(self, detail: str) -> str:
        """Belt and braces: a gateway that echoed the credential back in an
        error body must not put it into an audited event."""
        key = self._settings.api_key
        return detail.replace(key, "***") if key else detail

    # -- metrics --------------------------------------------------------------

    def _count(
        self,
        outcome: str,
        model_class: ModelClass,
        reason: Optional[DegradedReason] = None,
    ) -> None:
        if self._metrics is None:
            return
        self._metrics.increment(
            "signalforge_llm_calls_total",
            {
                "model_class": model_class.value,
                "outcome": outcome,
                "reason": reason.value if reason else "",
            },
        )

    def _tokens(self, model_class: ModelClass, usage: TokenUsage) -> None:
        if self._metrics is None or usage.total == 0:
            return
        for kind, value in (("prompt", usage.prompt), ("completion", usage.completion)):
            if value:
                self._metrics.increment(
                    "signalforge_llm_tokens_total",
                    {"model_class": model_class.value, "kind": kind},
                    value,
                )


# --- wire helpers -------------------------------------------------------------


def _wire_messages(request: CompletionRequest) -> list[dict[str, str]]:
    """The messages as the gateway sees them.

    A schema is restated in the prompt as well as in `response_format` because
    that is what makes the weaker JSON-object mode - and models that ignore
    `response_format` entirely - produce something the validator accepts.
    """
    messages = [{"role": m.role, "content": m.content} for m in request.messages]
    if request.schema is not None:
        messages.append(
            {
                "role": "system",
                "content": (
                    "Answer with a single JSON object and nothing else. It must "
                    "validate against this JSON Schema:\n"
                    + json.dumps(request.schema, sort_keys=True, separators=(",", ":"))
                ),
            }
        )
    return messages


def _json_schema_format(schema: Optional[dict]) -> Optional[dict]:
    if schema is None:
        return None
    return {
        "type": "json_schema",
        "json_schema": {"name": "signalforge_response", "schema": schema, "strict": False},
    }


def _read_completion(payload: dict, requested_model: str) -> tuple[str, TokenUsage, str]:
    choices = payload.get("choices") or []
    if not choices:
        raise GatewayFailure(
            DegradedReason.INVALID_RESPONSE, "gateway returned no choices"
        )
    message = (choices[0] or {}).get("message") or {}
    content = message.get("content")
    if not isinstance(content, str) or not content.strip():
        raise GatewayFailure(
            DegradedReason.INVALID_RESPONSE, "gateway returned an empty completion"
        )
    return content, _usage_of(payload), str(payload.get("model") or requested_model)


def _read_embeddings(
    payload: dict, requested_model: str, expected: int
) -> tuple[tuple[tuple[float, ...], ...], TokenUsage, str]:
    data = payload.get("data") or []
    if len(data) != expected:
        raise GatewayFailure(
            DegradedReason.INVALID_RESPONSE,
            f"gateway returned {len(data)} vectors for {expected} inputs",
        )
    vectors = []
    # `index` is honoured rather than assumed: the contract allows the list to
    # come back in any order, and a silently transposed corpus would poison
    # every similarity score that follows.
    for entry in sorted(data, key=lambda e: e.get("index", 0)):
        vector = entry.get("embedding")
        if not isinstance(vector, list) or not vector:
            raise GatewayFailure(
                DegradedReason.INVALID_RESPONSE, "gateway returned an empty vector"
            )
        vectors.append(tuple(float(value) for value in vector))
    return (
        tuple(vectors),
        _usage_of(payload),
        str(payload.get("model") or requested_model),
    )


def _usage_of(payload: dict) -> TokenUsage:
    usage = payload.get("usage") or {}
    return TokenUsage(
        prompt=int(usage.get("prompt_tokens", 0) or 0),
        completion=int(usage.get("completion_tokens", 0) or 0),
    )


def _estimate(request: CompletionRequest) -> int:
    return (
        sum(estimate_tokens(message.content) for message in request.messages)
        + request.max_tokens
    )


def _fallback_of(fallback: Any) -> tuple[str, Optional[Any]]:
    if fallback is None:
        return "", None
    if isinstance(fallback, str):
        return fallback, None
    return json.dumps(fallback, sort_keys=True, ensure_ascii=False), fallback


def _strip_code_fence(text: str) -> str:
    """Models fence JSON even when asked not to. Unwrapping it here is not
    leniency about the schema - the validator still decides - it just avoids
    degrading a correct answer over three backticks."""
    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped
    body = stripped[3:]
    if body.lower().startswith("json"):
        body = body[4:]
    return body.rsplit("```", 1)[0].strip()


# --- composition --------------------------------------------------------------


def build_llm_provider(
    settings: Optional[LlmSettings] = None,
    *,
    cache: Optional[ResponseCache] = None,
    ledger: Optional[TokenLedger] = None,
    listener: Optional[DegradationListener] = None,
    metrics: Optional[Any] = None,
    transport: Optional[httpx.AsyncBaseTransport] = None,
    backoff_s: Optional[float] = None,
) -> GatewayLlmProvider:
    """The composition root's one call. Everything optional has a working
    default, so a node with no cache, no bus and no key still gets a provider
    that answers deterministically."""
    resolved = settings or load_llm_settings()
    gateway = GenAiLabGateway(
        resolved,
        transport=transport,
        **({"backoff_s": backoff_s} if backoff_s is not None else {}),
    )
    return GatewayLlmProvider(
        resolved,
        gateway,
        cache=cache,
        ledger=ledger,
        listener=listener,
        metrics=metrics,
    )


__all__ = ["GatewayLlmProvider", "build_llm_provider", "Message"]
