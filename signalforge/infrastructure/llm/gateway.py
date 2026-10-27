"""The OpenAI-compatible client for the GenAI Lab gateway.

The protocol is OpenAI's; the endpoint is the organisation's LiteLLM gateway
(`hosts.py` decides that, and nothing here can widen it). Keeping the wire
format rather than a vendor SDK is deliberate: it is two endpoints, it costs no
dependency, and it means the model fleet - `azure/...`, `azure_ai/...`,
`gemini-...` - is a configured string rather than a client library per vendor.

What this class owns is exactly the transport contract:

- **A bounded timeout on every call.** No code path can issue an unbounded one.
- **Retry only what is transient.** Timeouts, transport failures, 429 and 5xx
  are retried with backoff; a 400 or a 401 is a fact about the request or the
  credential and retrying it just spends the budget again.
- **Typed failures.** Callers see `GatewayFailure` carrying a closed-vocabulary
  reason, never an httpx exception - so the degraded path is the same shape
  whatever went wrong underneath.
- **No credential in any message it raises or logs.**
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional, Sequence

import httpx

from signalforge.domain.llm.entities import DegradedReason
from signalforge.domain.llm.errors import LlmUnavailable
from signalforge.infrastructure.config import LlmSettings
from signalforge.infrastructure.llm.hosts import describe_endpoint

logger = logging.getLogger("signalforge.llm")

CHAT_PATH = "/chat/completions"
EMBEDDINGS_PATH = "/embeddings"
MODELS_PATH = "/models"

RETRYABLE_STATUS = frozenset({408, 409, 425, 429, 500, 502, 503, 504})
DEFAULT_BACKOFF_S = 0.25


class GatewayFailure(LlmUnavailable):
    """A call that did not produce a usable answer, and why - in the platform's
    own vocabulary rather than the vendor's."""

    def __init__(
        self, reason: DegradedReason, detail: str, status_code: Optional[int] = None
    ):
        super().__init__(detail)
        self.reason = reason
        self.detail = detail
        self.status_code = status_code


class GenAiLabGateway:
    """One long-lived async client per provider instance.

    The client is built lazily and only when the settings describe an approved,
    credentialled endpoint - so an unconfigured or misdirected deployment holds
    no client at all and cannot issue a request even by mistake.
    """

    def __init__(
        self,
        settings: LlmSettings,
        *,
        transport: Optional[httpx.AsyncBaseTransport] = None,
        backoff_s: float = DEFAULT_BACKOFF_S,
    ):
        self._settings = settings
        self._transport = transport
        self._backoff_s = max(0.0, backoff_s)
        self._client: Optional[httpx.AsyncClient] = None

    def __repr__(self) -> str:  # never the credential
        return f"<GenAiLabGateway {describe_endpoint(self._settings.base_url)}>"

    @property
    def usable(self) -> bool:
        return self._settings.configured

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            if not self._settings.configured:
                raise GatewayFailure(
                    DegradedReason.UNCONFIGURED,
                    f"no approved, credentialled gateway "
                    f"({describe_endpoint(self._settings.base_url)})",
                )
            self._client = httpx.AsyncClient(
                base_url=self._settings.base_url,
                timeout=httpx.Timeout(self._settings.timeout_s),
                headers={
                    "Authorization": f"Bearer {self._settings.api_key}",
                    "Content-Type": "application/json",
                    "User-Agent": "signalforge/llm",
                },
                # TLS verification is on unless a CA bundle path was supplied,
                # and there is no configuration that turns it off.
                verify=self._settings.verify,
                transport=self._transport,
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    # -- calls ----------------------------------------------------------------

    async def chat(
        self,
        *,
        model: str,
        messages: Sequence[dict[str, str]],
        max_tokens: int,
        temperature: Optional[float],
        response_format: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": model,
            "messages": list(messages),
            "max_tokens": max_tokens,
        }
        # None means "send no temperature at all", which is different from
        # sending the model's default: some models refuse any value below
        # their own and accept only the field's absence.
        if temperature is not None:
            body["temperature"] = temperature
        if response_format is not None:
            body["response_format"] = response_format
        return await self._post(CHAT_PATH, body)

    async def embeddings(self, *, model: str, texts: Sequence[str]) -> dict[str, Any]:
        return await self._post(EMBEDDINGS_PATH, {"model": model, "input": list(texts)})

    async def list_models(self) -> list[str]:
        """`GET /v1/models`, used by the opt-in configuration check.

        Never called on the incident path: a model id that the gateway does not
        serve shows up as a degraded call with a reason, and a startup probe
        that blocked the boot on a network round trip would be the wrong trade.
        """
        response = await self._http().get(MODELS_PATH)
        response.raise_for_status()
        payload = response.json()
        return [entry.get("id", "") for entry in payload.get("data", []) if entry.get("id")]

    # -- transport ------------------------------------------------------------

    async def _post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        client = self._http()
        attempts = self._settings.max_attempts
        last: GatewayFailure = GatewayFailure(
            DegradedReason.TRANSPORT, "no attempt was made"
        )
        for attempt in range(1, attempts + 1):
            try:
                response = await client.post(path, json=body)
            except httpx.TimeoutException as exc:
                last = GatewayFailure(
                    DegradedReason.TIMEOUT,
                    f"gateway timed out after {self._settings.timeout_s}s "
                    f"({type(exc).__name__})",
                )
            except httpx.HTTPError as exc:
                last = GatewayFailure(
                    DegradedReason.TRANSPORT,
                    f"gateway unreachable ({type(exc).__name__})",
                )
            else:
                if response.status_code < 400:
                    try:
                        return response.json()
                    except ValueError:
                        raise GatewayFailure(
                            DegradedReason.INVALID_RESPONSE,
                            "gateway returned a non-JSON body",
                        ) from None
                failure = GatewayFailure(
                    DegradedReason.UPSTREAM_ERROR,
                    f"gateway answered {response.status_code}: "
                    f"{_error_text(response)}",
                    status_code=response.status_code,
                )
                if response.status_code not in RETRYABLE_STATUS:
                    # A 400 or a 401 will fail identically next time; retrying
                    # only spends the incident's budget on the same mistake.
                    raise failure
                last = failure

            logger.warning(
                "llm gateway call failed",
                extra={
                    "endpoint": describe_endpoint(self._settings.base_url),
                    "path": path,
                    "attempt": attempt,
                    "reason": last.reason.value,
                },
            )
            if attempt < attempts and self._backoff_s:
                await asyncio.sleep(self._backoff_s * (2 ** (attempt - 1)))
        raise last


def _error_text(response: httpx.Response) -> str:
    """A short, credential-free description of a gateway error body.

    Truncated because it lands in an event, and taken from the structured error
    field when there is one so that a gateway echoing the request - headers
    included - cannot push a credential into an audit row.
    """
    try:
        payload = response.json()
    except ValueError:
        return "unparseable error body"
    error = payload.get("error") if isinstance(payload, dict) else None
    if isinstance(error, dict):
        message = str(error.get("message") or error.get("type") or "")
    else:
        message = str(error or "")
    return message[:200] if message else "no error detail"
