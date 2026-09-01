"""The platform safety floor, tested where it lives (REQ-060).

Pure domain. No API, no database, no clock - because the claim under test is
that the floor is a property of the *objects*, not of a route that happens to
guard them. A test that could only prove it through HTTP would be proving that
one caller is careful.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from signalforge.contracts.canonical import ActionClass, RiskLevel
from signalforge.contracts.roles import Role
from signalforge.domain.governance.errors import PolicyFloorViolation
from signalforge.domain.governance.policy import (
    MAX_APPROVAL_TTL_S,
    ActionRule,
    Outcome,
    PolicyDocument,
    PolicyEngine,
    RiskClass,
    classify,
    default_policy,
)
from signalforge.domain.onboarding.profile import ExecutionMode, MappedAction

AT = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)


def rollback(**overrides) -> MappedAction:
    fields = {
        "action_id": "rollback-payments",
        "name": "Roll back payments",
        "action_class": ActionClass.ROLLBACK,
        "manifest_risk": RiskLevel.HIGH,
        "approver_roles": (Role.INCIDENT_COMMANDER,),
        "execution_mode": ExecutionMode.APPROVAL,
        "reversible": False,
    }
    fields.update(overrides)
    return MappedAction(**fields)


def diagnostic(**overrides) -> MappedAction:
    fields = {
        "action_id": "dump-pool-stats",
        "name": "Dump pool stats",
        "action_class": ActionClass.DIAGNOSTIC,
        "manifest_risk": RiskLevel.LOW,
        "execution_mode": ExecutionMode.MANUAL,
        "idempotent": True,
    }
    fields.update(overrides)
    return MappedAction(**fields)


def policy(**overrides) -> PolicyDocument:
    fields = {
        "tenant_id": "t1",
        "app_id": "payments",
        "version": 1,
        "created_at": AT,
        "author_id": "u-owner",
    }
    fields.update(overrides)
    return PolicyDocument(**fields)


@pytest.mark.req("REQ-060")
def test_rollback_auto_execute_true_is_rejected_by_the_floor():
    """The sprint's critical test, at the level the floor actually lives.

    A policy that says `rollback: auto_execute: true` does not become a
    document with a flag the engine later ignores - it does not become a
    document at all.
    """
    document = policy(
        action_rules={
            "rollback-payments": ActionRule(
                action_id="rollback-payments", auto_execute=True
            )
        }
    )
    with pytest.raises(PolicyFloorViolation) as raised:
        document.assert_against_actions([rollback()])
    message = str(raised.value)
    assert "FLOOR-HIGH-RISK-NO-AUTO" in message
    assert "rollback" in message
    assert "approve_high_risk" in message


@pytest.mark.req("REQ-060")
@pytest.mark.parametrize(
    "action_class",
    sorted(
        (
            ActionClass.ROLLBACK,
            ActionClass.RESTART,
            ActionClass.FAILOVER,
            ActionClass.TRAFFIC_SHIFT,
            ActionClass.CONFIG_CHANGE,
            ActionClass.CUSTOMER_IMPACTING,
        ),
        key=lambda c: c.value,
    ),
)
def test_every_high_risk_class_refuses_auto_execution(action_class):
    action = rollback(action_id=f"do-{action_class.value}", action_class=action_class)
    document = policy(
        action_rules={
            action.action_id: ActionRule(action_id=action.action_id, auto_execute=True)
        }
    )
    with pytest.raises(PolicyFloorViolation):
        document.assert_against_actions([action])


@pytest.mark.req("REQ-060")
def test_asking_the_engine_to_auto_execute_a_rollback_blocks_rather_than_allows():
    """A caller that asks for AUTO gets a recorded BLOCK, not an exception the
    client could swallow and not a silent downgrade to approval."""
    decision = PolicyEngine.decide(
        rollback(), policy(), intent=ExecutionMode.AUTO
    )
    assert decision.outcome is Outcome.BLOCK
    assert "FLOOR-AUTO-EXECUTE-REFUSED" in decision.floor_rules
    assert "FLOOR-HIGH-RISK-NO-AUTO" in decision.floor_rules


@pytest.mark.req("REQ-060")
def test_high_risk_without_intent_is_approval_required_with_a_bounded_ttl():
    decision = PolicyEngine.decide(rollback(), policy(approval_ttl_s=900))
    assert decision.outcome is Outcome.APPROVAL_REQUIRED
    assert decision.risk_class is RiskClass.HIGH_RISK
    assert decision.approval_ttl_s == 900
    assert Role.INCIDENT_COMMANDER in decision.approver_roles


@pytest.mark.req("REQ-060")
def test_approval_ttl_cannot_exceed_the_platform_cap():
    with pytest.raises(PolicyFloorViolation) as raised:
        policy(approval_ttl_s=MAX_APPROVAL_TTL_S + 1)
    assert "FLOOR-APPROVAL-TTL-CAP" in str(raised.value)


@pytest.mark.req("REQ-060")
def test_high_risk_classes_cannot_be_dropped_from_require_approval():
    with pytest.raises(PolicyFloorViolation) as raised:
        policy(require_approval_classes=frozenset({ActionClass.RESTART}))
    assert "FLOOR-HIGH-RISK-NO-AUTO" in str(raised.value)
    assert "rollback" in str(raised.value)


@pytest.mark.req("REQ-060")
def test_an_ineligible_role_cannot_be_named_as_an_approver():
    """Naming an Executive as the approver for a rollback is a separation-of-
    duties violation, and it is refused where somebody types it."""
    with pytest.raises(PolicyFloorViolation) as raised:
        policy(approver_roles=(Role.EXECUTIVE,))
    assert "FLOOR-HIGH-RISK-APPROVER" in str(raised.value)


@pytest.mark.req("REQ-060")
def test_risk_override_may_raise_but_never_lower():
    document = policy(
        action_rules={
            "dump-pool-stats": ActionRule(
                action_id="dump-pool-stats", risk_override=RiskLevel.HIGH
            )
        }
    )
    document.assert_against_actions([diagnostic()])  # raising is fine

    lowering = policy(
        action_rules={
            "rollback-payments": ActionRule(
                action_id="rollback-payments", risk_override=RiskLevel.LOW
            )
        }
    )
    with pytest.raises(PolicyFloorViolation) as raised:
        lowering.assert_against_actions([rollback()])
    assert "FLOOR-RISK-NEVER-LOWERED" in str(raised.value)


@pytest.mark.req("REQ-060")
def test_a_rollback_declared_low_risk_by_the_application_is_still_high_risk():
    """An application publishing `risk: low` on a rollback changes nothing."""
    assert (
        classify(ActionClass.ROLLBACK, RiskLevel.LOW, override=RiskLevel.LOW)
        is RiskClass.HIGH_RISK
    )
    decision = PolicyEngine.decide(
        rollback(manifest_risk=RiskLevel.LOW), policy()
    )
    assert decision.outcome is Outcome.APPROVAL_REQUIRED
    assert decision.risk_class is RiskClass.HIGH_RISK


@pytest.mark.req("REQ-060")
def test_read_only_is_allowed_and_marked_as_never_mutating():
    decision = PolicyEngine.decide(diagnostic(), policy())
    assert decision.outcome is Outcome.ALLOW
    assert decision.risk_class is RiskClass.READ_ONLY
    assert "FLOOR-READ-ONLY-NO-MUTATE" in decision.floor_rules
    assert not decision.risk_class.mutating


@pytest.mark.req("REQ-060")
def test_a_mutating_class_can_never_be_classified_read_only():
    for action_class in (ActionClass.RESTART, ActionClass.OTHER):
        assert (
            classify(action_class, RiskLevel.LOW) is not RiskClass.READ_ONLY
        ), f"{action_class.value} must never be read-only"


@pytest.mark.req("REQ-060")
def test_app_policy_may_tighten_a_low_risk_action_into_approval():
    action = diagnostic(action_id="scale-cache", action_class=ActionClass.OTHER)
    allowed = PolicyEngine.decide(action, policy())
    assert allowed.outcome is Outcome.ALLOW

    tightened = PolicyEngine.decide(
        action,
        policy(
            action_rules={
                "scale-cache": ActionRule(
                    action_id="scale-cache",
                    require_approval=True,
                    note="the cache is shared with search",
                )
            }
        ),
    )
    assert tightened.outcome is Outcome.APPROVAL_REQUIRED
    assert any(
        hit.rule_id == "APP-REQUIRE-APPROVAL" for hit in tightened.rules_hit
    )


@pytest.mark.req("REQ-060")
def test_app_policy_may_block_outright():
    blocked = PolicyEngine.decide(
        diagnostic(),
        policy(
            action_rules={
                "dump-pool-stats": ActionRule(
                    action_id="dump-pool-stats", blocked=True, note="noisy"
                )
            }
        ),
    )
    assert blocked.outcome is Outcome.BLOCK
    assert any(hit.rule_id == "APP-BLOCKED-ACTION" for hit in blocked.rules_hit)


@pytest.mark.req("REQ-060")
def test_a_disabled_action_is_blocked_whatever_policy_says():
    decision = PolicyEngine.decide(diagnostic(enabled=False), policy())
    assert decision.outcome is Outcome.BLOCK
    assert "FLOOR-DISABLED-ACTION" in decision.floor_rules


@pytest.mark.req("REQ-060")
def test_the_default_policy_auto_executes_nothing():
    document = default_policy("t1", "payments", at=AT, author_id="system")
    assert document.version == 1
    assert all(not rule.auto_execute for rule in document.action_rules.values())
    assert document.approval_ttl_s <= MAX_APPROVAL_TTL_S


@pytest.mark.req("REQ-060")
def test_every_decision_carries_the_rules_that_produced_it():
    decision = PolicyEngine.decide(rollback(), policy())
    assert decision.rules_hit, "a decision with no rules_hit is unreviewable"
    assert decision.policy_ref == "payments@v1"
    assert decision.floor_version
    payload = decision.as_dict()
    assert payload["rules_hit"][0]["rule_id"]
    assert payload["rules_hit"][0]["source"] in {"floor", "app_policy", "manifest"}


@pytest.mark.req("REQ-060")
def test_a_policy_version_must_name_its_author():
    with pytest.raises(Exception) as raised:
        policy(author_id="")
    assert "author" in str(raised.value)
