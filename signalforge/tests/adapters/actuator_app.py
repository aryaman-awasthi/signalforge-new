"""A Spring Boot application as Actuator exposes it.

A test double for *an application nobody changed*, which is the claim the
actuator family makes: a Boot service with Micrometer on its classpath already
serves these two endpoints, and binding them is the whole integration. So the
exposition text below is shaped the way Micrometer really writes it - the
`_total` suffix on a counter, a timer's `_count`/`_sum`/`_max` trio rather than
one metric, the trailing comma Micrometer leaves inside the label braces, Java's
scientific notation on a large gauge - because a parser that only handles tidy
text passes here and fails against the first real application.

The health document is the shape Actuator returns with
`management.endpoint.health.show-details` enabled, including a status this
platform has never heard of, which is the normal case: Spring's status is an
open set and an application may register its own.

The adapters reach this over an ASGI transport through the shared ObserveHarness,
so the request line, the status handling and the size limit are all exercised for
real. A *timeout* is staged on the harness rather than by sleeping in a handler
here: an in-process ASGI transport has no socket to time out on, so a sleeping
handler would test the test rather than the adapter.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Optional

from starlette.applications import Starlette
from starlette.responses import JSONResponse, PlainTextResponse, Response
from starlette.routing import Route

ACTUATOR_METRICS_CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"

DEFAULT_PROMETHEUS = """# HELP orders_placed_total Orders accepted by the checkout endpoint
# TYPE orders_placed_total counter
orders_placed_total{application="checkout",region="region-a",status="200",} 1240.0
orders_placed_total{application="checkout",region="region-a",status="500",} 17.0
orders_placed_total{application="checkout",region="region-b",status="200",} 860.0
# HELP http_server_requests_seconds
# TYPE http_server_requests_seconds summary
http_server_requests_seconds_count{region="region-a",status="200",uri="/orders",} 1240.0
http_server_requests_seconds_sum{region="region-a",status="200",uri="/orders",} 96.72
http_server_requests_seconds_count{region="region-a",status="500",uri="/orders",} 17.0
http_server_requests_seconds_sum{region="region-a",status="500",uri="/orders",} 4.291
http_server_requests_seconds_count{region="region-b",status="200",uri="/orders",} 860.0
http_server_requests_seconds_sum{region="region-b",status="200",uri="/orders",} 71.104
# HELP http_server_requests_seconds_max
# TYPE http_server_requests_seconds_max gauge
http_server_requests_seconds_max{region="region-a",status="500",uri="/orders",} 3.104
# HELP jvm_memory_used_bytes The amount of used memory
# TYPE jvm_memory_used_bytes gauge
jvm_memory_used_bytes{area="heap",id="G1 Eden Space",} 5.1380224E8
"""

DEFAULT_HEALTH = {
    "status": "UP",
    "components": {
        "db": {
            "status": "UP",
            "details": {"database": "PostgreSQL", "validationQuery": "isValid()"},
        },
        "diskSpace": {
            "status": "DOWN",
            "details": {
                "total": 499963174912,
                "free": 1073741824,
                "threshold": 10485760,
                "path": "/var/lib/app",
                "exists": True,
            },
        },
        "mailSender": {
            # An indicator that puts a credential in its details. Applications
            # do this, which is why the manifest declares a redaction policy.
            "status": "OUT_OF_SERVICE",
            "details": {
                "location": "smtp.internal:587",
                "user": "alerts@example.com",
                "password": "hunter2",
            },
        },
        "rateLimiter": {
            # A status Spring itself never defines. An application may register
            # one, and this platform must not guess what it meant.
            "status": "RESTRICTED",
            "details": {"permitsRemaining": 0},
        },
    },
}

HEALTH_WITHOUT_DETAILS = {"status": "UP"}
"""What Actuator returns when show-details is off - which is the default. It
carries no components at all, and a health adapter that needed them would report
every default-configured Boot application as broken."""


@dataclass
class ActuatorState:
    prometheus_text: str = DEFAULT_PROMETHEUS
    health: dict = field(default_factory=lambda: deepcopy(DEFAULT_HEALTH))
    show_details: bool = True
    status_overrides: dict[str, int] = field(default_factory=dict)
    calls: list[str] = field(default_factory=list)

    def hide_details(self) -> None:
        """Turn show-details off, as a default Boot deployment has it."""
        self.show_details = False

    def fail(self, path: str, status: int) -> None:
        self.status_overrides[path] = status

    def count(self, path: str) -> int:
        return sum(1 for seen in self.calls if seen == path)


def build_actuator_app(
    state: Optional[ActuatorState] = None,
) -> tuple[Starlette, ActuatorState]:
    state = state or ActuatorState()

    async def intercept(request) -> Optional[Response]:
        state.calls.append(request.url.path)
        override = state.status_overrides.get(request.url.path)
        if override is not None:
            return JSONResponse({"status": "DOWN"}, status_code=override)
        return None

    async def prometheus(request):
        refused = await intercept(request)
        if refused:
            return refused
        return PlainTextResponse(
            state.prometheus_text, media_type=ACTUATOR_METRICS_CONTENT_TYPE
        )

    async def health(request):
        refused = await intercept(request)
        if refused:
            return refused
        if not state.show_details:
            return JSONResponse(dict(HEALTH_WITHOUT_DETAILS))
        return JSONResponse(state.health)

    async def orders(request):
        # A public entry point, so a synthetic probe has a journey to exercise
        # rather than only the management endpoints.
        refused = await intercept(request)
        if refused:
            return refused
        return JSONResponse({"orders": []})

    app = Starlette(
        routes=[
            Route("/actuator/prometheus", prometheus),
            Route("/actuator/health", health),
            Route("/orders", orders),
        ]
    )
    return app, state
