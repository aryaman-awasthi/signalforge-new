"""The floor: only a human declares, changes a severity or closes (REQ-043).

Written failing-first. This is the policy half of the lifecycle and it is
checked at three depths, because a rule that exists in only one of them is a
rule the next caller walks around:

- in the **domain**, where `assert_transition` refuses a system actor outright,
  so a future agent, drill or automation meets the same refusal a service does;
- in the **API**, where a session without `declare_incident` gets a clean 403
  from the frozen roles matrix and the denial lands in the audit chain;
- on the **timeline**, where a declaration is an incident-plane event with a
  human actor and an incident id - which is exactly what detection's events can
  never be (they are signal-plane, system-actor, incident_id null).
"""

from __future__ import annotations

import pytest

from signalforge.contracts.envelope import ActorType
from signalforge.domain.incidents.errors import (
    HumanOnlyTransitionError,
    IllegalTransitionError,
)
from signalforge.domain.incidents.lifecycle import (
    HUMAN_ONLY,
    LifecycleState,
    assert_severity_change,
    assert_transition,
)
from signalforge.tests.incidents.conftest import (
    APP_ID,
    close_incident,
    commander,
    declare,
    lifecycle,
    onboard,
    owner,
    read_incident,
    set_severity,
    set_state,
    topics,
)

pytestmark = pytest.mark.anyio


# --- the domain floor ---------------------------------------------------------


@pytest.mark.req("REQ-043")
def test_a_machine_can_reach_proposed_and_no_further():
    """Detection's whole range. Every stance-to-stance move is open to a system
    actor; the move that creates an incident is not."""
    for target in (LifecycleState.WATCH, LifecycleState.PROPOSED):
        assert_transition(
            LifecycleState.STEADY, target, by=ActorType.SYSTEM
        )
    assert_transition(LifecycleState.PROPOSED, LifecycleState.STEADY, by=ActorType.SYSTEM)

    for stance in (LifecycleState.STEADY, LifecycleState.WATCH, LifecycleState.PROPOSED):
        with pytest.raises(HumanOnlyTransitionError) as refused:
            assert_transition(stance, LifecycleState.ACTIVE, by=ActorType.SYSTEM)
        assert "only a person" in str(refused.value)
        # An agent is not a person either, and neither is an external system.
        for actor in (ActorType.AGENT, ActorType.EXTERNAL):
            with pytest.raises(HumanOnlyTransitionError):
                assert_transition(stance, LifecycleState.ACTIVE, by=actor)
        assert_transition(stance, LifecycleState.ACTIVE, by=ActorType.HUMAN)


@pytest.mark.req("REQ-043")
def test_a_machine_can_escalate_a_recovery_but_never_end_one():
    """The single move a machine may make inside an incident is the escalating
    one: a regression during validation returns it to active."""
    assert_transition(
        LifecycleState.RECOVERING, LifecycleState.ACTIVE, by=ActorType.SYSTEM
    )
    for start in (LifecycleState.ACTIVE, LifecycleState.RECOVERING):
        with pytest.raises(HumanOnlyTransitionError):
            assert_transition(start, LifecycleState.CLOSED, by=ActorType.SYSTEM)
    with pytest.raises(HumanOnlyTransitionError):
        assert_transition(
            LifecycleState.ACTIVE, LifecycleState.RECOVERING, by=ActorType.SYSTEM
        )


@pytest.mark.req("REQ-043")
def test_closed_is_terminal_for_everyone():
    """Closure records that a person signed off. Nothing un-signs it - not a
    machine, and not another person."""
    for actor in ActorType:
        for target in LifecycleState:
            if target is LifecycleState.CLOSED:
                continue
            with pytest.raises(IllegalTransitionError) as refused:
                assert_transition(LifecycleState.CLOSED, target, by=actor)
            assert "closure is final" in str(refused.value)


@pytest.mark.req("REQ-043")
def test_severity_is_a_human_act_on_an_open_incident():
    assert_severity_change(LifecycleState.ACTIVE, by=ActorType.HUMAN)
    assert_severity_change(LifecycleState.RECOVERING, by=ActorType.HUMAN)
    with pytest.raises(HumanOnlyTransitionError):
        assert_severity_change(LifecycleState.ACTIVE, by=ActorType.SYSTEM)
    with pytest.raises(IllegalTransitionError):
        assert_severity_change(LifecycleState.CLOSED, by=ActorType.HUMAN)


@pytest.mark.req("REQ-043")
def test_the_human_only_set_is_every_move_that_creates_relaxes_or_ends_an_incident():
    """Stated as a set so that adding a transition without deciding who may make
    it is a failing test rather than a silent grant."""
    assert HUMAN_ONLY == {
        (LifecycleState.STEADY, LifecycleState.ACTIVE),
        (LifecycleState.WATCH, LifecycleState.ACTIVE),
        (LifecycleState.PROPOSED, LifecycleState.ACTIVE),
        (LifecycleState.ACTIVE, LifecycleState.RECOVERING),
        (LifecycleState.ACTIVE, LifecycleState.CLOSED),
        (LifecycleState.RECOVERING, LifecycleState.CLOSED),
    }


# --- the API floor ------------------------------------------------------------


@pytest.mark.req("REQ-043", "REQ-012")
async def test_the_org_owner_cannot_declare_and_the_commander_can(api):
    """Separation of duties, straight out of the frozen matrix: manage_apps wires
    applications up, incident command runs incidents, and neither is the other."""
    owner_token, tenant_id = owner(api)
    onboard(api, owner_token)

    refused = api.client.post(
        f"/incidents?app_id={APP_ID}",
        json={"title": "I wired it so I will declare it"},
        headers=api.auth_header(owner_token),
    )
    assert refused.status_code == 403, refused.text
    assert refused.json()["permission"] == "declare_incident"
    assert any(
        row["topic"] == "audit.authz.denied"
        and row["payload"]["permission"] == "declare_incident"
        for row in api.audit_rows(tenant_id)
    ), "a denial is evidence"

    ic = commander(api, owner_token)
    incident = declare(api, ic["tokens"]["access_token"], severity="P1")
    assert incident["state"] == "active"
    assert incident["declared_by"] == ic["user"]["id"]


@pytest.mark.req("REQ-043", "REQ-012")
async def test_severity_and_closure_are_separately_permissioned(api):
    """A tech lead may declare and re-grade; only incident command closes."""
    owner_token, _tenant = owner(api)
    onboard(api, owner_token)
    lead = api.invite_and_accept(
        owner_token, email="lead@acme.io", roles=["tech_lead"], display_name="Lee"
    )
    lead_token = lead["tokens"]["access_token"]

    incident = declare(api, lead_token, severity="P2")
    assert set_severity(api, lead_token, incident["id"], "P0")["severity"] == "P0"
    close_incident(api, lead_token, incident["id"], expect=403)

    ic = commander(api, owner_token)
    closed = close_incident(
        api, ic["tokens"]["access_token"], incident["id"], reason="mitigated"
    )
    assert closed["state"] == "closed"
    assert closed["closed_by"] == ic["user"]["id"]


@pytest.mark.req("REQ-043")
async def test_a_declaration_is_an_incident_plane_event_naming_a_person(api):
    owner_token, _tenant = owner(api)
    onboard(api, owner_token)
    ic = commander(api, owner_token)
    token = ic["tokens"]["access_token"]

    incident = declare(api, token, severity="P0")
    # Replay is read_audit work, which the commander deliberately does not hold.
    events = topics(api, owner_token, "incident.declared")
    assert len(events) == 1
    event = events[0]
    assert event["plane"] == "incident", "an incident is kept and sealed, not aged out"
    assert event["actor"]["type"] == "human"
    assert event["actor"]["id"] == ic["user"]["id"]
    assert event["incident_id"] == incident["id"]
    assert event["source"]["authority"] == "authoritative"
    assert event["payload"]["declared"] is True
    assert event["payload"]["severity"] == "P0"


@pytest.mark.req("REQ-043")
async def test_the_lifecycle_view_says_a_stance_is_not_an_incident(api):
    """Before anybody declares, the application is in a detection stance and the
    view says who would have to act to change that."""
    owner_token, _tenant = owner(api)
    onboard(api, owner_token)

    view = lifecycle(api, owner_token)
    assert view["state"] == "steady"
    assert view["source"] == "detection"
    assert view["incident"] is None
    assert view["declared"] is False
    assert view["declare_requires_human"] is True
    assert "active" in view["next_states"]


@pytest.mark.req("REQ-043")
async def test_a_person_may_declare_against_a_steady_stance_and_it_is_recorded_as_an_override(
    api,
):
    """Human override. The detector said nothing was wrong; a person who knows
    better declares anyway, and the record says the machine was not the source."""
    owner_token, _tenant = owner(api)
    onboard(api, owner_token)
    ic = commander(api, owner_token)
    token = ic["tokens"]["access_token"]

    incident = declare(api, token, severity="P1", reason="a customer rang us")
    assert incident["stance_at_declare"] == "steady"
    assert incident["severity_source"] == "chosen"
    assert incident["overrode_detection"] is True
    assert incident["proposal"]["severity"] is None

    detail = read_incident(api, token, incident["id"])
    assert detail["transitions"][0]["from_state"] == "steady"
    assert detail["transitions"][0]["to_state"] == "active"
    assert detail["transitions"][0]["actor_type"] == "human"
    assert detail["transitions"][0]["reason"] == "a customer rang us"


@pytest.mark.req("REQ-043")
async def test_an_illegal_move_is_refused_with_the_moves_that_are_legal(api):
    owner_token, _tenant = owner(api)
    onboard(api, owner_token)
    ic = commander(api, owner_token)
    token = ic["tokens"]["access_token"]
    incident = declare(api, token, severity="P2")

    # active -> active is not a move.
    response = api.client.post(
        f"/incidents/{incident['id']}/state",
        json={"state": "active"},
        headers=api.auth_header(token),
    )
    assert response.status_code == 409, response.text
    assert "already active" in response.json()["detail"]

    # A stance is not something a person sets on an incident.
    watch = set_state(api, token, incident["id"], "watch", expect=409)
    assert "not a state this route may set" in watch["detail"]

    set_state(api, token, incident["id"], "recovering")
    close_incident(api, token, incident["id"], reason="validated")
    again = close_incident(api, token, incident["id"], expect=409)
    assert "closure is final" in again["detail"]
