"""A minimal application that serves the frozen observe contract.

This is a *test double for an onboarded application*, not a fixture for the
adapters: the http-observe suite talks to it over real HTTP semantics through an
ASGI transport, so what is exercised is the adapter's request building, auth
header, status handling, size limit and translation - not a mocked-out method.

It deliberately implements only what `contracts/observe.py` freezes, because the
claim under test is that serving that contract is sufficient to be onboarded.
The reference harness (S3-T06) is the same shape at full size.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from starlette.applications import Starlette
from starlette.responses import JSONResponse, PlainTextResponse, Response
from starlette.routing import Route

from signalforge.contracts.observe import OBSERVE_METRICS_CONTENT_TYPE

NOW = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)

DEFAULT_METRICS = """# HELP http_requests_total Requests served
# TYPE http_requests_total counter
http_requests_total{route="/checkout",region="eu-west-1"} 1200
http_requests_total{route="/checkout",region="us-east-1"} 800
http_requests_total{route="/health",region="eu-west-1"} 45
# TYPE checkout_success_ratio gauge
checkout_success_ratio{region="eu-west-1"} 0.987
checkout_success_ratio{region="us-east-1"} 0.5
saturation_ratio 0.42
"""

DEFAULT_LOGS = [
    {
        "ts": "2026-09-01T11:59:00+00:00",
        "level": "error",
        "message": "payment declined for ada@example.com card 4111 1111 1111 1111",
        "service": "checkout",
        "logger": "billing",
        "trace_id": "trace-1",
        "attributes": {"region": "eu-west-1", "customer": "ada@example.com"},
    },
    {
        "ts": "2026-09-01T11:59:10+00:00",
        "level": "error",
        "message": "upstream timeout",
        "service": "checkout",
        "attributes": {"region": "eu-west-1"},
    },
    {
        "ts": "2026-09-01T11:59:20+00:00",
        "level": "warning",
        "message": "retrying",
        "service": "cart",
        "attributes": {"region": "us-east-1"},
    },
    {
        "ts": "2026-09-01T11:59:30+00:00",
        "level": "info",
        "message": "cache warmed",
        "service": "cart",
        "attributes": {"region": "us-east-1"},
    },
]

DEFAULT_SPANS = [
    {
        "trace_id": "trace-1",
        "span_id": "span-1",
        "service": "checkout",
        "operation": "POST /checkout",
        "start": "2026-09-01T11:59:00+00:00",
        "end": "2026-09-01T11:59:01+00:00",
        "status": "error",
        "attributes": {"user.email": "ada@example.com"},
    },
    {
        "trace_id": "trace-1",
        "span_id": "span-2",
        "parent_span_id": "span-1",
        "service": "billing",
        "operation": "charge",
        "start": "2026-09-01T11:59:00+00:00",
        "end": "2026-09-01T11:59:01+00:00",
        "status": "ok",
        "attributes": {},
    },
]

DEFAULT_HEALTH = {
    "status": "degraded",
    "checked_at": "2026-09-01T12:00:00+00:00",
    "components": [
        {"component": "database", "status": "up"},
        {"component": "payment-gateway", "status": "degraded", "detail": "p99 3.1s"},
    ],
}

DEFAULT_TOPOLOGY = {
    "version": "2026-09-01.1",
    "captured_at": "2026-09-01T12:00:00+00:00",
    "nodes": [
        {"node_id": "checkout", "name": "Checkout", "node_type": "service",
         "region": "eu-west-1"},
        {"node_id": "billing", "name": "Billing", "node_type": "service",
         "region": "eu-west-1"},
        {"node_id": "ledger-db", "name": "Ledger", "node_type": "database"},
    ],
    "edges": [
        {"source": "checkout", "target": "billing", "edge_type": "calls"},
        {"source": "billing", "target": "ledger-db", "edge_type": "depends_on"},
    ],
}

DEFAULT_ACTIONS = {
    "schema_version": 1,
    "actions": [
        {
            "action_id": "restart-billing",
            "name": "Restart billing",
            "description": "Rolling restart of the billing pods.",
            "action_class": "restart",
            "risk": "high",
            "approver_roles": ["incident_commander"],
            "timeout_s": 120,
            "idempotent": False,
            "verification": ["billing_health_up"],
        },
        {
            "action_id": "collect-diagnostics",
            "name": "Collect diagnostics",
            "description": "Dump a heap profile and thread stacks.",
            "action_class": "diagnostic",
            "risk": "low",
            "idempotent": True,
        },
    ],
}

DEFAULT_CHAOS = {
    "schema_version": 1,
    "scenarios": [
        {
            "scenario_id": "billing-latency",
            "name": "Billing latency",
            "description": "Adds latency to billing calls.",
            "fault_type": "latency",
            "target": "billing",
            "max_duration_s": 300,
            "blast_radius": "billing service only",
            "abort_conditions": ["error_rate > 5%"],
            "simulated": True,
        }
    ],
}


@dataclass
class ObserveState:
    token: str = "app-token"
    require_auth: bool = True
    metrics_text: str = DEFAULT_METRICS
    logs: list[dict] = field(default_factory=lambda: [dict(r) for r in DEFAULT_LOGS])
    spans: list[dict] = field(default_factory=lambda: [dict(s) for s in DEFAULT_SPANS])
    health: dict = field(default_factory=lambda: dict(DEFAULT_HEALTH))
    topology: dict = field(default_factory=lambda: dict(DEFAULT_TOPOLOGY))
    actions: dict = field(default_factory=lambda: dict(DEFAULT_ACTIONS))
    chaos: dict = field(default_factory=lambda: dict(DEFAULT_CHAOS))
    executions: dict[str, dict] = field(default_factory=dict)
    injections: dict[str, dict] = field(default_factory=dict)
    status_overrides: dict[str, int] = field(default_factory=dict)
    calls: list[str] = field(default_factory=list)

    def fail(self, path: str, status: int) -> None:
        self.status_overrides[path] = status

    def count(self, path: str) -> int:
        return sum(1 for seen in self.calls if seen == path)


def build_observe_app(state: Optional[ObserveState] = None) -> tuple[Starlette, ObserveState]:
    state = state or ObserveState()

    async def intercept(request) -> Optional[Response]:
        """Record the call, then apply whatever the test asked this path to do."""
        state.calls.append(request.url.path)
        override = state.status_overrides.get(request.url.path)
        if override is not None:
            return JSONResponse({"detail": "forced"}, status_code=override)
        return None

    async def guard(request) -> Optional[Response]:
        if state.require_auth:
            header = request.headers.get("authorization", "")
            if header != f"Bearer {state.token}":
                state.calls.append(request.url.path)
                return JSONResponse({"detail": "unauthorized"}, status_code=401)
        return await intercept(request)

    async def metrics(request):
        refused = await guard(request)
        if refused:
            return refused
        return PlainTextResponse(
            state.metrics_text, media_type=OBSERVE_METRICS_CONTENT_TYPE
        )

    async def logs(request):
        refused = await guard(request)
        if refused:
            return refused
        query = request.query_params.get("query", "")
        limit = int(request.query_params.get("limit", 100))
        records = [
            record
            for record in state.logs
            if not query or query.lower() in _searchable(record)
        ]
        return JSONResponse({"records": records[:limit], "total": len(records)})

    async def traces(request):
        refused = await guard(request)
        if refused:
            return refused
        trace_id = request.query_params.get("trace_id")
        spans = state.spans
        if trace_id:
            spans = [span for span in spans if span["trace_id"] == trace_id]
        service = request.query_params.get("service")
        if service:
            spans = [span for span in spans if span["service"] == service]
        if request.query_params.get("errors_only") == "true":
            spans = [span for span in spans if span.get("status") == "error"]
        limit = int(request.query_params.get("limit", 100))
        return JSONResponse({"spans": spans[:limit]})

    async def health(request):
        refused = await guard(request)
        if refused:
            return refused
        return JSONResponse(state.health)

    async def topology(request):
        refused = await guard(request)
        if refused:
            return refused
        return JSONResponse(state.topology)

    async def actions_manifest(request):
        refused = await guard(request)
        if refused:
            return refused
        return JSONResponse(state.actions)

    async def action_execute(request):
        refused = await guard(request)
        if refused:
            return refused
        body = await request.json()
        key = body["idempotency_key"]
        existing = state.executions.get(key)
        if existing is not None:
            return JSONResponse(existing)
        execution = {
            "execution_id": f"exec-{len(state.executions) + 1}",
            "action_id": request.path_params["action_id"],
            "idempotency_key": key,
            "state": "succeeded",
            "requested_at": NOW.isoformat(),
            "started_at": NOW.isoformat(),
            "finished_at": NOW.isoformat(),
            "output": {"params": body.get("params", {})},
        }
        state.executions[key] = execution
        return JSONResponse(execution)

    async def action_execution(request):
        refused = await guard(request)
        if refused:
            return refused
        execution = state.executions.get(request.path_params["idempotency_key"])
        if execution is None:
            return JSONResponse({"detail": "never applied"}, status_code=404)
        return JSONResponse(execution)

    async def chaos_manifest(request):
        refused = await guard(request)
        if refused:
            return refused
        return JSONResponse(state.chaos)

    async def chaos_inject(request):
        refused = await guard(request)
        if refused:
            return refused
        body = await request.json()
        injection = {
            "injection_id": f"inj-{len(state.injections) + 1}",
            "scenario_id": request.path_params["scenario_id"],
            "target": "billing",
            "params": body.get("params", {}),
            "state": "active",
            "scope": body["scope"],
            "started_at": NOW.isoformat(),
        }
        state.injections[injection["injection_id"]] = injection
        return JSONResponse(injection)

    async def chaos_status(request):
        refused = await guard(request)
        if refused:
            return refused
        injection = state.injections.get(request.path_params["injection_id"])
        if injection is None:
            return JSONResponse({"detail": "unknown"}, status_code=404)
        return JSONResponse(injection)

    async def chaos_abort(request):
        refused = await guard(request)
        if refused:
            return refused
        injection = state.injections.get(request.path_params["injection_id"])
        if injection is None:
            return JSONResponse({"detail": "unknown"}, status_code=404)
        injection["state"] = "aborted"
        injection["ended_at"] = NOW.isoformat()
        return JSONResponse(injection)

    async def probe_target(request):
        # A synthetic probe exercises a public entry point, so this one is not
        # behind the credential - only behind whatever the test staged.
        refused = await intercept(request)
        if refused:
            return refused
        return JSONResponse({"ok": True})

    app = Starlette(
        routes=[
            Route("/observe/metrics", metrics),
            Route("/observe/logs", logs),
            Route("/observe/traces", traces),
            Route("/observe/health", health),
            Route("/observe/topology", topology),
            Route("/observe/actions", actions_manifest),
            Route("/observe/actions/{action_id}/execute", action_execute, methods=["POST"]),
            Route("/observe/actions/executions/{idempotency_key}", action_execution),
            Route("/observe/chaos", chaos_manifest),
            Route("/observe/chaos/{scenario_id}/inject", chaos_inject, methods=["POST"]),
            Route("/observe/chaos/injections/{injection_id}", chaos_status),
            Route(
                "/observe/chaos/injections/{injection_id}/abort",
                chaos_abort,
                methods=["POST"],
            ),
            Route("/checkout", probe_target),
        ]
    )
    return app, state


@dataclass
class PrometheusState:
    """A Prometheus server's two query endpoints, enough to prove the
    translation into canonical series and the error envelope."""

    series: list[dict] = field(
        default_factory=lambda: [
            {
                "metric": {"__name__": "http_requests_total", "region": "eu-west-1"},
                "values": [[1756728000, "1200"], [1756728015, "1260"]],
            },
            {
                "metric": {"__name__": "http_requests_total", "region": "us-east-1"},
                "values": [[1756728000, "800"], [1756728015, "NaN"]],
            },
        ]
    )
    error: Optional[str] = None
    error_status: int = 400
    """Prometheus answers a bad query with 400 and the error envelope; some
    API-compatible servers answer 200 with the same envelope. Both reach the
    caller as an AdapterError naming the reason, and both are exercised."""
    last_params: dict = field(default_factory=dict)


def build_prometheus_app(
    state: Optional[PrometheusState] = None,
) -> tuple[Starlette, PrometheusState]:
    state = state or PrometheusState()

    def envelope(result_type: str, result: list[dict]) -> dict:
        return {"status": "success", "data": {"resultType": result_type, "result": result}}

    def refused_empty_expression():
        """What the real server answers when `query` carries no expression.

        A fake that accepts anything is not a fake of Prometheus, and this one
        used to: it answered a scrape for an empty `query`, which is the one
        thing the real API will not do. That is what let an adapter that could
        never scrape pass its own suite.
        """
        return JSONResponse(
            {
                "status": "error",
                "errorType": "bad_data",
                "error": 'invalid parameter "query": unknown position: parse error: '
                "no expression found in input",
            },
            status_code=400,
        )

    async def query(request):
        state.last_params = dict(request.query_params)
        if not (request.query_params.get("query") or "").strip():
            return refused_empty_expression()
        if state.error:
            return JSONResponse(
                {"status": "error", "errorType": "bad_data", "error": state.error},
                status_code=state.error_status,
            )
        vector = [
            {"metric": one["metric"], "value": one["values"][-1]} for one in state.series
        ]
        return JSONResponse(envelope("vector", vector))

    async def query_range(request):
        state.last_params = dict(request.query_params)
        if not (request.query_params.get("query") or "").strip():
            return refused_empty_expression()
        if state.error:
            return JSONResponse(
                {"status": "error", "errorType": "bad_data", "error": state.error},
                status_code=state.error_status,
            )
        return JSONResponse(envelope("matrix", state.series))

    async def healthy(request):
        """`/-/healthy` - the cheap unauthenticated probe discovery uses.

        The real server serves it, `LOCAL_SERVICES` names it, and nothing about
        it involves a query. It is here so a discovery test can probe what
        production probes instead of borrowing a query endpoint.
        """
        return PlainTextResponse("Prometheus Server is Healthy.")

    app = Starlette(
        routes=[
            Route("/-/healthy", healthy),
            Route("/api/v1/query", query),
            Route("/api/v1/query_range", query_range),
        ]
    )
    return app, state


def _searchable(record: dict[str, Any]) -> str:
    return " ".join(
        [
            str(record.get("message", "")),
            str(record.get("service", "")),
            str(record.get("level", "")),
        ]
    ).lower()
