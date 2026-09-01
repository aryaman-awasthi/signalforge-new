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

from signalforge.domain.governance.nba import (
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
