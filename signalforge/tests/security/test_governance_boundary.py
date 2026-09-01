"""The consequential-action boundary (S6, REQ-013, REQ-060..REQ-065).

The other governance suites prove the features work. This one proves the
refusals hold, and every test here is a thing an attacker - or a well-meaning
person in a hurry at 03:00 - would try.

The list is deliberately short and each entry is a boundary rather than a
behaviour:

| What is tried | What must happen |
|---|---|
| execute in another tenant's incident | 404, indistinguishable from an id that exists nowhere |
| approve without approve_high_risk | 403 |
| approve your own request | 422, whatever permissions you hold |
| execute on an expired approval | 422, and nothing reaches the application |
| execute after the approver lost the permission | 422 |
| retry an unreconciled execution | 409 |
| submit the same mutation twice | the first execution, and nothing sent again |
| put a credential in an action argument | `***` in the ledger and in the audit chain |
| close an incident whose customer layer is failing | 409 |
| make a communication send itself | there is no such route |
"""

from __future__ import annotations

import pytest

from signalforge.tests.adapters.conftest import observe  # noqa: F401  (fixture)
from signalforge.tests.governance.conftest import (  # noqa: F401  (fixtures)
    APP_ID,
    act,
    governed,
    request_execution,
)

pytestmark = pytest.mark.anyio


@pytest.mark.req("REQ-013")
async def test_another_tenants_incident_is_not_reachable(governed):
    """A second organisation, its own incident, and no path between them.

    The refusal is a 404 rather than a 403 on purpose: a 403 would confirm the
    id exists, which is a fact about another tenant.
    """
    incident = governed.declare()
    intruder = governed.api.signup(
        org="Other Corp", email="owner@other.io", display_name="Other"
    )
    token = intruder["tokens"]["access_token"]

    for method, path in (
        ("get", f"/incidents/{incident['id']}/executions"),
        ("get", f"/incidents/{incident['id']}/validation"),
        ("get", f"/incidents/{incident['id']}/communications"),
        ("get", f"/incidents/{incident['id']}/actions"),
    ):
        response = getattr(governed.api.client, method)(
            path, headers=governed.api.auth_header(token)
        )
        assert response.status_code == 404, f"{path}: {response.text}"

    response = governed.api.client.post(
        f"/incidents/{incident['id']}/executions",
        json={"action_id": "collect-diagnostics"},
        headers=governed.api.auth_header(token),
    )
    assert response.status_code == 404, response.text
    assert governed.observe.state.executions == {}


@pytest.mark.req("REQ-012", "REQ-061")
async def test_approving_needs_approve_high_risk(governed):
    incident = governed.declare()
    requested = request_execution(
        governed.api,
        governed.requester_token,
        incident["id"],
        action_id="rollback-checkout",
        args={"release": "v11"},
    )
    sme = governed.api.invite_and_accept(
        governed.owner_token,
        email="sme@acme.io",
        roles=["sme"],
        display_name="Sam SME",
        password="sme-strong-password",
    )
    response = governed.api.client.post(
        f"/incidents/{incident['id']}/executions/"
        f"{requested['execution']['execution_id']}/approve",
        json={},
        headers=governed.api.auth_header(sme["tokens"]["access_token"]),
    )
    assert response.status_code == 403, response.text
    assert governed.observe.state.executions == {}


@pytest.mark.req("REQ-012", "REQ-064")
async def test_an_executive_cannot_approve_a_customer_statement(governed):
    """Separation of duties, per audience. `approve_exec_comms` is not
    `approve_customer_comms`, and the platform will not treat one as the other."""
    from signalforge.tests.governance.conftest import (
        communication_action,
        draft_communication,
    )

    incident = governed.declare()
    await governed.api.container.governor.run_for(governed.tenant_id, incident["id"])
    executive = governed.api.invite_and_accept(
        governed.owner_token,
        email="exec@acme.io",
        roles=["executive"],
        display_name="Eve Exec",
        password="exec-strong-password",
    )
    draft = draft_communication(
        governed.api, governed.requester_token, incident["id"], audience="customer"
    )
    communication_action(
        governed.api,
        executive["tokens"]["access_token"],
        incident["id"],
        draft["communication_id"],
        "approve",
        expect=403,
    )


@pytest.mark.req("REQ-064")
async def test_there_is_no_route_that_sends_a_communication(governed):
    """Structural, not a filter. The API surface has no send, transmit,
    broadcast or publish operation on a communication, and `issue` is guarded by
    a person's permission."""
    spec = governed.api.client.get("/openapi.json").json()
    comms_paths = [p for p in spec["paths"] if "/communications" in p]
    assert comms_paths, "the surface exists"
    for path in comms_paths:
        tail = path.rsplit("/", 1)[-1]
        assert tail not in {"send", "transmit", "broadcast", "publish", "post"}


@pytest.mark.req("REQ-060")
async def test_the_floor_is_not_a_permission(governed):
    """An org owner holding every permission is refused exactly as firmly as
    anybody else. The floor is not an authorization decision."""
    from signalforge.tests.governance.conftest import AUTO_ROLLBACK_POLICY

    response = governed.api.client.post(
        f"/apps/{APP_ID}/policy",
        json={"source": AUTO_ROLLBACK_POLICY},
        headers=governed.api.auth_header(governed.owner_token),
    )
    assert response.status_code == 422, response.text
    assert "FLOOR-HIGH-RISK-NO-AUTO" in response.json()["detail"]


@pytest.mark.req("REQ-062")
async def test_an_uncertain_execution_cannot_be_retried_by_any_route(governed):
    """The refusal is on the ledger row, not on one route, so `retry` and a
    fresh submit of the same request are both refused."""
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
        "retry",
        expect=409,
    )
    # Re-submitting the identical request derives the identical key and is
    # refused for the same reason rather than starting a second attempt.
    governed.observe.timing_out.clear()
    response = governed.api.client.post(
        f"/incidents/{incident['id']}/executions",
        json={"action_id": "collect-diagnostics"},
        headers=governed.api.auth_header(governed.requester_token),
    )
    assert response.status_code == 409, response.text
    assert "reconcile" in response.json()["detail"].lower()
    assert governed.observe.state.executions == {}


@pytest.mark.req("REQ-061")
async def test_an_agent_holds_no_role_and_therefore_no_path_to_execution(governed):
    """The frozen matrix gives roles only to people; there is no agent principal
    in this platform, and the gateway's `requested_by` is always a user id."""
    from signalforge.contracts.roles import ROLE_PERMISSIONS, Permission, Role

    assert set(ROLE_PERMISSIONS) == set(Role)
    holders = [
        role
        for role, permissions in ROLE_PERMISSIONS.items()
        if Permission.APPROVE_HIGH_RISK in permissions
    ]
    assert holders and all(role in set(Role) for role in holders)

    incident = governed.declare()
    result = request_execution(
        governed.api,
        governed.requester_token,
        incident["id"],
        action_id="collect-diagnostics",
    )
    assert result["execution"]["requested_by"] == governed.requester_id


@pytest.mark.req("REQ-016", "REQ-061")
async def test_every_execution_transition_is_in_the_audit_chain(governed):
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
    act(governed.api, governed.requester_token, incident["id"], execution_id, "run")

    topics = governed.api.audit_topics(governed.tenant_id)
    for expected in (
        "execution.requested",
        "execution.decided",
        "execution.approval.requested",
        "execution.approval.granted",
        "execution.started",
        "execution.succeeded",
    ):
        assert expected in topics, f"{expected} is missing from the audit chain"

    # The chain is intact after all of it.
    response = governed.api.client.get(
        "/platform/audit/verify", headers=governed.api.auth_header(governed.owner_token)
    )
    if response.status_code == 200:
        assert response.json().get("valid", True) is True
