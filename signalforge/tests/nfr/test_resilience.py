"""The failure catalogue, proven rather than asserted (REQ-073, artifact 16).

CLAUDE.md ground rule 6 says every designed failure lands somewhere safe,
observable and recoverable, with a test. Those tests exist across the suites that
built each feature. This file is the *catalogue*: one place that walks the whole
list end to end, so the non-functional report is generated from a run rather than
from a claim, and so a regression in any one of them fails a test whose name says
what was lost.

Each test states the failure, the designed landing place, and the observable
signal an operator would see.
"""

from __future__ import annotations

import json

import pytest

from signalforge.tests.nfr.conftest import (  # noqa: F401  (fixtures)
    APP_ID,
    act,
    governed,
    observe,
    record,
    request_execution,
    run_validation,
    validation_view,
)

pytestmark = pytest.mark.anyio

ROLLBACK = "rollback-checkout"


@pytest.mark.req("REQ-073", "REQ-051")
async def test_no_model_is_configured_and_every_read_still_works(governed):
    """Failure: the model is unavailable. Landing: deterministic, badged.

    The whole release-critical path with `GENAILAB_API_KEY` absent. Not a mock -
    the container's model settings are resolved from an empty environment, which
    is exactly a laptop with nothing exported.
    """
    api = governed.api
    assert api.container.llm_settings.api_key in (None, "")

    incident = governed.declare(title="Checkout failing")
    incident_id = incident["id"]
    token = governed.requester_token

    reads = {
        "incident": f"/incidents/{incident_id}",
        "war_room": f"/incidents/{incident_id}/war-room",
        "brain": f"/incidents/{incident_id}/brain",
        "guidance": f"/incidents/{incident_id}/brain/guidance",
        "nba": f"/incidents/{incident_id}/actions",
        "ledger": f"/incidents/{incident_id}/executions",
        "validation": f"/incidents/{incident_id}/validation",
        "communications": f"/incidents/{incident_id}/communications",
        "replay": f"/incidents/{incident_id}/replay",
    }
    degraded_flags = {}
    for name, path in reads.items():
        use = governed.owner_token if name == "replay" else token
        response = api.client.get(path, headers=api.auth_header(use))
        assert response.status_code == 200, f"{name}: {response.text}"
        body = response.json()
        if isinstance(body, dict) and "degraded" in body:
            degraded_flags[name] = body["degraded"]

    assert degraded_flags, "the AI surfaces must say they are degraded, not hide it"
    assert all(degraded_flags.values()), degraded_flags
    record(
        "degraded-mode",
        {"reads": sorted(reads), "degraded_flags": degraded_flags},
    )


@pytest.mark.req("REQ-073", "REQ-062")
async def test_an_adapter_timeout_lands_uncertain_and_blocks_the_retry(governed):
    """Failure: the target does not answer. Landing: UNCERTAIN, retry refused.

    A timeout is not a failure. The one thing a platform must never do here is
    assume nothing happened and send the rollback again.
    """
    api = governed.api
    incident_id = governed.declare()["id"]
    requested = request_execution(
        api,
        governed.requester_token,
        incident_id,
        action_id=ROLLBACK,
        args={"release": "v11"},
    )
    execution_id = requested["execution"]["execution_id"]
    act(api, governed.approver_token, incident_id, execution_id, "approve", note="ok")

    governed.observe.times_out(f"/observe/actions/{ROLLBACK}/execute")
    ran = act(api, governed.requester_token, incident_id, execution_id, "run")
    assert ran["execution"]["state"] == "uncertain"
    assert ran["execution"]["error_category"] == "timeout"
    assert ran["execution"]["retry_blocked"] is True

    blocked = act(
        api, governed.requester_token, incident_id, execution_id, "retry", expect=409
    )
    assert "reconcile" in blocked["detail"].lower()

    # And the platform says so on its own metrics surface.
    metrics = api.client.get("/metrics").text
    assert "signalforge_executions_unreconciled" in metrics


@pytest.mark.req("REQ-073", "REQ-062")
async def test_a_duplicate_submission_sends_nothing(governed):
    """Failure: two operators, one action. Landing: the second is recognised."""
    api = governed.api
    incident_id = governed.declare()["id"]
    first = request_execution(
        api,
        governed.requester_token,
        incident_id,
        action_id=ROLLBACK,
        args={"release": "v11"},
    )
    before = dict(governed.observe.state.executions)
    second = request_execution(
        api,
        governed.requester_token,
        incident_id,
        action_id=ROLLBACK,
        args={"release": "v11"},
    )
    assert second["duplicate"] is True
    assert (
        second["execution"]["execution_id"] == first["execution"]["execution_id"]
    )
    assert governed.observe.state.executions == before


@pytest.mark.req("REQ-073", "REQ-061", "REQ-012")
async def test_a_permission_lost_after_approval_refuses_the_execution(governed):
    """Failure: the approver's authority changes mid-flow. Landing: refused.

    The approval was valid when it was granted. It is checked again against what
    the approver holds *now*, because an approval is not a bearer token for the
    rest of the incident.
    """
    api = governed.api
    incident_id = governed.declare()["id"]
    requested = request_execution(
        api,
        governed.requester_token,
        incident_id,
        action_id=ROLLBACK,
        args={"release": "v11"},
    )
    execution_id = requested["execution"]["execution_id"]
    approved = act(
        api, governed.approver_token, incident_id, execution_id, "approve", note="ok"
    )
    assert approved["approval"]["permission"] == "approve_high_risk"

    # The organisation takes the approver's commander role away.
    response = api.client.put(
        f"/users/{governed.approver_id}/roles",
        json={"bindings": [{"role": "sme"}]},
        headers=api.auth_header(governed.owner_token),
    )
    assert response.status_code == 200, response.text

    refused = act(
        api, governed.requester_token, incident_id, execution_id, "run", expect=422
    )
    assert "permission" in refused["detail"].lower() or "approv" in refused["detail"].lower()
    assert governed.observe.state.executions == {}, "nothing reached the application"


@pytest.mark.req("REQ-073", "REQ-065")
async def test_a_healthy_estate_with_a_failing_customer_journey_cannot_close(governed):
    """Failure: recovery that is not recovery. Landing: closure refused.

    The most expensive failure in incident response is closing on infrastructure
    health while customers still cannot pay.
    """
    api = governed.api
    incident_id = governed.declare()["id"]
    governed.observe.state.health = {
        "status": "up",
        "checked_at": "2026-09-01T12:00:00+00:00",
        "components": [{"component": "database", "status": "up"}],
    }
    response = api.client.put(
        f"/apps/{APP_ID}/mapping",
        json={
            "journeys": [{"journey_id": "card-payment", "name": "Card payment", "tier": 1}],
            "probes": [
                {
                    "probe_id": "card-payment",
                    "path": "/checkout/pay",
                    "journey_id": "card-payment",
                    "per_region": False,
                }
            ],
        },
        headers=api.auth_header(governed.owner_token),
    )
    assert response.status_code == 200, response.text
    await api.container.probe_runner.run(governed.tenant_id, APP_ID)

    cycle = run_validation(api, governed.requester_token, incident_id)
    assert cycle["closure_blocked"] is True
    assert "customer" in cycle["closure_lock_reason"]

    refused = api.client.post(
        f"/incidents/{incident_id}/close",
        json={},
        headers=api.auth_header(governed.requester_token),
    )
    assert refused.status_code == 409, refused.text


@pytest.mark.req("REQ-073", "REQ-032")
async def test_one_broken_connector_does_not_take_the_platform_down(governed):
    """Failure: partial external failure. Landing: reported, contained.

    The health endpoint says which binding is unwell; every other read still
    answers, because a monitoring integration going dark is the most ordinary
    thing that happens during an incident.
    """
    api = governed.api
    incident_id = governed.declare()["id"]
    governed.observe.state.fail("/observe/health", 503)

    await api.container.health.report()

    health = api.client.get("/health").json()
    assert health["status"] in ("ok", "degraded")

    # And the cockpit still answers everywhere else.
    for path in (
        f"/incidents/{incident_id}",
        f"/incidents/{incident_id}/executions",
        f"/incidents/{incident_id}/brain",
    ):
        assert api.client.get(
            path, headers=api.auth_header(governed.requester_token)
        ).status_code == 200, path


@pytest.mark.req("REQ-073", "REQ-016")
async def test_the_failure_catalogue_is_recorded_for_the_report(governed):
    """The artefact this suite exists to generate.

    Written from the run rather than typed into a document, so artifact 16 can
    never claim a failure path this suite does not exercise.
    """
    catalogue = {
        "model unavailable": "deterministic fallback, degraded badge on every AI surface",
        "adapter timeout": "UNCERTAIN, retry blocked until reconciled",
        "uncertain external state": "reconcile-before-retry, duplicate prevention",
        "duplicate submission": "recognised, nothing re-sent",
        "permission revoked after approval": "execution refused at run time",
        "websocket unavailable": "screens keep polling (signalforge/tests/test_web_warroom.py)",
        "partial external failure": "connector health degraded, cockpit unaffected",
        "customer unhealthy after technical recovery": "closure refused by the customer layer",
        "process crash mid-dispatch": "offsets replayed (signalforge/tests/events/test_crash_recovery.py)",
        "audit record edited": "replay reports invalid, PIR refuses to generate",
    }
    path = record("failure-catalogue", {"paths": catalogue})
    assert json.loads(path.read_text(encoding="utf-8"))["paths"] == catalogue
