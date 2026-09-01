"""Sprint 6 acceptance: the high-risk rollback, end to end.

One scenario, run against the real API, the real bus, the real adapters and a
real application that serves the frozen observe contract. It is the sprint's
whole argument in twenty steps:

    1  an incident is active
    2  next best action proposes the rollback, with its score broken out
    3  policy answers APPROVAL_REQUIRED, naming the floor rule
    4  an attempt to auto-execute it is refused by the hard floor
    5  the operator requests it properly
    6  a second person holding approve_high_risk approves
    7  the Tool Gateway sends it
    8  the application does not answer in time
    9  the ledger lands UNCERTAIN
    10 retry is refused
    11 reconciliation asks the application what actually happened
    12 the answer is COMPLETED_LATE
    13 a duplicate submit sends nothing
    14 validation reads the application, never the brain
    15 technical passes
    16 service passes
    17 customer fails
    18 closure is refused
    19 the failure is linked back into the incident's reasoning
    20 every one of the above is in the audit chain

and then, separately, the guide's "a control actively preventing an unsafe
action": somebody tries to configure the rollback to auto-execute, and the
platform safety floor refuses it with audit evidence.
"""

from __future__ import annotations

import pytest

from signalforge.tests.governance.conftest import (  # noqa: F401  (fixtures)
    APP_ID,
    AUTO_ROLLBACK_POLICY,
    Sender,
    act,
    announce,
    close_incident,
    governed,
    graph_nodes,
    issue_key,
    ledger,
    next_best_actions,
    read_incident,
    request_execution,
    run_validation,
    validation_view,
)

pytestmark = pytest.mark.anyio

ROLLBACK = "rollback-checkout"


@pytest.mark.req(
    "REQ-060", "REQ-061", "REQ-062", "REQ-063", "REQ-065", "REQ-016", "REQ-043"
)
async def test_sprint6_high_risk_rollback_end_to_end(governed):
    api = governed.api
    incident = governed.declare(title="Checkout failing after the 14:02 release")
    incident_id = incident["id"]

    # -- 1. the incident is active ------------------------------------------
    assert incident["state"] == "active"
    assert incident["severity"] == "P0"

    # A deployment is announced through the frozen ingest path, so the rollback
    # has something to roll back to. Its precondition is then met for real.
    key = issue_key(api, governed.owner_token, stream="changes", app_id=APP_ID)
    await announce(api, Sender(api=api, key=key), change_id="release-14-02")

    # -- 2. next best action proposes the rollback ---------------------------
    board = next_best_actions(api, governed.requester_token, incident_id, explain=False)
    candidates = (
        ([board["primary"]] if board["primary"] else [])
        + board["alternatives"]
        + board["ineligible"]
    )
    rollback = next(c for c in candidates if c["action_id"] == ROLLBACK)
    assert rollback["eligible"] is True, rollback["eligibility_reason"]
    assert rollback["risk_class"] == "high_risk"
    # The score is arithmetic, and it is shown broken into its terms.
    assert rollback["score"] == pytest.approx(
        rollback["urgency"] * rollback["impact"] * rollback["information_gain"]
        - rollback["risk"],
        abs=1e-4,
    )
    assert rollback["rationale"], "there is always an explanation"
    assert board["degraded"] is True, "and it holds with no model at all"
    assert rollback["rollback_action_id"] == "roll-forward-checkout"
    assert rollback["expected_validation_s"] > 0

    # -- 3. policy answers APPROVAL_REQUIRED ---------------------------------
    assert rollback["requires_approval"] is True
    assert rollback["policy"]["outcome"] == "approval_required"
    assert "FLOOR-HIGH-RISK-NO-AUTO" in [
        hit["rule_id"] for hit in rollback["policy"]["rules_hit"]
    ]
    assert rollback["approver_roles"] == ["incident_commander"]

    # -- 4. an attempt to auto-execute is refused by the floor ---------------
    refused = request_execution(
        api,
        governed.requester_token,
        incident_id,
        action_id=ROLLBACK,
        args={"release": "v11"},
        intent="auto",
    )
    assert refused["decision"]["outcome"] == "block"
    assert refused["execution"]["state"] == "blocked"
    assert "FLOOR-AUTO-EXECUTE-REFUSED" in [
        hit["rule_id"] for hit in refused["decision"]["rules_hit"]
    ]
    assert governed.observe.state.executions == {}, "nothing reached the application"

    # -- 5. the operator requests it properly --------------------------------
    requested = request_execution(
        api,
        governed.requester_token,
        incident_id,
        action_id=ROLLBACK,
        args={"release": "v11"},
        note="rolling back the 14:02 release",
    )
    execution_id = requested["execution"]["execution_id"]
    assert requested["execution"]["state"] == "approval_required"
    assert requested["approval"]["state"] == "requested"
    assert governed.observe.state.executions == {}

    # -- 6. a second person holding approve_high_risk approves ---------------
    approved = act(
        api,
        governed.approver_token,
        incident_id,
        execution_id,
        "approve",
        note="agreed - roll back to v11",
    )
    assert approved["execution"]["state"] == "approved"
    assert approved["approval"]["approver_user_id"] == governed.approver_id
    assert approved["approval"]["permission"] == "approve_high_risk"
    assert 0 < approved["approval"]["seconds_left"] <= 3_600

    # -- 7 & 8. the gateway sends it and the application does not answer -----
    governed.observe.times_out(f"/observe/actions/{ROLLBACK}/execute")
    ran = act(api, governed.requester_token, incident_id, execution_id, "run")

    # -- 9. the ledger lands UNCERTAIN ---------------------------------------
    assert ran["execution"]["state"] == "uncertain"
    assert ran["execution"]["retry_blocked"] is True
    assert ran["execution"]["error_category"] == "timeout"

    # -- 10. retry is refused ------------------------------------------------
    blocked = act(
        api, governed.requester_token, incident_id, execution_id, "retry", expect=409
    )
    assert "reconcile" in blocked["detail"].lower()

    # -- 11 & 12. reconciliation asks, and the answer is COMPLETED_LATE ------
    # The application applied it after SignalForge stopped waiting. Recorded
    # under the key the gateway derived, which is the whole reason that key is
    # derived rather than generated.
    governed.observe.state.executions[ran["execution"]["idempotency_key"]] = {
        "execution_id": "exec-rollback-1",
        "action_id": ROLLBACK,
        "idempotency_key": ran["execution"]["idempotency_key"],
        "state": "succeeded",
        "requested_at": "2026-09-01T12:00:00+00:00",
        "finished_at": "2026-09-01T12:02:00+00:00",
        "output": {"release": "v11", "applied": True},
    }
    reconciled = act(
        api, governed.requester_token, incident_id, execution_id, "reconcile"
    )
    assert reconciled["execution"]["reconcile_outcome"] == "completed_late"
    assert reconciled["execution"]["state"] == "completed_late"
    assert reconciled["execution"]["retry_blocked"] is True, (
        "it landed; repeating it would roll back twice"
    )

    # -- 13. a duplicate submit sends nothing --------------------------------
    governed.observe.timing_out.clear()
    sent_before = dict(governed.observe.state.executions)
    duplicate = request_execution(
        api,
        governed.requester_token,
        incident_id,
        action_id=ROLLBACK,
        args={"release": "v11"},
    )
    assert duplicate["duplicate"] is True
    assert duplicate["execution"]["execution_id"] == execution_id
    assert governed.observe.state.executions == sent_before

    # -- 14..17. validation reads the application, layer by layer ------------
    await _customer_journey_fails(governed)
    cycle = run_validation(api, governed.requester_token, incident_id)
    layers = {layer["layer"]: layer for layer in cycle["layers"]}
    for layer in layers.values():
        for check in layer["checks"]:
            assert check["source"] not in {
                "incident-brain",
                "brain",
                "graph",
                "copilot",
            }, "validation never asks the brain whether the system is healthy"

    assert layers["technical"]["verdict"] == "pass", layers["technical"]["summary"]
    assert layers["service"]["verdict"] in {"pass", "not_applicable"}
    assert layers["customer"]["verdict"] == "fail", layers["customer"]["summary"]
    assert cycle["verdict"] == "not_recovered"

    # -- 18. closure is refused ----------------------------------------------
    assert cycle["closure_blocked"] is True
    assert "customer" in cycle["closure_lock_reason"]
    locked = close_incident(api, governed.requester_token, incident_id, expect=409)
    assert "customer" in locked["detail"]
    assert read_incident(api, governed.requester_token, incident_id)["state"] != "closed"

    # -- 19. the failure is linked back into the reasoning -------------------
    assert cycle["evidence_node_ids"]
    nodes = {node["id"]: node for node in graph_nodes(api, governed.requester_token, incident_id)}
    evidence = nodes[cycle["evidence_node_ids"][0]]
    assert evidence["node_type"] == "evidence"
    assert evidence["provenance"]["authority"] == "observed"
    assert evidence["provenance"]["result_ref"] == f"validation:{cycle['validation_id']}"

    # -- 20. all of it is auditable ------------------------------------------
    topics = api.audit_topics(governed.tenant_id)
    for expected in (
        "execution.requested",
        "execution.decided",
        "execution.blocked",
        "execution.approval.requested",
        "execution.approval.granted",
        "execution.started",
        "execution.uncertain",
        "execution.retry.blocked",
        "execution.reconciled",
        "execution.duplicate.prevented",
        "validation.started",
        "validation.completed",
        "validation.failed",
        "incident.closure.blocked",
    ):
        assert expected in topics, f"{expected} is missing from the audit chain"

    # And the ledger reads as the history a reviewer would want.
    board = ledger(api, governed.requester_token, incident_id)
    states = [row["state"] for row in board["executions"]]
    assert "completed_late" in states
    assert "blocked" in states
    spent = [a for a in board["approvals"] if a["execution_id"] == execution_id]
    assert spent and spent[0]["state"] == "used"


@pytest.mark.req("REQ-060", "REQ-016")
async def test_a_control_actively_prevents_an_unsafe_configuration(governed):
    """The guide's twelfth demonstration check, on its own.

    Somebody with every permission in the organisation tries to make the
    rollback auto-execute. The platform refuses, says which rule refused it, and
    writes the attempt to the audit chain - because the 422 is seen only by the
    person who tried.
    """
    api = governed.api
    before = api.audit_topics(governed.tenant_id)
    assert "policy.floor.rejected" not in before

    response = api.client.post(
        f"/apps/{APP_ID}/policy",
        json={"source": AUTO_ROLLBACK_POLICY},
        headers=api.auth_header(governed.owner_token),
    )
    assert response.status_code == 422, response.text
    detail = response.json()["detail"]
    assert "FLOOR-HIGH-RISK-NO-AUTO" in detail
    assert "approve_high_risk" in detail

    rejection = [
        row
        for row in api.audit_rows(governed.tenant_id)
        if row["topic"] == "policy.floor.rejected"
    ][-1]
    payload = rejection["payload"]
    assert payload["rejected_by"] == "platform-safety-floor"
    assert payload["floor_rule"] == "FLOOR-HIGH-RISK-NO-AUTO"
    assert payload["floor_version"]
    assert "auto_execute" in payload["attempted_source"]

    # The policy did not change, and the rollback still needs a person.
    view = api.client.get(
        f"/apps/{APP_ID}/policy", headers=api.auth_header(governed.owner_token)
    ).json()
    assert view["version"] == 1
    row = next(r for r in view["actions"] if r["action_id"] == ROLLBACK)
    assert row["locked"] is True
    assert row["decision"]["outcome"] == "approval_required"


async def _customer_journey_fails(governed) -> None:
    """The application is up; the customer journey is not.

    A real probe against a path the application does not serve, run by the real
    probe runner over the real adapter. The failing check is an HTTP result, not
    a stubbed verdict.
    """
    governed.observe.state.health = {
        "status": "up",
        "checked_at": "2026-09-01T12:00:00+00:00",
        "components": [
            {"component": "database", "status": "up"},
            {"component": "payment-gateway", "status": "up"},
        ],
    }
    response = governed.api.client.put(
        f"/apps/{APP_ID}/mapping",
        json={
            "journeys": [
                {"journey_id": "card-payment", "name": "Card payment", "tier": 1}
            ],
            "probes": [
                {
                    "probe_id": "card-payment",
                    "path": "/checkout/pay",
                    "journey_id": "card-payment",
                    "per_region": False,
                }
            ],
        },
        headers=governed.api.auth_header(governed.owner_token),
    )
    assert response.status_code == 200, response.text
    records = await governed.api.container.probe_runner.run(
        governed.tenant_id, APP_ID
    )
    assert any(record.verdict.value == "failed" for record in records)
