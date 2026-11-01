"""Next best action and plan revision (REQ-063).

Two claims under test, and they are the sprint's whole position on what an AI
may decide:

1. **The ranking is arithmetic.** These tests run with no model configured at
   all - the default `api` fixture resolves its model settings from an empty
   environment - and the ordering, the four terms and the eligibility verdicts
   are all fully populated. A node whose gateway to the model is down produces
   the same recommendation.
2. **An impossible action is never the recommendation.** A blocked action, one
   nobody could approve and one whose prerequisites are unmet come back with the
   reason attached and can never be primary.
"""

from __future__ import annotations

import pytest

from datetime import datetime, timezone

from signalforge.domain.governance.nba import (
    Eligibility,
    Score,
    impact_of,
    information_gain_of,
    risk_of,
    urgency_of,
)
from signalforge.domain.governance.policy import RiskClass
from signalforge.contracts.canonical import ActionClass
from signalforge.tests.governance.conftest import (  # noqa: F401  (fixtures)
    APP_ID,
    governed,
    next_best_actions,
    put_policy,
    revise_plan,
)

pytestmark = pytest.mark.anyio


@pytest.mark.req("REQ-063")
def test_the_score_is_the_stated_formula():
    """urgency x impact x information gain - risk, and nothing else."""
    score = Score(urgency=1.0, impact=0.8, information_gain=0.5, risk=0.3)
    assert score.value == pytest.approx(1.0 * 0.8 * 0.5 - 0.3)
    assert score.as_dict()["score"] == score.value


@pytest.mark.req("REQ-063")
def test_the_terms_are_deterministic_functions_of_stored_facts():
    assert urgency_of("P0", customer_facing=True) == 1.0
    assert urgency_of("P2", customer_facing=False) == 0.4
    # A powerful action attached to a weak hypothesis is a weak action.
    strong = impact_of(ActionClass.ROLLBACK, hypothesis_confidence=0.9, customer_facing=False)
    weak = impact_of(ActionClass.ROLLBACK, hypothesis_confidence=0.2, customer_facing=False)
    assert strong > weak
    # Reversibility halves the toll; idempotency shaves it further.
    assert risk_of(RiskClass.HIGH_RISK, reversible=False, idempotent=False) == 0.6
    assert risk_of(RiskClass.HIGH_RISK, reversible=True, idempotent=False) == 0.3
    assert information_gain_of(ActionClass.DIAGNOSTIC) > information_gain_of(
        ActionClass.FAILOVER
    )


@pytest.mark.req("REQ-063")
async def test_the_ranking_is_produced_with_no_model_at_all(governed):
    incident = governed.declare()
    view = next_best_actions(
        governed.api, governed.requester_token, incident["id"], explain=False
    )
    assert view["degraded"] is True, "no model is configured in this suite"
    everything = (
        ([view["primary"]] if view["primary"] else [])
        + view["alternatives"]
        + view["ineligible"]
    )
    assert everything, "every published action should be a candidate"
    for candidate in everything:
        assert candidate["rationale"], "a deterministic sentence is always present"
        assert candidate["rationale_degraded"] is True
        assert set(candidate) >= {
            "score",
            "urgency",
            "impact",
            "information_gain",
            "risk",
            "policy",
            "requires_approval",
            "expected_validation_s",
            "rollback_summary",
        }


@pytest.mark.req("REQ-063")
async def test_an_action_nobody_could_approve_is_never_the_recommendation(governed):
    """`restart-checkout` names incident_commander and tech_lead; both exist.
    Blocking an action by policy is the cleaner way to prove the rule."""
    put_policy(
        governed.api,
        governed.owner_token,
        """
actions:
  collect-diagnostics:
    blocked: true
    note: "too noisy under load"
""",
    )
    incident = governed.declare()
    view = next_best_actions(
        governed.api, governed.requester_token, incident["id"], explain=False
    )
    blocked = [
        c for c in view["ineligible"] if c["action_id"] == "collect-diagnostics"
    ]
    assert blocked, "a blocked action is listed with its reason, not hidden"
    assert blocked[0]["eligibility"] == "blocked_by_policy"
    assert "too noisy" in blocked[0]["eligibility_reason"] or (
        "blocks" in blocked[0]["eligibility_reason"]
    )
    if view["primary"]:
        assert view["primary"]["action_id"] != "collect-diagnostics"


@pytest.mark.req("REQ-063")
async def test_an_unmet_prerequisite_makes_an_action_ineligible(governed):
    """The rollback declares `change_window_open`, and nothing has deployed."""
    incident = governed.declare()
    view = next_best_actions(
        governed.api, governed.requester_token, incident["id"], explain=False
    )
    rollback = next(
        c
        for c in view["ineligible"] + view["alternatives"]
        + ([view["primary"]] if view["primary"] else [])
        if c["action_id"] == "rollback-checkout"
    )
    assert rollback["eligibility"] == "missing_prerequisite"
    assert "change_window_open" in rollback["unmet_prerequisites"]
    assert rollback["eligible"] is False


@pytest.mark.req("REQ-063")
async def test_every_candidate_carries_its_rollback_and_validation_time(governed):
    incident = governed.declare()
    view = next_best_actions(
        governed.api, governed.requester_token, incident["id"], explain=False
    )
    everything = (
        ([view["primary"]] if view["primary"] else [])
        + view["alternatives"]
        + view["ineligible"]
    )
    rollback = next(c for c in everything if c["action_id"] == "rollback-checkout")
    assert rollback["reversible"] is True
    assert rollback["rollback_action_id"] == "roll-forward-checkout"
    # Two verification checks plus the stability window.
    assert rollback["expected_validation_s"] >= 900


@pytest.mark.req("REQ-063")
async def test_plan_revision_marks_kept_paused_and_added(governed):
    incident = governed.declare()
    first = revise_plan(
        governed.api, governed.requester_token, incident["id"], note="opening plan"
    )
    plan = first["plan"]
    assert plan["version"] == 1
    assert plan["added"], "the first plan adds everything eligible"
    assert plan["kept"] == []

    # The evidence changes: diagnostics are now blocked by policy.
    put_policy(
        governed.api,
        governed.owner_token,
        """
actions:
  collect-diagnostics:
    blocked: true
""",
        confirm_active_incident=True,
    )
    second = revise_plan(
        governed.api,
        governed.requester_token,
        incident["id"],
        changed_assumptions=["the heap dump is what is causing the pauses"],
        note="pausing diagnostics",
    )
    revised = second["plan"]
    assert revised["version"] == 2
    assert revised["previous_version"] == 1
    assert "collect-diagnostics" in revised["paused"]
    paused = next(
        item for item in revised["items"] if item["action_id"] == "collect-diagnostics"
    )
    assert paused["changed_assumption"] == (
        "the heap dump is what is causing the pauses"
    )
    # The previous version is still readable.
    assert second["previous_plan"]["version"] == 1


@pytest.mark.req("REQ-012", "REQ-063")
async def test_revising_a_plan_is_incident_command_work(governed):
    incident = governed.declare()
    signed = governed.api.invite_and_accept(
        governed.owner_token,
        email="sme@acme.io",
        roles=["sme"],
        display_name="Sam SME",
        password="sme-strong-password",
    )
    response = governed.api.client.post(
        f"/incidents/{incident['id']}/plan",
        json={},
        headers=governed.api.auth_header(signed["tokens"]["access_token"]),
    )
    assert response.status_code == 403, response.text

    # Reading it, though, is open to the room.
    next_best_actions(
        governed.api,
        signed["tokens"]["access_token"],
        incident["id"],
        explain=False,
    )


@pytest.mark.req("REQ-063")
def test_a_plan_is_ordered_under_the_same_threshold_as_the_recommendation():
    """Written failing-first. `revise_plan` ranked with the domain's default
    threshold while `recommend` used the application's policy, so a policy that
    said "act at 50%" produced a card recommending the rollback beside a plan
    whose first row was the diagnostic. One ordering, under one threshold."""
    from signalforge.domain.governance.nba import ActionCandidate, rank, revise_plan
    from signalforge.domain.governance.policy import Outcome, PolicyDecision

    def candidate(action_id: str, action_class: ActionClass, *, score: float, confidence: float):
        return ActionCandidate(
            action_id=action_id,
            name=action_id,
            action_class=action_class,
            risk_class=RiskClass.LOW_RISK,
            score=Score(urgency=1.0, impact=score, information_gain=1.0, risk=0.0),
            decision=PolicyDecision(
                outcome=Outcome.ALLOW,
                action_id=action_id,
                action_class=action_class,
                risk_class=RiskClass.LOW_RISK,
                reason="allowed",
            ),
            eligibility=Eligibility.ELIGIBLE,
            hypothesis_confidence=confidence,
        )

    diagnose = candidate("collect-diagnostics", ActionClass.DIAGNOSTIC, score=0.9, confidence=0.6)
    fix = candidate("rollback-release", ActionClass.ROLLBACK, score=0.5, confidence=0.6)
    candidates = (diagnose, fix)

    at = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)
    def plan(**kwargs):
        return revise_plan(
            None, candidates, plan_id="p1", tenant_id="t", incident_id="i",
            at=at, by="ic", **kwargs,
        )

    # At the default threshold the hypothesis is not established: the free
    # diagnostic leads. At 50% the rollback acts on a settled mechanism.
    assert [i.action_id for i in plan().items] == [c.action_id for c in rank(candidates)]
    assert [i.action_id for i in plan(act_threshold=0.5).items] == [
        c.action_id for c in rank(candidates, act_threshold=0.5)
    ]
    assert plan().items[0].action_id == "collect-diagnostics"
    assert plan(act_threshold=0.5).items[0].action_id == "rollback-release"
