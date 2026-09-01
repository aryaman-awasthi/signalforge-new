"""The platform observability HTTP surface: /health, /metrics, /platform/status,
the dead-letter operator pane and agent-run traces (REQ-025, REQ-012, REQ-013)."""

from __future__ import annotations

import pytest

from signalforge.contracts.observe import OBSERVE_METRICS_CONTENT_TYPE

pytestmark = pytest.mark.anyio


@pytest.mark.req("REQ-025")
async def test_health_reports_components_without_a_session(api):
    response = api.client.get("/health")
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["status"] == "ok"
    assert body["service"] == "signalforge-api"
    components = {c["component"]: c for c in body["components"]}
    assert set(components) == {"database", "event-bus"}, (
        "the worker group is disabled in tests, so this node does not claim one"
    )
    assert components["database"]["status"] == "up"
    assert components["event-bus"]["detail"].startswith("lag ")


@pytest.mark.req("REQ-025")
async def test_health_degrades_when_a_component_is_down(api):
    from signalforge.contracts.canonical import ComponentHealth, HealthStatus
    from signalforge.domain.observability.health import HealthSource

    class BrokenSource(HealthSource):
        source_id = "adapter-x"

        async def check(self):
            return ComponentHealth(component="adapter-x", status=HealthStatus.DOWN)

    api.container.health.register(BrokenSource())
    response = api.client.get("/health")
    assert response.status_code == 503
    assert response.json()["status"] == "down"


@pytest.mark.req("REQ-025")
async def test_metrics_is_prometheus_text_and_names_no_tenant(api):
    owner = api.signup()
    api.client.get("/health")

    response = api.client.get("/metrics")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert OBSERVE_METRICS_CONTENT_TYPE.split(";")[0] in response.headers["content-type"]

    body = response.text
    assert "# TYPE signalforge_http_requests_total counter" in body
    assert 'signalforge_http_requests_total{method="GET",route="/health",status="200"}' in body
    assert "signalforge_http_request_duration_seconds_bucket" in body
    assert "signalforge_event_seq" in body
    assert "signalforge_dead_letters{" in body

    assert owner["org"]["id"] not in body, "no tenant id may reach an unauthenticated scrape"
    assert "owner@acme.io" not in body


@pytest.mark.req("REQ-025")
async def test_request_ids_are_returned_and_echoed(api):
    fresh = api.client.get("/health")
    assert fresh.headers["X-Request-Id"]

    echoed = api.client.get("/health", headers={"X-Request-Id": "abc123"})
    assert echoed.headers["X-Request-Id"] == "abc123"

    hostile = api.client.get("/health", headers={"X-Request-Id": "a b\nc: d"})
    assert hostile.headers["X-Request-Id"] != "a b\nc: d", (
        "an inbound id is only echoed when it is safe to put in a header"
    )


@pytest.mark.req("REQ-025", "REQ-012")
async def test_platform_status_is_operator_work(api):
    owner = api.signup()
    auditor = api.invite_and_accept(
        owner["tokens"]["access_token"], email="auditor@acme.io", roles=["auditor"]
    )

    denied = api.client.get(
        "/platform/status", headers=api.auth_header(auditor["tokens"]["access_token"])
    )
    assert denied.status_code == 403
    assert denied.json()["permission"] == "manage_apps"

    allowed = api.client.get(
        "/platform/status", headers=api.auth_header(owner["tokens"]["access_token"])
    )
    assert allowed.status_code == 200, allowed.text
    body = allowed.json()
    assert body["clock"]["source"] in ("wall", "virtual")
    assert body["bus"]["head_seq"] >= 0
    # The S2 backbone jobs, plus whatever later sprints scheduled beside them.
    # Asserted as a subset with the detection loops named explicitly (S4-T01),
    # so a job silently disappearing still fails while adding one does not.
    jobs = {j["name"] for j in body["worker"]["jobs"]}
    assert {"dispatcher", "connector-health", "retention"} <= jobs
    assert {"metric-rollups", "detection", "probes", "rollup-retention"} <= jobs
    assert body["retention"] == []


@pytest.mark.req("REQ-022", "REQ-025", "REQ-012")
async def test_dead_letters_are_listed_and_requeued_over_http(api):
    owner = api.signup()
    tenant_id = owner["org"]["id"]
    token = owner["tokens"]["access_token"]
    container = api.container

    from signalforge.domain.events.dispatch import RetryPolicy
    from signalforge.tests.events.conftest import RecordingHandler, make_event

    handler = RecordingHandler(fail_topics=("app.poison.dropped",))
    container.dispatcher._retry = RetryPolicy(max_attempts=1, base_backoff_s=0.0,
                                              max_backoff_s=0.0)
    container.dispatcher.register("projector", handler)

    async with container.event_uow_factory() as uow:
        await container.bus.publish(
            uow, make_event(topic="app.poison.dropped", tenant_id=tenant_id)
        )
        await uow.commit()
    await container.dispatcher.run_once()

    listed = api.client.get("/platform/dead-letters", headers=api.auth_header(token))
    assert listed.status_code == 200, listed.text
    (row,) = [r for r in listed.json() if r["group"] == "projector"]
    assert row["state"] == "dead" and "poisoned handler" in row["error"]

    handler.fail_topics = ()
    requeued = api.client.post(
        f"/platform/dead-letters/{row['id']}/requeue", headers=api.auth_header(token)
    )
    assert requeued.status_code == 200
    assert requeued.json()["state"] == "requeued"

    report = await container.dispatcher.run_once()
    assert report.redelivered == 1

    resolved = api.client.get(
        "/platform/dead-letters?state=resolved", headers=api.auth_header(token)
    ).json()
    assert [r["id"] for r in resolved if r["group"] == "projector"] == [row["id"]]


@pytest.mark.req("REQ-013", "REQ-022")
async def test_dead_letters_of_another_tenant_are_invisible(api):
    owner = api.signup()
    other = api.signup(org="Other Inc", email="owner@other.io")
    container = api.container

    from signalforge.domain.events.dispatch import RetryPolicy
    from signalforge.tests.events.conftest import RecordingHandler, make_event

    container.dispatcher._retry = RetryPolicy(max_attempts=1, base_backoff_s=0.0,
                                              max_backoff_s=0.0)
    container.dispatcher.register(
        "projector", RecordingHandler(fail_topics=("app.poison.dropped",))
    )
    async with container.event_uow_factory() as uow:
        await container.bus.publish(
            uow, make_event(topic="app.poison.dropped", tenant_id=owner["org"]["id"])
        )
        await uow.commit()
    await container.dispatcher.run_once()

    theirs = api.client.get(
        "/platform/dead-letters",
        headers=api.auth_header(other["tokens"]["access_token"]),
    )
    assert theirs.status_code == 200 and theirs.json() == []

    mine = api.client.get(
        "/platform/dead-letters", headers=api.auth_header(owner["tokens"]["access_token"])
    ).json()
    (row,) = mine
    stolen = api.client.post(
        f"/platform/dead-letters/{row['id']}/requeue",
        headers=api.auth_header(other["tokens"]["access_token"]),
    )
    assert stolen.status_code == 404


@pytest.mark.req("REQ-025", "REQ-012")
async def test_agent_run_traces_are_readable_and_start_empty(api):
    owner = api.signup()
    token = owner["tokens"]["access_token"]

    empty = api.client.get("/platform/agent-runs", headers=api.auth_header(token))
    assert empty.status_code == 200 and empty.json() == []

    from signalforge.domain.observability.agent_runs import (
        AgentRun,
        AgentRunStatus,
        AgentRunStep,
    )

    now = api.clock.now()
    async with api.container.event_uow_factory() as uow:
        await uow.agent_runs.start(
            AgentRun(id="run-1", tenant_id=owner["org"]["id"], agent="telemetry",
                     status=AgentRunStatus.STARTED, started_at=now, incident_id="inc-1")
        )
        await uow.agent_runs.add_step(
            AgentRunStep(id="s1", run_id="run-1", seq=1, step="query",
                         status="succeeded", started_at=now, tokens=42)
        )
        await uow.commit()

    listed = api.client.get(
        "/platform/agent-runs?incident_id=inc-1", headers=api.auth_header(token)
    ).json()
    assert [r["agent"] for r in listed] == ["telemetry"]

    detail = api.client.get(
        "/platform/agent-runs/run-1", headers=api.auth_header(token)
    ).json()
    assert [s["step"] for s in detail["steps"]] == ["query"]
    assert detail["total_tokens"] == 0

    executive = api.invite_and_accept(token, email="exec@acme.io", roles=["executive"])
    denied = api.client.get(
        "/platform/agent-runs", headers=api.auth_header(executive["tokens"]["access_token"])
    )
    assert denied.status_code == 403


@pytest.mark.req("REQ-025", "REQ-021")
async def test_the_bus_metrics_consumer_is_registered_and_counts_traffic(api):
    owner = api.signup()
    container = api.container
    from signalforge.application.events.consumers import BUS_METRICS_GROUP
    from signalforge.tests.events.conftest import make_event

    assert BUS_METRICS_GROUP in container.dispatcher.groups

    async with container.event_uow_factory() as uow:
        await container.bus.publish(
            uow, make_event(topic="app.thing.happened", tenant_id=owner["org"]["id"])
        )
        await uow.commit()
    await container.dispatcher.run_once()

    assert container.metrics.value(
        "signalforge_events_consumed_total", {"topic": "app.thing.happened"}
    ) == 1
    assert container.metrics.value(
        "signalforge_events_published_total", {"topic": "app.thing.happened"}
    ) == 1
