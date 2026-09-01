"""S3-T04: the policy floor at the onboarding boundary (REQ-036, REQ-060).

Written failing-first. The wizard's action and policy steps are the first place
in the product where a human types something the policy floor has to refuse, and
the refusal has to be structural rather than advisory:

- a high-risk class can never be set to auto-execute;
- a risk override may raise risk and never lower it;
- an approver for a high-risk action must actually hold approve_high_risk;
- an approval TTL is capped, and the high-risk classes cannot be removed from
  the set that requires approval.

Every one of these is refused for an *org owner*, who holds every permission
this platform grants. That is the point: the floor is not a permission.
"""

import pytest

from signalforge.tests.onboarding.conftest import (  # noqa: F401
    APP_ID,
    connect,
    import_actions,
    observe,
    owner,
    patch_action,
    put_governance,
    register_app,
)

HIGH_RISK_ACTION = "restart-billing"
LOW_RISK_ACTION = "collect-diagnostics"


def _onboard_with_actions(api, observe):
    token, tenant_id = owner(api)
    register_app(api, token)
    connect(api, token, "http-observe-actions", app_id=APP_ID, scope="remediation")
    imported = import_actions(api, token)
    return token, tenant_id, imported


@pytest.mark.req("REQ-036", "REQ-060")
def test_imported_high_risk_action_never_arrives_executable(api, observe):
    token, _tenant, imported = _onboard_with_actions(api, observe)

    by_id = {action["action_id"]: action for action in imported["actions"]}
    assert set(by_id) == {HIGH_RISK_ACTION, LOW_RISK_ACTION}

    restart = by_id[HIGH_RISK_ACTION]
    assert restart["high_risk"] is True
    assert restart["effective_risk"] == "high"
    assert restart["execution_mode"] == "approval"
    assert restart["approver_roles"] == ["incident_commander"]

    diagnostics = by_id[LOW_RISK_ACTION]
    assert diagnostics["high_risk"] is False
    assert diagnostics["execution_mode"] == "manual"


@pytest.mark.req("REQ-036", "REQ-060")
def test_high_risk_action_cannot_be_set_to_auto_execute(api, observe):
    token, _tenant, _imported = _onboard_with_actions(api, observe)

    refused = patch_action(
        api, token, HIGH_RISK_ACTION, execution_mode="auto", expect=422
    )
    assert "auto" in refused["detail"]
    assert "high-risk" in refused["detail"]

    # The refusal changed nothing.
    still = api.client.get(f"/apps/{APP_ID}", headers=api.auth_header(token)).json()
    action = next(
        a for a in still["profile"]["actions"] if a["action_id"] == HIGH_RISK_ACTION
    )
    assert action["execution_mode"] == "approval"


@pytest.mark.req("REQ-036", "REQ-060")
def test_risk_override_may_raise_but_never_lower(api, observe):
    token, _tenant, _imported = _onboard_with_actions(api, observe)

    lowered = patch_action(
        api, token, HIGH_RISK_ACTION, risk_override="low", expect=422
    )
    assert "risk" in lowered["detail"]

    raised = patch_action(api, token, LOW_RISK_ACTION, risk_override="high")
    action = next(
        a for a in raised["actions"] if a["action_id"] == LOW_RISK_ACTION
    )
    assert action["risk_override"] == "high"
    assert action["effective_risk"] == "high"
    assert action["manifest_risk"] == "low", "what the app declared is not overwritten"


@pytest.mark.req("REQ-036", "REQ-060")
def test_approver_must_hold_approve_high_risk(api, observe):
    token, _tenant, _imported = _onboard_with_actions(api, observe)

    refused = patch_action(
        api, token, HIGH_RISK_ACTION, approver_roles=["executive"], expect=422
    )
    assert "approve_high_risk" in refused["detail"]

    allowed = patch_action(
        api,
        token,
        HIGH_RISK_ACTION,
        approver_roles=["incident_commander", "tech_lead"],
    )
    action = next(
        a for a in allowed["actions"] if a["action_id"] == HIGH_RISK_ACTION
    )
    assert action["approver_roles"] == ["incident_commander", "tech_lead"]


@pytest.mark.req("REQ-036", "REQ-060")
def test_high_risk_action_cannot_be_left_without_an_approver(api, observe):
    token, _tenant, _imported = _onboard_with_actions(api, observe)
    refused = patch_action(
        api, token, HIGH_RISK_ACTION, approver_roles=[], expect=422
    )
    assert "approver role" in refused["detail"]


@pytest.mark.req("REQ-036", "REQ-060")
def test_low_risk_action_may_be_automated(api, observe):
    token, _tenant, _imported = _onboard_with_actions(api, observe)
    updated = patch_action(api, token, LOW_RISK_ACTION, execution_mode="auto")
    action = next(a for a in updated["actions"] if a["action_id"] == LOW_RISK_ACTION)
    assert action["execution_mode"] == "auto"


@pytest.mark.req("REQ-036", "REQ-060")
def test_policy_defaults_cap_the_approval_ttl(api, observe):
    token, _tenant_id = owner(api)
    register_app(api, token)

    refused = put_governance(
        api, token, policy={"approval_ttl_s": 86_400}, expect=422
    )
    assert "approval_ttl_s" in refused["detail"]

    accepted = put_governance(api, token, policy={"approval_ttl_s": 600})
    assert accepted["policy"]["approval_ttl_s"] == 600


@pytest.mark.req("REQ-036", "REQ-060")
def test_high_risk_classes_cannot_be_dropped_from_approval(api, observe):
    token, _tenant_id = owner(api)
    register_app(api, token)

    refused = put_governance(
        api,
        token,
        policy={"require_approval_classes": ["restart", "failover"]},
        expect=422,
    )
    assert "rollback" in refused["detail"]

    unchanged = api.client.get(
        f"/apps/{APP_ID}", headers=api.auth_header(token)
    ).json()
    assert "rollback" in unchanged["profile"]["policy"]["require_approval_classes"]


@pytest.mark.req("REQ-036", "REQ-060")
def test_a_refused_policy_edit_is_audited_and_the_defaults_hold(api, observe):
    token, tenant_id = owner(api)
    register_app(api, token)

    defaults = api.client.get(
        f"/apps/{APP_ID}", headers=api.auth_header(token)
    ).json()["profile"]["policy"]
    assert defaults["auto_execute_low_risk"] is False
    assert defaults["approval_ttl_s"] <= 3_600
    assert set(defaults["require_approval_classes"]) >= {
        "rollback", "restart", "failover", "traffic_shift",
        "config_change", "customer_impacting",
    }

    put_governance(api, token, policy={"approval_ttl_s": 999_999}, expect=422)

    topics = api.audit_topics(tenant_id)
    assert "system.app.policy.refused" in topics, (
        "a contradicted floor is evidence, not only an error message"
    )
