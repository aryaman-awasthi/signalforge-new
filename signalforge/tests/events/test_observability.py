"""Platform self-observability (REQ-025): structured logs, a self /metrics
endpoint, a real /health, system.connector.health events, the dead-letter
operator surface and the agent-run trace tables.

The two policies here are written failing-test-first: how component health
aggregates into one verdict, and the rule that platform metrics carry no tenant
label - /metrics is unauthenticated, so a tenant label there would be a
cross-tenant disclosure.
"""

from __future__ import annotations

import json
import logging
from datetime import timezone
from io import StringIO

import pytest

from signalforge.contracts.canonical import ComponentHealth, HealthStatus
from signalforge.domain.observability.agent_runs import (
    AgentRun,
    AgentRunStatus,
    AgentRunStep,
)
from signalforge.domain.observability.health import (
    PLATFORM_TENANT_ID,
    overall_status,
    transitioned,
)
from signalforge.infrastructure.observability.logging import (
    JsonFormatter,
    configure_logging,
    request_id_var,
)
from signalforge.infrastructure.observability.metrics import (
    MetricType,
    MetricsRegistry,
    build_registry,
)
from signalforge.tests.helpers import FakeClock

pytestmark = pytest.mark.anyio


# --- health aggregation (pure policy) ----------------------------------------


@pytest.mark.req("REQ-025")
async def test_overall_status_takes_the_worst_component():
    up = ComponentHealth(component="db", status=HealthStatus.UP)
    degraded = ComponentHealth(component="bus", status=HealthStatus.DEGRADED)
    down = ComponentHealth(component="worker", status=HealthStatus.DOWN)
    unknown = ComponentHealth(component="adapter", status=HealthStatus.UNKNOWN)

    assert overall_status([up, up]) is HealthStatus.UP
    assert overall_status([up, degraded]) is HealthStatus.DEGRADED
    assert overall_status([up, degraded, down]) is HealthStatus.DOWN
    assert overall_status([up, unknown]) is HealthStatus.DEGRADED, (
        "not knowing is not the same as being fine"
    )
    assert overall_status([]) is HealthStatus.UNKNOWN


@pytest.mark.req("REQ-025")
async def test_only_transitions_are_worth_an_audit_entry():
    assert transitioned(None, HealthStatus.UP)
    assert transitioned(HealthStatus.UP, HealthStatus.DOWN)
    assert not transitioned(HealthStatus.UP, HealthStatus.UP)


# --- metrics -----------------------------------------------------------------


@pytest.mark.req("REQ-025")
async def test_platform_metrics_never_carry_a_tenant_label():
    """/metrics is scraped without a token, so a tenant label on any platform
    metric would publish who the customers are and how busy each one is."""
    registry = build_registry()
    forbidden = {"tenant", "tenant_id", "org", "incident", "incident_id", "user", "user_id"}
    for metric in registry.declared:
        assert not forbidden & set(metric.label_names), (
            f"{metric.name} exposes {forbidden & set(metric.label_names)}"
        )

    registry.increment("signalforge_events_published_total", {"topic": "app.thing.happened"})
    with pytest.raises(ValueError):
        registry.increment("signalforge_events_published_total", {"tenant_id": "tenant-a"})


@pytest.mark.req("REQ-025")
async def test_registry_renders_prometheus_text():
    registry = MetricsRegistry()
    registry.declare("sf_things_total", MetricType.COUNTER, "Things that happened", ("kind",))
    registry.declare("sf_queue_depth", MetricType.GAUGE, "Queued items")
    registry.declare(
        "sf_latency_seconds", MetricType.HISTOGRAM, "Latency", buckets=(0.1, 1.0)
    )

    registry.increment("sf_things_total", {"kind": "good"})
    registry.increment("sf_things_total", {"kind": "good"}, value=2)
    registry.increment("sf_things_total", {"kind": "bad"})
    registry.set_gauge("sf_queue_depth", 7)
    for value in (0.05, 0.5, 5.0):
        registry.observe("sf_latency_seconds", value)

    text = registry.render()
    assert "# HELP sf_things_total Things that happened" in text
    assert "# TYPE sf_things_total counter" in text
    assert 'sf_things_total{kind="good"} 3' in text
    assert 'sf_things_total{kind="bad"} 1' in text
    assert "sf_queue_depth 7" in text
    assert 'sf_latency_seconds_bucket{le="0.1"} 1' in text
    assert 'sf_latency_seconds_bucket{le="1.0"} 2' in text
    assert 'sf_latency_seconds_bucket{le="+Inf"} 3' in text
    assert "sf_latency_seconds_count 3" in text
    assert registry.value("sf_things_total", {"kind": "good"}) == 3

    with pytest.raises(KeyError):
        registry.increment("sf_never_declared")


@pytest.mark.req("REQ-025")
async def test_label_values_are_escaped():
    registry = MetricsRegistry()
    registry.declare("sf_things_total", MetricType.COUNTER, "Things", ("kind",))
    registry.increment("sf_things_total", {"kind": 'a"b\\c\nd'})
    assert 'sf_things_total{kind="a\\"b\\\\c\\nd"} 1' in registry.render()


# --- structured logs ---------------------------------------------------------


@pytest.mark.req("REQ-025")
async def test_logs_are_json_carry_context_and_redact_secrets():
    stream = StringIO()
    configure_logging(level="INFO", stream=stream)
    token = request_id_var.set("req-123")
    try:
        logging.getLogger("signalforge.test").info(
            "user logged in",
            extra={"tenant_id": "tenant-a", "password": "hunter2",
                   "authorization": "Bearer abc", "route": "/auth/login"},
        )
    finally:
        request_id_var.reset(token)

    line = json.loads(stream.getvalue().strip().splitlines()[-1])
    assert line["level"] == "INFO"
    assert line["message"] == "user logged in"
    assert line["logger"] == "signalforge.test"
    assert line["request_id"] == "req-123"
    assert line["tenant_id"] == "tenant-a"
    assert line["route"] == "/auth/login"
    assert line["password"] == "[redacted]"
    assert line["authorization"] == "[redacted]"
    assert "hunter2" not in stream.getvalue()
    assert line["ts"].endswith("+00:00") or line["ts"].endswith("Z")


@pytest.mark.req("REQ-025")
async def test_exceptions_are_logged_with_their_type():
    stream = StringIO()
    configure_logging(level="INFO", stream=stream)
    try:
        raise ValueError("boom")
    except ValueError:
        logging.getLogger("signalforge.test").exception("handler failed")

    line = json.loads(stream.getvalue().strip().splitlines()[-1])
    assert line["error"]["type"] == "ValueError" and line["error"]["message"] == "boom"
    assert "Traceback" in line["error"]["stack"]


@pytest.mark.req("REQ-025")
async def test_json_formatter_survives_unserialisable_values():
    formatter = JsonFormatter()
    record = logging.LogRecord(
        "x", logging.INFO, "f", 1, "msg", None, None
    )
    record.thing = object()
    assert isinstance(json.loads(formatter.format(record))["thing"], str)


# --- connector health --------------------------------------------------------


@pytest.mark.req("REQ-025")
async def test_connector_health_publishes_events_and_audits_transitions(bus):
    from signalforge.application.observability.health import ConnectorHealthService
    from signalforge.tests.events.helpers_observability import FlippingHealthSource

    source = FlippingHealthSource("adapter-1", HealthStatus.UP)
    service = ConnectorHealthService(bus.uow_factory, bus.bus, clock=bus.wall_clock)
    service.register(source)

    first = await service.report()
    assert first.status is HealthStatus.UP
    assert [c.component for c in first.components] == ["adapter-1"]

    source.status = HealthStatus.DOWN
    second = await service.report()
    assert second.status is HealthStatus.DOWN

    async with bus.uow_factory() as uow:
        events = await uow.events.read(tenant_id=PLATFORM_TENANT_ID, limit=50)
    health_events = [e for e in events if e.topic == "system.connector.health"]
    assert len(health_events) == 2
    assert health_events[0].payload["status"] == "up"
    assert health_events[1].payload["status"] == "down"
    assert health_events[0].source.system == "adapter-1"

    import sqlite3

    conn = sqlite3.connect(bus.db_path)
    try:
        rows = conn.execute(
            "select topic, payload from audit_log where tenant_id = ?",
            (PLATFORM_TENANT_ID,),
        ).fetchall()
    finally:
        conn.close()
    assert len(rows) == 2, "one audit entry per transition, not per poll"


@pytest.mark.req("REQ-025")
async def test_a_health_source_that_raises_reports_down_not_a_crash(bus):
    from signalforge.application.observability.health import ConnectorHealthService
    from signalforge.tests.events.helpers_observability import ExplodingHealthSource

    service = ConnectorHealthService(bus.uow_factory, bus.bus, clock=bus.wall_clock)
    service.register(ExplodingHealthSource("adapter-boom"))

    report = await service.report()
    assert report.status is HealthStatus.DOWN
    assert "RuntimeError" in report.components[0].detail


# --- agent-run traces (tables exist and work; empty until S5) ----------------


@pytest.mark.req("REQ-025")
async def test_agent_run_traces_record_a_run_with_steps(bus):
    clock = FakeClock()
    run = AgentRun(
        id="run-1",
        tenant_id="tenant-a",
        agent="hypothesis",
        status=AgentRunStatus.STARTED,
        started_at=clock.now(),
        incident_id="inc-1",
        trigger_topic="incident.declared",
        model="gpt-4.1",
    )
    async with bus.uow_factory() as uow:
        await uow.agent_runs.start(run)
        await uow.agent_runs.add_step(
            AgentRunStep(
                id="step-1", run_id="run-1", seq=1, step="gather-evidence",
                status="succeeded", started_at=clock.now(), tokens=120,
            )
        )
        await uow.agent_runs.finish(
            run.completed(status=AgentRunStatus.SUCCEEDED, at=clock.now(),
                          prompt_tokens=100, completion_tokens=20)
        )
        await uow.commit()

    async with bus.uow_factory() as uow:
        stored = await uow.agent_runs.get("tenant-a", "run-1")
        steps = await uow.agent_runs.steps("tenant-a", "run-1")
        listed = await uow.agent_runs.list("tenant-a", incident_id="inc-1")
        other_tenant = await uow.agent_runs.list("tenant-b")

    assert stored.status is AgentRunStatus.SUCCEEDED
    assert stored.prompt_tokens == 100 and stored.completion_tokens == 20
    assert stored.finished_at is not None
    assert [s.step for s in steps] == ["gather-evidence"]
    assert [r.id for r in listed] == ["run-1"]
    assert other_tenant == [], "agent traces are tenant-scoped like everything else"


@pytest.mark.req("REQ-025")
async def test_agent_run_tables_start_empty(bus):
    async with bus.uow_factory() as uow:
        assert await uow.agent_runs.list("tenant-a") == []
