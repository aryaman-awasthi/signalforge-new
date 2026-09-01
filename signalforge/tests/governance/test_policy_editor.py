"""The policy editor over the real API (REQ-060).

`test_policy_floor.py` proves the floor is a property of the objects. This file
proves the surface an operator actually touches behaves the same way: the
attempt is audited, the version is authored, the dry run uses the engine that
decides, and the high-risk rows come back locked.
"""

from __future__ import annotations

import pytest

from signalforge.tests.governance.conftest import (  # noqa: F401  (fixtures)
    APP_ID,
    AUTO_ROLLBACK_POLICY,
    STRICT_POLICY,
    dry_run_policy,
    get_policy,
    governed,
    put_policy,
)

pytestmark = pytest.mark.anyio


@pytest.mark.req("REQ-060")
async def test_the_default_policy_locks_every_high_risk_row(governed):
    view = get_policy(governed.api, governed.owner_token)
    assert view["version"] == 1
    rows = {row["action_id"]: row for row in view["actions"]}

    rollback = rows["rollback-checkout"]
    assert rollback["locked"] is True
    assert rollback["decision"]["outcome"] == "approval_required"
    assert "cannot auto-execute" in rollback["locked_reason"] or (
        "never auto-execute" in rollback["locked_reason"]
    )

    diagnostic = rows["collect-diagnostics"]
    assert diagnostic["locked"] is False
    assert diagnostic["decision"]["risk_class"] == "read_only"

    floor = view["floor"]
    assert floor["max_approval_ttl_s"] == 3_600
    assert "FLOOR-HIGH-RISK-NO-AUTO" in [
        rule["rule_id"] for rule in floor["rules"]
    ]


@pytest.mark.req("REQ-060")
async def test_setting_a_rollback_to_auto_execute_is_refused_and_audited(governed):
    """The sprint's critical acceptance, at the surface an operator uses."""
    response = governed.api.client.post(
        f"/apps/{APP_ID}/policy",
        json={"source": AUTO_ROLLBACK_POLICY},
        headers=governed.api.auth_header(governed.owner_token),
    )
    assert response.status_code == 422, response.text
    assert "FLOOR-HIGH-RISK-NO-AUTO" in response.json()["detail"]

    rows = governed.api.audit_rows(governed.tenant_id)
    rejections = [r for r in rows if r["topic"] == "policy.floor.rejected"]
    assert rejections, "the refusal must be in the audit chain, not only in the 422"
    payload = rejections[-1]["payload"]
    assert payload["rejected_by"] == "platform-safety-floor"
    assert payload["floor_rule"] == "FLOOR-HIGH-RISK-NO-AUTO"
    assert payload["attempted_by"], "the attempt names who made it"
    assert "auto_execute" in payload["attempted_source"]

    # And nothing was stored.
    assert get_policy(governed.api, governed.owner_token)["version"] == 1


@pytest.mark.req("REQ-060")
async def test_a_policy_edit_writes_a_version_that_names_its_author(governed):
    # Version 2, not 1: the platform default is version 1, and an approval
    # granted under it must not survive the first real edit.
    view = put_policy(governed.api, governed.owner_token, STRICT_POLICY, note="tighter")
    assert view["version"] == 2
    assert view["rules"]["approval_ttl_s"] == 600

    again = put_policy(
        governed.api, governed.owner_token, STRICT_POLICY, note="no change"
    )
    assert again["version"] == 3
    versions = again["versions"]
    assert [v["version"] for v in versions] == [3, 2]
    assert all(v["author_id"] for v in versions)
    assert versions[0]["note"] == "no change"


@pytest.mark.req("REQ-060")
async def test_the_dry_run_uses_the_engine_that_decides(governed):
    tightened = """
approval_ttl_s: 300
actions:
  collect-diagnostics:
    require_approval: true
    note: "noisy in production"
"""
    result = dry_run_policy(governed.api, governed.owner_token, tightened)
    assert result["accepted"] is True
    change = next(
        c for c in result["changes"] if c["action_id"] == "collect-diagnostics"
    )
    assert change["from"] == "allow"
    assert change["to"] == "approval_required"
    # Nothing was written.
    assert get_policy(governed.api, governed.owner_token)["version"] == 1


@pytest.mark.req("REQ-060")
async def test_a_dry_run_of_an_unsafe_document_reports_the_rule_rather_than_erroring(
    governed,
):
    result = dry_run_policy(governed.api, governed.owner_token, AUTO_ROLLBACK_POLICY)
    assert result["accepted"] is False
    assert result["floor_rule"] == "FLOOR-HIGH-RISK-NO-AUTO"
    assert result["after"] == []
    assert result["before"], "the current decisions are still shown beside it"
    assert "policy.floor.rejected" in governed.api.audit_topics(governed.tenant_id)


@pytest.mark.req("REQ-060")
async def test_audit_cannot_be_configured_off(governed):
    response = governed.api.client.post(
        f"/apps/{APP_ID}/policy",
        json={"source": "audit: false\napproval_ttl_s: 900\n"},
        headers=governed.api.auth_header(governed.owner_token),
    )
    assert response.status_code == 422, response.text
    assert "FLOOR-AUDIT-IMMUTABLE" in response.json()["detail"]


@pytest.mark.req("REQ-060")
async def test_an_unknown_setting_is_refused_rather_than_ignored(governed):
    response = governed.api.client.post(
        f"/apps/{APP_ID}/policy",
        json={"source": "auto_approve_everything: true\n"},
        headers=governed.api.auth_header(governed.owner_token),
    )
    assert response.status_code == 422, response.text
    assert "auto_approve_everything" in response.json()["detail"]


@pytest.mark.req("REQ-060")
async def test_editing_mid_incident_needs_confirmation_and_is_recorded(governed):
    incident = governed.declare()
    response = governed.api.client.post(
        f"/apps/{APP_ID}/policy",
        json={"source": STRICT_POLICY},
        headers=governed.api.auth_header(governed.owner_token),
    )
    assert response.status_code == 409, response.text
    assert "confirm_active_incident" in response.json()["detail"]

    put_policy(
        governed.api,
        governed.owner_token,
        STRICT_POLICY,
        confirm_active_incident=True,
    )
    rows = governed.api.audit_rows(governed.tenant_id)
    confirmed = [r for r in rows if r["topic"] == "policy.edit.mid_incident"]
    assert confirmed, "a mid-incident edit is recorded as one"
    assert incident["reference"] in confirmed[-1]["payload"]["open_incidents"]


@pytest.mark.req("REQ-060", "REQ-061")
async def test_a_policy_edit_invalidates_an_approval_granted_under_the_old_version(
    governed,
):
    """A high-risk action approved under one set of rules is not silently
    re-interpreted under another."""
    from signalforge.tests.governance.conftest import act, request_execution

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

    put_policy(
        governed.api,
        governed.owner_token,
        STRICT_POLICY,
        confirm_active_incident=True,
    )
    refused = act(
        governed.api,
        governed.requester_token,
        incident["id"],
        execution_id,
        "run",
        expect=422,
    )
    assert "policy version" in refused["detail"]
    assert governed.observe.state.executions == {}


@pytest.mark.req("REQ-012", "REQ-060")
async def test_editing_a_policy_needs_edit_policies(governed):
    """The incident commander runs incidents; they do not rewrite the rules."""
    governed.api.client.post(
        f"/apps/{APP_ID}/policy",
        json={"source": STRICT_POLICY},
        headers=governed.api.auth_header(governed.requester_token),
    )
    response = governed.api.client.post(
        f"/apps/{APP_ID}/policy",
        json={"source": STRICT_POLICY},
        headers=governed.api.auth_header(governed.requester_token),
    )
    assert response.status_code == 403, response.text
