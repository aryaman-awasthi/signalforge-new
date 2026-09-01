"""The one HTTP client SignalForge points at an integrated system (REQ-031).

Every outbound adapter goes through this class, which is what makes four
platform-wide promises true in one place rather than six times over:

- **Vendor errors never escape.** httpx raises httpx exceptions; callers see the
  frozen `contracts.errors` taxonomy, so failure handling upstream stays
  vendor-agnostic. A timeout in particular is its own error, because a timeout
  is not a failure - it is an unknown outcome, and the action gateway treats the
  two differently.
- **Limits are ceilings, not suggestions.** The manifest's `timeout_s` bounds
  the call, `max_calls_per_minute` is charged against the binding's budget
  before the request is made, and the response body is read in chunks and
  abandoned the moment it exceeds `max_result_bytes` - a hostile or broken
  target cannot exhaust this process's memory.
- **Credentials go on the wire and nowhere else.** They are held for the life of
  one call, never logged, and `__repr__` does not carry them.
- **Nothing here knows an application.** It is given a base URL from a binding's
  config and a path from an adapter; no product code names an app.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from typing import Any, Mapping, Optional
from urllib.parse import urljoin, urlparse

import httpx

from signalforge.contracts.errors import (
    AdapterAuthError,
    AdapterError,
    AdapterTimeoutError,
    AdapterUnavailableError,
)
from signalforge.contracts.ingest import HDR_SIGNATURE, HDR_TIMESTAMP
from signalforge.domain.adapters.manifest import AdapterAuth, AdapterLimits, AuthMethod
from signalforge.infrastructure.adapters.limits import CallBudget

DEFAULT_API_KEY_HEADER = "X-API-Key"
ALLOWED_SCHEMES = ("http", "https")


def normalize_base_url(base_url: str) -> str:
    """Reject anything that is not an absolute http(s) URL.

    A binding's base URL is operator input that this process then makes requests
    to, so the scheme check is a control, not tidiness: `file://` and
    `unix://` are how a config field becomes a local file read.
    """
    text = (base_url or "").strip()
    parsed = urlparse(text)
    if parsed.scheme not in ALLOWED_SCHEMES or not parsed.netloc:
        raise AdapterError(
            f"base_url must be an absolute http(s) URL, got {base_url!r}"
        )
    return text.rstrip("/") + "/"


class HttpTransport:
    def __init__(
        self,
        *,
        base_url: str,
        auth: AdapterAuth,
        credentials: Mapping[str, str],
        limits: AdapterLimits,
        budget: Optional[CallBudget] = None,
        budget_key: str = "",
        verify_tls: bool = True,
        client_factory=None,
    ):
        self._base_url = normalize_base_url(base_url)
        self._auth = auth
        self._credentials = dict(credentials)
        self._limits = limits
        self._budget = budget
        self._budget_key = budget_key or self._base_url
        self._verify_tls = verify_tls
        # Tests and the reference harness hand in a factory that returns an
        # httpx client wired to an ASGI app, so the adapter suite exercises this
        # code against a real server rather than a mocked-out method.
        self._client_factory = client_factory

    def __repr__(self) -> str:
        return f"HttpTransport(base_url={self._base_url!r}, credentials=<redacted>)"

    @property
    def base_url(self) -> str:
        return self._base_url

    def url(self, path: str) -> str:
        return urljoin(self._base_url, path.lstrip("/"))

    # -- requests ----------------------------------------------------------

    async def get_text(
        self, path: str, params: Optional[Mapping[str, Any]] = None
    ) -> str:
        return await self._request("GET", path, params=params)

    async def get_json(
        self, path: str, params: Optional[Mapping[str, Any]] = None
    ) -> Any:
        return _decode_json(await self._request("GET", path, params=params), path)

    async def get_json_optional(
        self, path: str, params: Optional[Mapping[str, Any]] = None
    ) -> Optional[Any]:
        """None on 404. "It does not exist" is an answer to some questions -
        "has this idempotency key ever run" is the important one - and turning
        it into an exception would make the caller guess."""
        status, text = await self.send("GET", path, params=params)
        if status == 404:
            return None
        self._raise_for_status(status, self.url(path), text)
        return _decode_json(text, path)

    async def post_json(
        self, path: str, body: Optional[Mapping[str, Any]] = None
    ) -> Any:
        return _decode_json(await self._request("POST", path, body=body), path)

    async def reachable(self, path: str) -> bool:
        """A cheap liveness probe. Any 2xx is alive; auth failures are raised,
        because "we reached it and it refused our key" is a different fact from
        "it is down" and the health loop scores them differently."""
        await self._request("GET", path, allow_empty=True)
        return True

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[Mapping[str, Any]] = None,
        body: Optional[Mapping[str, Any]] = None,
        allow_empty: bool = False,
    ) -> str:
        status, text = await self.send(method, path, params=params, body=body)
        self._raise_for_status(status, self.url(path), text)
        if not text and not allow_empty:
            return ""
        return text

    async def send(
        self,
        method: str,
        path: str,
        *,
        params: Optional[Mapping[str, Any]] = None,
        body: Optional[Mapping[str, Any]] = None,
        timeout_s: Optional[float] = None,
    ) -> tuple[int, str]:
        """The raw call: status and bounded body, no status interpretation.

        Callers that must see a status code rather than an exception use this -
        a synthetic probe asserting `expected_status`, and the actions adapter
        turning a 404 from the execution lookup into "never applied" rather than
        into an error.
        """
        if self._budget is not None:
            self._budget.charge(self._budget_key, self._limits.max_calls_per_minute)
        raw_body = (
            json.dumps(dict(body), separators=(",", ":")).encode("utf-8")
            if body is not None
            else b""
        )
        headers = self._headers(raw_body)
        url = self.url(path)
        # A caller may only ever tighten the manifest's timeout, never widen it.
        effective_timeout = min(timeout_s or self._limits.timeout_s, self._limits.timeout_s)
        try:
            async with self._client(effective_timeout) as client:
                async with client.stream(
                    method,
                    url,
                    params=dict(params or {}),
                    headers=headers,
                    content=raw_body if raw_body else None,
                ) as response:
                    text = await self._read_bounded(response, url)
                    return response.status_code, text
        except httpx.TimeoutException as exc:
            raise AdapterTimeoutError(
                f"{method} {url} did not answer within {effective_timeout}s"
            ) from exc
        except httpx.TransportError as exc:
            raise AdapterUnavailableError(f"{method} {url} unreachable: {exc}") from exc

    def _client(self, timeout_s: float) -> httpx.AsyncClient:
        if self._client_factory is not None:
            return self._client_factory(timeout_s)
        return httpx.AsyncClient(
            timeout=httpx.Timeout(timeout_s),
            verify=self._verify_tls,
            follow_redirects=False,
        )

    async def _read_bounded(self, response: httpx.Response, url: str) -> str:
        """Read at most max_result_bytes, then stop and say so.

        Streaming rather than `.text` on purpose: the point of a size limit is
        that the oversized body is never fully in this process's memory.
        """
        limit = self._limits.max_result_bytes
        chunks: list[bytes] = []
        size = 0
        async for chunk in response.aiter_bytes():
            size += len(chunk)
            if size > limit:
                raise AdapterError(
                    f"response from {url} exceeds the adapter's max_result_bytes "
                    f"limit ({limit})"
                )
            chunks.append(chunk)
        return b"".join(chunks).decode("utf-8", errors="replace")

    @staticmethod
    def _raise_for_status(status: int, url: str, text: str) -> None:
        if status < 400:
            return
        detail = text.strip()[:200]
        if status in (401, 403):
            raise AdapterAuthError(f"{url} rejected the credential ({status}) {detail}")
        if status == 429:
            raise AdapterUnavailableError(f"{url} rate-limited this client (429)")
        if status >= 500:
            raise AdapterUnavailableError(f"{url} answered {status}: {detail}")
        raise AdapterError(f"{url} answered {status}: {detail}")

    # -- auth --------------------------------------------------------------

    def _headers(self, raw_body: bytes) -> dict[str, str]:
        headers = {"Accept": "application/json, text/plain;q=0.9, */*;q=0.5"}
        if raw_body:
            headers["Content-Type"] = "application/json"
        method = self._auth.method
        if method is AuthMethod.NONE:
            return headers
        missing = [
            name for name in self._auth.credential_fields if not self._credentials.get(name)
        ]
        if missing:
            raise AdapterAuthError(
                f"credentials {sorted(missing)} were not supplied to the adapter"
            )
        values = [self._credentials[name] for name in self._auth.credential_fields]
        if method is AuthMethod.BEARER:
            headers[self._auth.header or "Authorization"] = f"Bearer {values[0]}"
        elif method is AuthMethod.API_KEY:
            headers[self._auth.header or DEFAULT_API_KEY_HEADER] = values[0]
        elif method is AuthMethod.BASIC:
            if len(values) < 2:
                raise AdapterAuthError(
                    "basic auth needs two credential_fields, user then password"
                )
            token = base64.b64encode(f"{values[0]}:{values[1]}".encode()).decode()
            headers[self._auth.header or "Authorization"] = f"Basic {token}"
        elif method is AuthMethod.HMAC:
            # The same scheme the ingest contract freezes for inbound calls,
            # used outbound: sign "<timestamp>.<raw body>" so a target can
            # verify us with the code it already runs to verify itself.
            timestamp = str(int(time.time()))
            signature = hmac.new(
                values[0].encode("utf-8"),
                f"{timestamp}.".encode("utf-8") + raw_body,
                hashlib.sha256,
            ).hexdigest()
            headers[HDR_TIMESTAMP] = timestamp
            headers[HDR_SIGNATURE] = signature
        return headers


def _decode_json(text: str, path: str) -> Any:
    if not text.strip():
        raise AdapterError(f"{path} returned an empty body where JSON was expected")
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise AdapterError(f"{path} did not return valid JSON: {exc}") from exc
