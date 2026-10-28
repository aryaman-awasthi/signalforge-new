"""Tool-gateway repairs found by the S11 review (REQ-061, REQ-062, REQ-063).

Each test here failed against the gateway as sprint 10 left it:

- an approved execution reached the adapter with *no arguments* (`run` has no
  body, `execute` sent `{}`), so a rollback approved for `release: v11` was sent
  without a release;
- a settled attempt was never a duplicate, so a double-click on a fast action
  ran it twice - while S10-T22 was right that a renewal twenty minutes later is
  a new attempt;
- an approval that lapsed left its execution APPROVED forever: `run` refused it
  and every new request for the same action was answered as its duplicate;
- an approver's role on *another* application satisfied this application's
  approver roles;
- a compensation moved the source to COMPENSATING and nothing ever moved it on.
"""

from __future__ import annotations

import pytest

from signalforge.tests.governance.conftest import (
    APP_ID,
    act,
    ledger,
    relogin,
    request_execution,
)

pytestmark = pytest.mark.anyio


def _sent(governed) -> list[dict]:
    return list(governed.observe.state.executions.values())


def _relogin(governed, email: str = "ic@acme.io") -> str:
    return relogin(governed.api, email=email, password="ic-strong-password")


@pytest.mark.req("REQ-061", "REQ-063")
async def test_an_approved_execution_is_sent_with_the_arguments_it_was_authorised_for(governed):
    incident = governed.declare()
    requested = request_execution(
        governed.api,
        governed.requester_token,
        incident["id"],
        action_id="rollback-checkout",
        args={"release": "v11"},
    )
    execution_id = requested["execution"]["execution_id"]
    act(governed.api, governed.approver_token, incident["id"], execution_id, "approve")

    ran = act(governed.api, governed.requester_token, incident["id"], execution_id, "run")
    assert ran["execution"]["state"] == "succeeded"
    sent = _sent(governed)
    assert len(sent) == 1
    assert sent[0]["output"]["params"] == {"release": "v11"}, (
        "the adapter must receive what the approver approved, not an empty call"
    )

    # Arguments that disagree with the authorised ones are still refused.
    other = request_execution(
        governed.api,
        governed.requester_token,
        incident["id"],
        action_id="rollback-checkout",
        args={"release": "v12"},
    )
    act(governed.api, governed.approver_token, incident["id"], other["execution"]["execution_id"], "approve")
    refused = act(
        governed.api,
        governed.requester_token,
        incident["id"],
        other["execution"]["execution_id"],
        "run",
        expect=422,
        args={"release": "v13"},
    )
    assert "do not match" in refused["detail"]
    assert len(_sent(governed)) == 1


@pytest.mark.req("REQ-061")
async def test_a_repeat_inside_the_window_is_a_duplicate_and_a_later_one_is_a_new_attempt(governed):
    incident = governed.declare()
    first = request_execution(
        governed.api, governed.requester_token, incident["id"], action_id="collect-diagnostics"
    )
    assert first["execution"]["state"] == "succeeded"

    # The double-click: seconds later, same action, same (empty) arguments.
    governed.api.clock.advance(seconds=5)
    again = request_execution(
        governed.api, governed.requester_token, incident["id"], action_id="collect-diagnostics"
    )
    assert again["duplicate"] is True
    assert again["execution"]["execution_id"] == first["execution"]["execution_id"]
    assert len(_sent(governed)) == 1

    # Ten minutes on, the same request is a decision, not a repeat (S10-T22).
    governed.api.clock.advance(minutes=10)
    later = request_execution(
        governed.api, governed.requester_token, incident["id"], action_id="collect-diagnostics"
    )
    assert later["duplicate"] is False
    assert later["execution"]["execution_id"] != first["execution"]["execution_id"]
    assert later["execution"]["attempt"] == 2
    assert later["execution"]["state"] == "succeeded"
    assert len(_sent(governed)) == 2


@pytest.mark.req("REQ-061")
async def test_an_approval_that_lapses_releases_the_execution_for_a_fresh_request(governed):
    incident = governed.declare()
    requested = request_execution(
        governed.api,
        governed.requester_token,
        incident["id"],
        action_id="rollback-checkout",
        args={"release": "v11"},
    )
    execution_id = requested["execution"]["execution_id"]
    act(governed.api, governed.approver_token, incident["id"], execution_id, "approve")

    # Past the platform cap on any approval; the sweep notices.
    governed.api.clock.advance(seconds=3_601)
    assert await governed.api.container.gateway.expire_approvals() == 1

    token = _relogin(governed)
    board = ledger(governed.api, token, incident["id"])
    row = next(e for e in board["executions"] if e["execution_id"] == execution_id)
    assert row["state"] == "blocked"
    assert "expired" in row["decision_reason"]
    approval = next(a for a in board["approvals"] if a["execution_id"] == execution_id)
    assert approval["state"] == "expired"

    # The same request is now a new attempt waiting for a new approval - not
    # "already requested" pointing at a row nobody can run.
    fresh = request_execution(
        governed.api, token, incident["id"], action_id="rollback-checkout", args={"release": "v11"}
    )
    assert fresh["duplicate"] is False
    assert fresh["execution"]["execution_id"] != execution_id
    assert fresh["execution"]["state"] == "approval_required"
    assert _sent(governed) == []


@pytest.mark.req("REQ-012", "REQ-061")
async def test_an_approver_role_bound_to_another_application_does_not_count(governed):
    """`rollback-checkout` wants an incident commander. A tech lead who is an
    incident commander *on billing* holds approve_high_risk here through the
    tech-lead binding, but the commander seat they hold is somebody else's."""
    api = governed.api
    registered = api.client.post(
        "/apps", json={"app_id": "billing", "name": "Billing"}, headers=api.auth_header(governed.owner_token)
    )
    assert registered.status_code == 201, registered.text
    joined = api.invite_and_accept(
        governed.owner_token,
        email="lead@acme.io",
        roles=["tech_lead"],
        display_name="Lena Lead",
        password="ic-strong-password",
    )
    rebound = api.client.put(
        f"/users/{joined['user']['id']}/roles",
        json={
            "bindings": [
                {"role": "tech_lead", "app_id": None},
                {"role": "incident_commander", "app_id": "billing"},
            ]
        },
        headers=api.auth_header(governed.owner_token),
    )
    assert rebound.status_code == 200, rebound.text
    lead_token = relogin(api, email="lead@acme.io", password="ic-strong-password")

    incident = governed.declare()
    requested = request_execution(
        governed.api,
        governed.requester_token,
        incident["id"],
        action_id="rollback-checkout",
        args={"release": "v11"},
    )
    execution_id = requested["execution"]["execution_id"]
    refused = act(api, lead_token, incident["id"], execution_id, "approve", expect=422)
    assert "incident_commander" in refused["detail"]

    # The commander on *this* application still can.
    approved = act(api, governed.approver_token, incident["id"], execution_id, "approve")
    assert approved["execution"]["state"] == "approved"


@pytest.mark.req("REQ-062", "REQ-063")
async def test_a_compensation_settles_the_row_it_compensates(governed):
    incident = governed.declare()
    requested = request_execution(
        governed.api,
        governed.requester_token,
        incident["id"],
        action_id="rollback-checkout",
        args={"release": "v11"},
    )
    execution_id = requested["execution"]["execution_id"]
    act(governed.api, governed.approver_token, incident["id"], execution_id, "approve")
    ran = act(governed.api, governed.requester_token, incident["id"], execution_id, "run")
    assert ran["execution"]["state"] == "succeeded"

    compensating = act(
        governed.api, governed.requester_token, incident["id"], execution_id, "compensate",
        note="rolled back the wrong thing",
    )
    reverse_id = compensating["execution"]["execution_id"]
    assert compensating["execution"]["action_id"] == "roll-forward-checkout"
    assert compensating["execution"]["state"] == "approval_required"
    assert compensating["execution"]["compensates_execution_id"] == execution_id

    board = ledger(governed.api, governed.requester_token, incident["id"])
    source = next(e for e in board["executions"] if e["execution_id"] == execution_id)
    assert source["state"] == "compensating"
    assert source["compensated_by_execution_id"] == reverse_id

    # While the reverse waits for its approval the original is in flight, so a
    # repeat of it is answered as such and nothing is sent.
    repeat = request_execution(
        governed.api,
        governed.requester_token,
        incident["id"],
        action_id="rollback-checkout",
        args={"release": "v11"},
    )
    assert repeat["duplicate"] is True

    act(governed.api, governed.approver_token, incident["id"], reverse_id, "approve")
    undone = act(governed.api, governed.requester_token, incident["id"], reverse_id, "run")
    assert undone["execution"]["state"] == "succeeded"
    board = ledger(governed.api, governed.requester_token, incident["id"])
    source = next(e for e in board["executions"] if e["execution_id"] == execution_id)
    assert source["state"] == "compensated", "the loop closes when the reverse lands"
    assert "execution.compensated" in governed.api.audit_topics(governed.tenant_id)
    assert len(_sent(governed)) == 2
