"""Conversation understanding, deterministically (S5-T03, REQ-052).

The floor under the model classifier: what runs with no key, no budget and no
gateway. These tests also pin `reconcile`'s asymmetry - the property that a model
may promote an unmatched line and may never overturn an explicit one.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from signalforge.domain.brain.statements import (
    MachineFact,
    Statement,
    StatementKind,
    classify,
    contradictions,
    reconcile,
)

NOW = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)
SUBJECTS = ("checkout", "payments-db", "connection-pool")


def said(text, ref="m1", by="sam"):
    return classify(ref=ref, text=text, said_by=by, said_at=NOW, subjects=SUBJECTS)


@pytest.mark.req("REQ-052")
@pytest.mark.parametrize(
    "text,expected",
    [
        ("connection-pool is at 100% utilisation", StatementKind.FACT),
        ("checkout is returning errors", StatementKind.FACT),
        ("probably the deploy that went out at 14:02", StatementKind.ASSUMPTION),
        ("I think it is the pool", StatementKind.ASSUMPTION),
        ("should we roll back?", StatementKind.QUESTION),
        ("who has access to the console", StatementKind.QUESTION),
        ("we're rolling back region-b now", StatementKind.DECISION),
        ("I'll drain the queue in ten minutes", StatementKind.COMMITMENT),
        ("taking this one", StatementKind.COMMITMENT),
        ("I can't get into the console, no access", StatementKind.BLOCKER),
        ("blocked on the DBA", StatementKind.BLOCKER),
        ("ok", StatementKind.CHATTER),
        ("thanks all", StatementKind.CHATTER),
    ],
)
def test_the_deterministic_classifier_places_the_seven_kinds(text, expected):
    assert said(text).kind is expected


@pytest.mark.req("REQ-052")
def test_a_question_containing_a_decision_verb_is_still_a_question():
    """Decision order matters: "should we roll back?" contains a decision verb
    and is not a decision."""
    assert said("should we roll back region-b?").kind is StatementKind.QUESTION


@pytest.mark.req("REQ-052")
def test_a_hedged_measurement_is_an_assumption_not_a_fact():
    """"probably 100% pool usage" is an assumption about a number."""
    assert said("probably 100% on the pool").kind is StatementKind.ASSUMPTION
    assert said("pool is at 100%").kind is StatementKind.FACT


@pytest.mark.req("REQ-052")
def test_a_subject_is_only_matched_against_the_caller_s_own_list():
    """Guessing a subject out of free text is how "the pool" becomes a service
    called Pool."""
    assert said("checkout is failing").subject == "checkout"
    assert said("the widget is failing").subject == ""


@pytest.mark.req("REQ-052")
def test_the_model_may_promote_an_unmatched_line():
    """The indirect commitment no honest regular expression will catch."""
    base = said("Sam, can you check the pool?")
    assert base.kind is StatementKind.QUESTION

    promoted = reconcile(
        base, kind=StatementKind.COMMITMENT, owner_hint="Sam", why="asked directly"
    )
    assert promoted.kind is StatementKind.COMMITMENT
    assert promoted.owner_hint == "Sam"
    assert promoted.by_model
    assert promoted.text == base.text
    assert promoted.detail["deterministic_was"] == "question"


@pytest.mark.req("REQ-052")
def test_the_model_may_not_overturn_an_explicit_deterministic_call():
    """A model second-guessing an explicit promise would lose a task somebody
    actually made."""
    base = said("I'll roll back region-b in ten minutes")
    assert base.kind is StatementKind.COMMITMENT

    overruled = reconcile(base, kind=StatementKind.CHATTER)
    assert overruled.kind is StatementKind.COMMITMENT
    assert not overruled.by_model
    assert overruled.detail["model_overruled"] is True
    assert overruled.detail["model_proposed"] == "chatter"


@pytest.mark.req("REQ-052")
def test_agreement_keeps_the_model_s_extracted_subject_and_owner():
    base = said("I'll drain the queue")
    agreed = reconcile(
        base, kind=StatementKind.COMMITMENT, subject="checkout", owner_hint="sam"
    )
    assert agreed.kind is StatementKind.COMMITMENT
    assert agreed.subject == "checkout"
    assert agreed.detail["agreed_with"] == "deterministic"


@pytest.mark.req("REQ-052")
def test_statement_keys_are_stable_and_distinguish_a_disagreement():
    one = said("pool is at 100%", ref="m9")
    same = said("pool is at 100%", ref="m9")
    assert one.key == same.key

    promoted = reconcile(said("Sam, can you check?", ref="m9"), kind=StatementKind.COMMITMENT)
    assert promoted.key != said("Sam, can you check?", ref="m9").key


@pytest.mark.req("REQ-052")
def test_a_human_claiming_health_against_failing_telemetry_is_a_contradiction():
    """The acceptance case: "DB is healthy" against "DB connectivity failing"."""
    claim = said("payments-db is fine, no issues")
    facts = (
        MachineFact(
            ref="signal:payments-db:eu",
            subject="payments-db",
            healthy=False,
            detail="connectivity probe failing in eu",
        ),
    )
    found = contradictions([claim], facts)
    assert len(found) == 1
    assert "healthy" in found[0].detail
    assert found[0].fact.ref == "signal:payments-db:eu"
    assert found[0].key.startswith("contra-")


@pytest.mark.req("REQ-052")
def test_a_human_claiming_failure_against_healthy_telemetry_is_also_a_contradiction():
    claim = said("payments-db is down")
    facts = (MachineFact(ref="s1", subject="payments-db", healthy=True, detail="green"),)
    found = contradictions([claim], facts)
    assert len(found) == 1
    assert "is failing" in found[0].detail


@pytest.mark.req("REQ-052")
def test_no_contradiction_without_a_subject_or_without_a_health_claim():
    """A war room that cried contradiction every third message would be turned
    off by minute ten."""
    facts = (MachineFact(ref="s1", subject="payments-db", healthy=False),)
    assert contradictions([said("everything is fine")], facts) == ()
    assert contradictions([said("payments-db was deployed at 14:02")], facts) == ()
    # Questions and commitments are not claims about the world.
    assert contradictions([said("is payments-db fine?")], facts) == ()


@pytest.mark.req("REQ-052")
def test_a_statement_records_which_classifier_decided():
    rule = said("pool is at 100%")
    assert rule.classifier == "deterministic"
    assert not rule.by_model
    model = reconcile(said("hmm"), kind=StatementKind.FACT)
    assert model.by_model


@pytest.mark.req("REQ-052")
def test_questions_and_chatter_do_not_enter_the_graph():
    """A graph that recorded every question asked would bury the answers."""
    assert not StatementKind.QUESTION.enters_graph
    assert not StatementKind.CHATTER.enters_graph
    for kind in (
        StatementKind.FACT,
        StatementKind.ASSUMPTION,
        StatementKind.DECISION,
        StatementKind.COMMITMENT,
        StatementKind.BLOCKER,
        StatementKind.CONTRADICTION,
    ):
        assert kind.enters_graph
