"""The auto-discovery probe (REQ-036).

Deliberately *not* the adapter transport. Discovery runs before any binding
exists, so there is no manifest to take limits from and no credential to
present - which means the four things HttpTransport gets from a manifest have to
be hardcoded here instead, and they are:

- the scheme is http(s) only, checked before the request, because the URL is
  assembled from operator input and `file://` would turn a text field into a
  local file read;
- redirects are never followed, so a target cannot walk this process onto
  another host;
- the response body is read in bounded chunks and abandoned, because discovery
  only needs a status code and a content type;
- the timeout is bounded by a module ceiling, so a discovery run over a dozen
  endpoints cannot hang a request thread on the slowest of them.

A non-2xx is a *result*, not an error: 404 is a definite "this application does
not serve that part of the contract", and 401 is a definite "it does, and it
wants a credential". Both are what discovery is asking.
"""

from __future__ import annotations

import time
from typing import Optional
from urllib.parse import urlparse

import httpx

from signalforge.domain.onboarding.ports import EndpointProbe, ProbeOutcome
from signalforge.infrastructure.adapters.services import AdapterServices

ALLOWED_SCHEMES = ("http", "https")
MAX_PROBE_TIMEOUT_S = 5.0
MAX_PROBE_BYTES = 64 * 1024


class HttpEndpointProbe(EndpointProbe):
    def __init__(self, services: AdapterServices, *, max_timeout_s: float = MAX_PROBE_TIMEOUT_S):
        # The adapter services bundle is read at call time rather than unpacked
        # here, so a deployment (or a test) that swaps the HTTP client factory
        # after wiring is honoured without re-registering anything.
        self._services = services
        self._max_timeout_s = min(max_timeout_s, MAX_PROBE_TIMEOUT_S)

    def __repr__(self) -> str:
        return f"HttpEndpointProbe(max_timeout_s={self._max_timeout_s})"

    async def probe(self, url: str, *, timeout_s: float = 3.0) -> ProbeOutcome:
        parsed = urlparse(url)
        if parsed.scheme not in ALLOWED_SCHEMES or not parsed.netloc:
            return ProbeOutcome(
                url=url,
                reachable=False,
                detail=f"not an absolute http(s) URL: {url!r}",
            )
        bounded = max(0.1, min(float(timeout_s), self._max_timeout_s))
        started = time.perf_counter()
        try:
            async with self._client(bounded) as client:
                async with client.stream("GET", url) as response:
                    content_type = response.headers.get("content-type", "")
                    await _drain(response)
                    return ProbeOutcome(
                        url=url,
                        reachable=True,
                        status_code=response.status_code,
                        latency_ms=(time.perf_counter() - started) * 1000.0,
                        content_type=content_type,
                        detail="",
                    )
        except httpx.TimeoutException:
            return ProbeOutcome(
                url=url,
                reachable=False,
                latency_ms=(time.perf_counter() - started) * 1000.0,
                detail=f"no answer within {bounded}s",
            )
        except httpx.TransportError as exc:
            return ProbeOutcome(
                url=url,
                reachable=False,
                latency_ms=(time.perf_counter() - started) * 1000.0,
                detail=f"unreachable: {exc}"[:200],
            )

    def _client(self, timeout_s: float) -> httpx.AsyncClient:
        factory: Optional[object] = self._services.client_factory
        if factory is not None:
            return factory(timeout_s)  # type: ignore[operator]
        return httpx.AsyncClient(
            timeout=httpx.Timeout(timeout_s),
            verify=self._services.verify_tls,
            follow_redirects=False,
        )


async def _drain(response: httpx.Response) -> None:
    """Read and discard at most MAX_PROBE_BYTES so the connection closes cleanly
    without an oversized body ever being fully in this process's memory."""
    seen = 0
    async for chunk in response.aiter_bytes():
        seen += len(chunk)
        if seen >= MAX_PROBE_BYTES:
            return
