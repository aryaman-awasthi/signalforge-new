"""The Tool Gateway, the ledger and uncertain outcomes (REQ-061, REQ-062).

Every test here drives the real API against a real onboarded application whose
actions were imported from its own manifest. The adapter reaches the application
over an ASGI transport, so the request line, the auth header and the timeout are
production code paths and only the socket is elided - which is what lets a
staged read timeout produce a genuine UNCERTAIN rather than a mocked one.
"""

from __future__ import annotations

import pytest

from signalforge.tests.governance.conftest import (  # noqa: F401  (fixtures)
    APP_ID,
    act,
    governed,
    ledger,
    request_execution,
)

pytestmark = pytest.mark.anyio


@pytest.mark.req("REQ-061")
async def test_a_read_only_action_runs_on_request_and_mutates_nothing(governed):
    incident = governed.declare()
    result = request_execution(
        governed.api,
        governed.requester_token,
        incident["id"],
        action_id="collect-diagnostics",
    )
    assert result["decision"]["outcome"] == "allow"
    assert result["decision"]["risk_class"] == "read_only"
    assert result["execution"]["state"] == "succeeded"
    assert "FLOOR-READ-ONLY-NO-MUTATE" in [
        hit["rule_id"] for hit in result["decision"]["rules_hit"]
    ]


@pytest.mark.req("REQ-061")
async def test_a_rollback_needs_approval_and_does_not_execute(governed):
    incident = governed.declare()
    result = request_execution(
        governed.api,
        governed.requester_token,
        incident["id"],
        action_id="rollback-checkout",
        args={"release": "v11"},
    )
    assert result["decision"]["outcome"] == "approval_required"
    assert result["decision"]["risk_class"] == "high_risk"
    assert result["execution"]["state"] == "approval_required"
    assert result["approval"]["state"] == "requested"
    assert result["approval"]["required_roles"] == ["incident_commander"]
    # Nothing reached the application.
    assert governed.observe.state.executions == {}


@pytest.mark.req("REQ-060", "REQ-061")
async def test_asking_to_auto_execute_a_rollback_is_blocked_by_the_floor(governed):
    """The refusal is a recorded ledger row and an audit entry, not an exception
    the caller could swallow."""
    incident = governed.declare()
    result = request_execution(
        governed.api,
        governed.requester_token,
        incident["id"],
        action_id="rollback-checkout",
        args={"release": "v11"},
        intent="auto",
    )
    assert result["decision"]["outcome"] == "block"
    assert result["execution"]["state"] == "blocked"
    rules = [hit["rule_id"] for hit in result["decision"]["rules_hit"]]
    assert "FLOOR-AUTO-EXECUTE-REFUSED" in rules
    assert "FLOOR-HIGH-RISK-NO-AUTO" in rules
    assert governed.observe.state.executions == {}

    topics = governed.api.audit_topics(governed.tenant_id)
    assert "execution.blocked" in topics


@pytest.mark.req("REQ-061")
async def test_the_requester_cannot_approve_their_own_high_risk_action(governed):
    incident = governed.declare()
    result = request_execution(
        governed.api,
        governed.requester_token,
        incident["id"],
        action_id="rollback-checkout",
        args={"release": "v11"},
    )
    act(
        governed.api,
        governed.requester_token,
        incident["id"],
        result["execution"]["execution_id"],
        "approve",
        expect=422,
    )
    assert governed.observe.state.executions == {}


@pytest.mark.req("REQ-061")
async def test_an_approved_rollback_executes_and_spends_its_approval(governed):
    incident = governed.declare()
    requested = request_execution(
        governed.api,
        governed.requester_token,
        incident["id"],
        action_id="rollback-checkout",
        args={"release": "v11"},
    )
    execution_id = requested["execution"]["execution_id"]
    approved = act(
        governed.api,
        governed.approver_token,
        incident["id"],
        execution_id,
        "approve",
        note="agreed, roll back",
    )
    assert approved["execution"]["state"] == "approved"
    assert approved["approval"]["approver_user_id"] == governed.approver_id
    assert approved["approval"]["seconds_left"] > 0

    ran = act(
        governed.api, governed.requester_token, incident["id"], execution_id, "run"
    )
    assert ran["execution"]["state"] == "succeeded"
    assert len(governed.observe.state.executions) == 1

    # One approval, one execution: it cannot be spent again.
    board = ledger(governed.api, governed.requester_token, incident["id"])
    spent = [a for a in board["approvals"] if a["execution_id"] == execution_id]
    assert spent[0]["state"] == "used"


@pytest.mark.req("REQ-061")
async def test_an_expired_approval_cannot_execute(governed):
    incident = governed.declare()
    requested = request_execution(
        governed.api,
        governed.requester_token,
        incident["id"],
        action_id="rollback-checkout",
        args={"release": "v11"},
    )
    execution_id = requested["execution"]["execution_id"]
    act(
        governed.api,
        governed.approver_token,
        incident["id"],
        execution_id,
        "approve",
    )
    # Past the platform cap, so no policy could have kept it alive.
    governed.api.clock.advance(seconds=3_601)
    fresh = relogin_requester(governed)
    refused = act(
        governed.api, fresh, incident["id"], execution_id, "run", expect=422
    )
    assert "expired" in refused["detail"]
    assert governed.observe.state.executions == {}


@pytest.mark.req("REQ-013", "REQ-061")
async def test_permission_revoked_after_approval_blocks_execution(governed):
    """The check people forget. An approval is a record of a decision by
    somebody entitled to make it, not a capability token."""
    incident = governed.declare()
    requested = request_execution(
        governed.api,
        governed.requester_token,
        incident["id"],
        action_id="rollback-checkout",
        args={"release": "v11"},
    )
    execution_id = requested["execution"]["execution_id"]
    act(
        governed.api,
        governed.approver_token,
        incident["id"],
        execution_id,
        "approve",
    )
    # The org owner takes the approver's incident-command role away.
    response = governed.api.client.put(
        f"/users/{governed.approver_id}/roles",
        json={"bindings": [{"role": "sme", "app_id": None}]},
        headers=governed.api.auth_header(governed.owner_token),
    )
    assert response.status_code == 200, response.text

    refused = act(
        governed.api,
        governed.requester_token,
        incident["id"],
        execution_id,
        "run",
        expect=422,
    )
    assert "approve_high_risk" in refused["detail"]
    assert governed.observe.state.executions == {}
    assert "execution.approval.rejected" in governed.api.audit_topics(
        governed.tenant_id
    )


@pytest.mark.req("REQ-062")
async def test_a_duplicate_submit_does_not_execute_twice(governed):
    incident = governed.declare()
    first = request_execution(
        governed.api,
        governed.requester_token,
        incident["id"],
        action_id="collect-diagnostics",
    )
    assert first["execution"]["state"] == "succeeded"
    assert len(governed.observe.state.executions) == 1

    second = request_execution(
        governed.api,
        governed.requester_token,
        incident["id"],
        action_id="collect-diagnostics",
    )
    assert second["duplicate"] is True
    assert second["execution"]["execution_id"] == first["execution"]["execution_id"]
    # The external mutation was not sent again.
    assert len(governed.observe.state.executions) == 1
    assert "execution.duplicate.prevented" in governed.api.audit_topics(
        governed.tenant_id
    )


@pytest.mark.req("REQ-062")
async def test_a_timeout_lands_uncertain_and_blocks_retry(governed):
    incident = governed.declare()
    governed.observe.times_out("/observe/actions/collect-diagnostics/execute")
    result = request_execution(
        governed.api,
        governed.requester_token,
        incident["id"],
        action_id="collect-diagnostics",
    )
    execution = result["execution"]
    assert execution["state"] == "uncertain"
    assert execution["retry_blocked"] is True
    assert execution["error_category"] == "timeout"

    refused = act(
        governed.api,
        governed.requester_token,
        incident["id"],
        execution["execution_id"],
        "retry",
        expect=409,
    )
    assert "reconcile" in refused["detail"].lower()
    assert "execution.retry.blocked" in governed.api.audit_topics(governed.tenant_id)


@pytest.mark.req("REQ-062")
async def test_reconcile_resolves_completed_late_and_forbids_repeating_it(governed):
    """The timeout happened after the application had already applied it.

    The staged timeout fires on the *outbound* path, so the double never sees
    the request - which is why the test writes the execution into the double's
    state directly under the key the gateway derived. That is exactly the world
    this control exists for: the target applied it and SignalForge never heard.
    """
    incident = governed.declare()
    governed.observe.times_out("/observe/actions/collect-diagnostics/execute")
    result = request_execution(
        governed.api,
        governed.requester_token,
        incident["id"],
        action_id="collect-diagnostics",
    )
    execution = result["execution"]
    key = execution["idempotency_key"]
    governed.observe.state.executions[key] = {
        "execution_id": "exec-late-1",
        "action_id": "collect-diagnostics",
        "idempotency_key": key,
        "state": "succeeded",
        "requested_at": "2026-09-01T12:00:00+00:00",
        "finished_at": "2026-09-01T12:00:20+00:00",
        "output": {"applied": True},
    }

    reconciled = act(
        governed.api,
        governed.requester_token,
        incident["id"],
        execution["execution_id"],
        "reconcile",
    )
    assert reconciled["execution"]["state"] == "completed_late"
    assert reconciled["execution"]["reconcile_outcome"] == "completed_late"
    assert reconciled["execution"]["retry_blocked"] is True

    # Repeating it is still refused: it is applied.
    act(
        governed.api,
        governed.requester_token,
        incident["id"],
        execution["execution_id"],
        "retry",
        expect=409,
    )


@pytest.mark.req("REQ-062")
async def test_reconcile_resolves_not_applied_and_a_retry_is_then_allowed(governed):
    incident = governed.declare()
    governed.observe.times_out("/observe/actions/collect-diagnostics/execute")
    result = request_execution(
        governed.api,
        governed.requester_token,
        incident["id"],
        action_id="collect-diagnostics",
    )
    execution = result["execution"]
    reconciled = act(
        governed.api,
        governed.requester_token,
        incident["id"],
        execution["execution_id"],
        "reconcile",
    )
    assert reconciled["execution"]["state"] == "not_applied"
    assert reconciled["execution"]["retry_blocked"] is False

    governed.observe.timing_out.clear()
    retried = act(
        governed.api,
        governed.requester_token,
        incident["id"],
        execution["execution_id"],
        "retry",
    )
    assert retried["execution"]["attempt"] == 2
    assert retried["execution"]["state"] == "succeeded"
    assert (
        retried["execution"]["supersedes_execution_id"]
        == execution["execution_id"]
    )
    # A new attempt at something that provably did not happen gets a new key.
    assert retried["execution"]["idempotency_key"] != execution["idempotency_key"]


@pytest.mark.req("REQ-062")
async def test_human_takeover_is_a_terminal_state_that_records_what_was_done(governed):
    incident = governed.declare()
    governed.observe.times_out("/observe/actions/collect-diagnostics/execute")
    result = request_execution(
        governed.api,
        governed.requester_token,
        incident["id"],
        action_id="collect-diagnostics",
    )
    execution_id = result["execution"]["execution_id"]
    act(
        governed.api,
        governed.requester_token,
        incident["id"],
        execution_id,
        "takeover",
        expect=422,
    )
    taken = act(
        governed.api,
        governed.requester_token,
        incident["id"],
        execution_id,
        "takeover",
        note="ran it by hand on the console; the pods are up",
    )
    assert taken["execution"]["state"] == "taken_over"
    assert taken["execution"]["takeover_by"] == governed.requester_id
    assert taken["execution"]["terminal"] is True


@pytest.mark.req("REQ-061")
async def test_arguments_are_validated_against_the_applications_own_schema(governed):
    incident = governed.declare()
    request_execution(
        governed.api,
        governed.requester_token,
        incident["id"],
        action_id="rollback-checkout",
        args={"release": "v11", "force": True},
        expect=422,
    )
    request_execution(
        governed.api,
        governed.requester_token,
        incident["id"],
        action_id="collect-diagnostics",
        args={"anything": 1},
        expect=422,
    )


@pytest.mark.req("REQ-032", "REQ-061")
async def test_credential_looking_arguments_never_reach_the_ledger(governed):
    """The ledger is not a place credentials are stored."""
    incident = governed.declare()
    governed.observe.state.actions = {
        "schema_version": 1,
        "actions": [
            {
                "action_id": "rotate-token",
                "name": "Rotate token",
                "description": "Rotates the service token.",
                "action_class": "diagnostic",
                "risk": "low",
                "idempotent": True,
                "params_schema": {
                    "type": "object",
                    "properties": {
                        "api_key": {"type": "string"},
                        "note": {"type": "string"},
                    },
                },
            }
        ],
    }
    import_actions_again(governed)
    result = request_execution(
        governed.api,
        governed.requester_token,
        incident["id"],
        action_id="rotate-token",
        args={"api_key": "sk-live-do-not-store-me", "note": "rotating"},
    )
    stored = result["execution"]["args"]
    assert stored["api_key"] == "***"
    assert stored["note"] == "rotating"
    payloads = [
        row["payload"] for row in governed.api.audit_rows(governed.tenant_id)
    ]
    assert "sk-live-do-not-store-me" not in str(payloads)


@pytest.mark.req("REQ-061")
async def test_an_unknown_action_cannot_be_executed(governed):
    incident = governed.declare()
    request_execution(
        governed.api,
        governed.requester_token,
        incident["id"],
        action_id="drop-the-database",
        expect=404,
    )


# --- helpers -------------------------------------------------------------------


def relogin_requester(governed) -> str:
    response = governed.api.client.post(
        "/auth/login",
        json={
            "org_slug": "acme-corp",
            "email": "ic@acme.io",
            "password": "ic-strong-password",
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["tokens"]["access_token"]


def import_actions_again(governed) -> None:
    response = governed.api.client.post(
        f"/apps/{APP_ID}/actions/import",
        json={},
        headers=governed.api.auth_header(governed.owner_token),
    )
    assert response.status_code == 200, response.text
