"""Request context, structured access logs and HTTP metrics (REQ-025).

Every request gets an id - the caller's X-Request-Id when it supplies one, so a
trace survives the hop from the cockpit - which is put in a context variable and
therefore appears on every log line the request produces, however deep. The
route template rather than the concrete path becomes the metric label, or
/events/{event_id} would blow the metric cardinality up one event at a time.
"""

from __future__ import annotations

import logging
import time

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.types import ASGIApp

from signalforge.infrastructure.observability.logging import (
    new_request_id,
    request_id_var,
)

logger = logging.getLogger("signalforge.api")

REQUEST_ID_HEADER = "X-Request-Id"
MAX_INBOUND_REQUEST_ID = 64


class RequestContextMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: ASGIApp, container_provider):
        super().__init__(app)
        self._container_provider = container_provider

    async def dispatch(self, request: Request, call_next):
        inbound = request.headers.get(REQUEST_ID_HEADER, "")
        request_id = (
            inbound[:MAX_INBOUND_REQUEST_ID] if inbound.isalnum() else new_request_id()
        )
        token = request_id_var.set(request_id)
        started = time.perf_counter()
        status_code = 500
        try:
            response = await call_next(request)
            status_code = response.status_code
            response.headers[REQUEST_ID_HEADER] = request_id
            return response
        finally:
            duration_s = time.perf_counter() - started
            self._record(request, status_code, duration_s)
            logger.info(
                "request",
                extra={
                    "method": request.method,
                    "route": _route_of(request),
                    "path": request.url.path,
                    "status": status_code,
                    "duration_ms": round(duration_s * 1000, 2),
                },
            )
            request_id_var.reset(token)

    def _record(self, request: Request, status_code: int, duration_s: float) -> None:
        metrics = getattr(self._container_provider(), "metrics", None)
        if metrics is None:
            return
        labels = {"method": request.method, "route": _route_of(request)}
        metrics.increment(
            "signalforge_http_requests_total", {**labels, "status": str(status_code)}
        )
        metrics.observe("signalforge_http_request_duration_seconds", duration_s, labels)


def _route_of(request: Request) -> str:
    """The matched route template, so path parameters do not become labels."""
    route = request.scope.get("route")
    return getattr(route, "path", None) or "unmatched"
