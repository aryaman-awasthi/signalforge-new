"""Per-row countdowns and next-in-rotation escalation (S4-T03, REQ-045).

The clock is frozen and moved by hand, because every assertion in this file is
about time. A countdown that has run out, a page unanswered for five minutes, a
second person woken because the first did not answer - none of them can be tested
by waiting, and all of them are exactly what a drill at 100x does to the same
code with no drill-aware branch anywhere in it.

The acceptance clause this file holds is the sprint's: *a second browser session
(SME) receives the page, non-ack escalates on the visible countdown*. The
"visible countdown" is asserted as the number the API actually publishes, not as
an internal timer, because the visible one is the one somebody is watching.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from signalforge.domain.identity.oncall import OnCallRotation
from signalforge.domain.onboarding.profile import Severity
from signalforge.domain.warroom.errors import EscalationExhaustedError
from signalforge.domain.warroom.paging import (
    DEFAULT_ACK_TIMEOUT_S,
    PAGE_TRANSITIONS,
    Page,
    PageState,
    channels_for,
    next_target,
)
from signalforge.tests.warroom.conftest import (
    APP_ID,
    ack,
    commander,
    declare,
    escalate,
    my_pages,
    onboard,
    owner,
    page_for,
    pages,
    raise_page,
    relogin,
    responder,
    rotate,
    topics,
    war_room,
    wire_paging,
)

pytestmark = pytest.mark.anyio


def rotation(*user_ids: str) -> OnCallRotation:
    return OnCallRotation(
        id="rot-1",
        tenant_id="t1",
        name="payments primary",
        user_ids=tuple(user_ids),
    )


def page(**overrides) -> Page:
    from datetime import datetime, timezone

    fields = {
        "id": "p1",
        "tenant_id": "t1",
        "incident_id": "i1",
        "app_id": APP_ID,
        "recipient": "u1",
        "role": None,
        "reason": "on call",
        "state": PageState.SENT,
        "created_at": datetime(2026, 9, 1, 3, 0, tzinfo=timezone.utc),
    }
    fields.update(overrides)
    return Page(**fields)


# --- the state machine --------------------------------------------------------


@pytest.mark.req("REQ-045")
def test_every_terminal_page_state_is_terminal():
    """Pinned as a literal, so adding an edge out of one becomes a decision.

    `failed` in particular: a page that never reached anybody must not later
    report that somebody answered it, or an escalation could be silenced by a
    delivery that did not happen.
    """
    for state in (
        PageState.ACKED,
        PageState.ESCALATED,
        PageState.FAILED,
        PageState.CANCELLED,
    ):
        assert PAGE_TRANSITIONS[state] == frozenset(), (
            f"{state.value} must be terminal"
        )
        assert state.settled is True
    assert PageState.PENDING.awaiting and PageState.SENT.awaiting


@pytest.mark.req("REQ-045")
def test_a_countdown_is_arithmetic_over_stored_times():
    """No timer is held anywhere. The board, the sweep and a phone opened ten
    minutes later all compute the same number from the same two timestamps."""
    from datetime import datetime, timezone

    at = datetime(2026, 9, 1, 3, 0, tzinfo=timezone.utc)
    sent = page(state=PageState.PENDING, ack_timeout_s=300).sent(at=at, deliveries=())
    assert sent.ack_deadline_at == at + timedelta(seconds=300)
    assert sent.countdown_s(at) == 300
    assert sent.countdown_s(at + timedelta(seconds=120)) == 180
    assert sent.overdue(at + timedelta(seconds=299)) is False
    assert sent.countdown_s(at + timedelta(seconds=400)) == -100
    assert sent.overdue(at + timedelta(seconds=400)) is True

    # Settled pages have no countdown at all, rather than a frozen one.
    answered = sent.acknowledged(at=at + timedelta(seconds=10), by="u1")
    assert answered.countdown_s(at + timedelta(seconds=400)) is None
    assert answered.overdue(at + timedelta(seconds=400)) is False


@pytest.mark.req("REQ-045")
def test_a_p0_rings_a_phone_and_a_p3_does_not():
    """Waking somebody at 03:00 for a P3 is how a pager gets muted, and a muted
    pager is how a P0 is missed."""
    p0 = {channel.value for channel in channels_for(Severity.P0)}
    p3 = {channel.value for channel in channels_for(Severity.P3)}
    assert {"voice", "sms"} <= p0
    assert not ({"voice", "sms"} & p3)
    assert "in_app" in p3


# --- who is next --------------------------------------------------------------


@pytest.mark.req("REQ-045")
def test_escalation_walks_the_rotation_and_skips_who_has_been_paged():
    """A three-person rotation escalates twice and then stops, rather than
    ringing the first person again at hop three."""
    roster = rotation("ada", "leo", "sam")
    first = page(recipient="ada", rotation_id="rot-1")

    second = next_target(first, rotation=roster, policy=None, already_paged=["ada"])
    assert second.user_id == "leo"
    assert "rotation" in second.reason

    third = next_target(
        page(recipient="leo", rotation_id="rot-1", hop=1),
        rotation=roster,
        policy=None,
        already_paged=["ada", "leo"],
    )
    assert third.user_id == "sam"

    with pytest.raises(EscalationExhaustedError) as exhausted:
        next_target(
            page(recipient="sam", rotation_id="rot-1", hop=2),
            rotation=roster,
            policy=None,
            already_paged=["ada", "leo", "sam"],
        )
    assert "none acknowledged" in str(exhausted.value)


@pytest.mark.req("REQ-045")
def test_running_out_of_people_raises_rather_than_stopping_quietly():
    """The moment nobody is left to page is the moment a human has to be told.

    A silent no-op here is how "nobody answered the P0 pager" becomes nobody's
    problem until the post-incident review.
    """
    with pytest.raises(EscalationExhaustedError):
        next_target(page(), rotation=None, policy=None, candidates=[])

    # And the hop ceiling is a stop, not a loop.
    with pytest.raises(EscalationExhaustedError) as exc:
        next_target(
            page(hop=10),
            rotation=rotation("a", "b", "c"),
            policy=None,
            candidates=["z"],
        )
    assert "ceiling" in str(exc.value)


@pytest.mark.req("REQ-045")
def test_with_no_rotation_configured_another_holder_of_the_role_is_paged():
    """The last resort, so an org with a bound tech lead and no roster still
    escalates to somebody rather than stopping at the first unanswered page."""
    target = next_target(
        page(recipient="ada"), rotation=None, policy=None, candidates=["leo", "sam"]
    )
    assert target.user_id == "leo"
    assert "no rotation or escalation policy" in target.reason


# --- through the real API -----------------------------------------------------


@pytest.mark.req("REQ-045")
async def test_declaring_pages_the_room_and_the_page_carries_a_visible_countdown(api):
    owner_token, _tenant = owner(api)
    onboard(api, owner_token)
    wire_paging(api, owner_token)
    ic = commander(api, owner_token)
    sme = responder(api, owner_token, email="sme@acme.io", display_name="Sam SME")
    incident = declare(api, ic["tokens"]["access_token"], severity="P0")

    board = pages(api, ic["tokens"]["access_token"], incident["id"])
    assert board["count"] >= 1
    row = page_for({"pages": board["pages"]}, sme["user"]["id"])
    assert row is not None
    assert row["state"] == "sent"
    assert row["countdown_s"] == pytest.approx(DEFAULT_ACK_TIMEOUT_S, abs=1)
    assert row["overdue"] is False
    assert row["reached"] is True
    # A P0 fans out across every channel the bound connectors carry.
    channels = {delivery["channel"] for delivery in row["deliveries"]}
    assert {"sms", "voice", "in_app"} <= channels

    # And it is on the responder's own pager.
    mine = my_pages(api, sme["tokens"]["access_token"])
    assert [item["id"] for item in mine["pages"]] == [row["id"]]


@pytest.mark.req("REQ-045")
async def test_an_unanswered_page_escalates_on_the_visible_countdown(api):
    """The sprint's acceptance clause, driven end to end.

    Nobody waits: the clock is advanced past the deadline the API published, and
    the shipped escalation sweep - the same job the worker runs - is run once.
    """
    owner_token, _tenant = owner(api)
    onboard(api, owner_token)
    wire_paging(api, owner_token)
    ic = commander(api, owner_token)
    first = responder(api, owner_token, email="first@acme.io", display_name="Ada First")
    # Second in the rotation but not in the blast radius, so the only page
    # addressed to them is the one this escalation produces. That keeps the
    # assertion about *this* chain rather than about who else the room woke.
    second = responder(
        api,
        owner_token,
        email="second@acme.io",
        roles=("auditor",),
        display_name="Leo Second",
    )
    rotate(api, owner_token, first["user"]["id"], second["user"]["id"])

    incident = declare(api, ic["tokens"]["access_token"], severity="P0")
    board = pages(api, ic["tokens"]["access_token"], incident["id"])
    original = page_for({"pages": board["pages"]}, first["user"]["id"])
    assert original is not None and original["countdown_s"] > 0
    assert page_for({"pages": board["pages"]}, second["user"]["id"]) is None

    # Nothing escalates while the countdown is still running.
    api.clock.advance(seconds=original["countdown_s"] - 10)
    quiet = await api.container.paging.sweep()
    assert quiet.considered == 0 and quiet.escalated == 0

    # Past the deadline it does, to the next person in the rotation. Everybody
    # the radius paged is now overdue, so the sweep does more than one thing;
    # what this test is about is what happened to *this* page.
    api.clock.advance(seconds=30)
    swept = await api.container.paging.sweep()
    assert swept.escalated >= 1, str(swept)

    token = relogin(api)
    board = pages(api, token, incident["id"])
    rows = {row["recipient"]: row for row in board["pages"]}
    assert rows[first["user"]["id"]]["state"] == "escalated"
    successor = rows[second["user"]["id"]]
    assert successor["hop"] == 1
    assert successor["state"] == "sent"
    assert successor["escalated_from_page_id"] == original["id"]
    assert successor["countdown_s"] > 0, "the successor gets its own countdown"

    published = [
        event
        for event in topics(api, token, "warroom.page.escalated")
        if event["payload"]["page_id"] == original["id"]
    ]
    assert len(published) == 1, "an escalation is on the incident timeline, once"
    assert published[0]["payload"]["escalated_to"] == second["user"]["id"]
    assert published[0]["payload"]["by_human"] is False
    assert published[0]["payload"]["unanswered_for_s"] >= original["ack_timeout_s"]

    # The escalated-to person is now in the room, marked as paged.
    room = war_room(api, token, incident["id"])
    joined = {member["user_id"]: member for member in room["members"]}
    assert joined[second["user"]["id"]]["paged"] is True


@pytest.mark.req("REQ-045")
async def test_an_acknowledged_page_never_escalates(api):
    """The human override wins the race against the countdown, permanently."""
    owner_token, _tenant = owner(api)
    onboard(api, owner_token)
    wire_paging(api, owner_token)
    ic = commander(api, owner_token)
    sme = responder(api, owner_token, email="sme@acme.io")
    incident = declare(api, ic["tokens"]["access_token"], severity="P0")

    board = pages(api, ic["tokens"]["access_token"], incident["id"])
    row = page_for({"pages": board["pages"]}, sme["user"]["id"])
    answered = ack(
        api, sme["tokens"]["access_token"], incident["id"], row["id"], note="on it"
    )
    assert answered["state"] == "acked" and answered["countdown_s"] is None

    api.clock.advance(seconds=DEFAULT_ACK_TIMEOUT_S * 4)
    async with api.container.uow_factory() as uow:
        work_list = await uow.incident_pages.overdue_across_tenants(
            api.clock.now(), 100
        )
    assert row["id"] not in {item.id for item in work_list}, (
        "an acknowledged page is not in the escalation sweep's work list at all"
    )
    await api.container.paging.sweep()

    token = relogin(api)
    escalations = [
        event
        for event in topics(api, token, "warroom.page.escalated")
        if event["payload"]["page_id"] == row["id"]
    ]
    assert not escalations, "an answered page never escalates, however long it waits"
    board = pages(api, token, incident["id"])
    assert page_for({"pages": board["pages"]}, sme["user"]["id"])["state"] == "acked"
    # Answering a page puts you in the room.
    room = war_room(api, token, incident["id"])
    present = {member["user_id"] for member in room["members"] if member["present"]}
    assert sme["user"]["id"] in present


@pytest.mark.req("REQ-045")
async def test_a_commander_can_escalate_before_the_countdown_ends(api):
    """A human override of the cadence, recorded as a human act."""
    owner_token, _tenant = owner(api)
    onboard(api, owner_token)
    wire_paging(api, owner_token)
    ic = commander(api, owner_token)
    ic_token = ic["tokens"]["access_token"]
    first = responder(api, owner_token, email="first@acme.io")
    second = responder(api, owner_token, email="second@acme.io", display_name="Leo")
    incident = declare(api, ic_token, severity="P0")

    board = pages(api, ic_token, incident["id"])
    row = page_for({"pages": board["pages"]}, first["user"]["id"])
    assert row["countdown_s"] > 0, "still ringing"

    successor = escalate(api, ic_token, incident["id"], row["id"])
    assert successor["hop"] == 1
    assert successor["recipient"] == second["user"]["id"]

    published = topics(api, owner_token, "warroom.page.escalated")
    assert published[0]["payload"]["by_human"] is True
    assert published[0]["actor"]["id"] == ic["user"]["id"]

    # And an already-escalated page cannot be escalated again.
    escalate(api, ic_token, incident["id"], row["id"], expect=409)


@pytest.mark.req("REQ-045")
async def test_escalation_that_runs_out_of_people_is_loud(api):
    """Nobody left to page is a finding, published before it is an error."""
    owner_token, _tenant = owner(api)
    onboard(api, owner_token)
    wire_paging(api, owner_token)
    ic = commander(api, owner_token)
    only = responder(api, owner_token, email="only@acme.io")
    incident = declare(api, ic["tokens"]["access_token"], severity="P0")

    board = pages(api, ic["tokens"]["access_token"], incident["id"])
    row = page_for({"pages": board["pages"]}, only["user"]["id"])
    api.clock.advance(seconds=row["countdown_s"] + 30)
    swept = await api.container.paging.sweep()
    assert swept.exhausted >= 1 and swept.escalated == 0, str(swept)

    token = relogin(api)
    published = [
        event
        for event in topics(api, token, "warroom.page.escalation.exhausted")
        if event["payload"]["page_id"] == row["id"]
    ]
    assert published, "running out of people is on the timeline"
    assert published[0]["payload"]["needs_human"] is True
    # The page is left ringing rather than quietly closed: nobody answered, and
    # the board has to keep saying so.
    board = pages(api, token, incident["id"])
    assert page_for({"pages": board["pages"]}, only["user"]["id"])["state"] == "sent"


@pytest.mark.req("REQ-045", "REQ-020")
async def test_closing_an_incident_stops_ringing_phones_but_not_answered_ones(api):
    """What somebody took responsibility for is not un-taken by the incident
    ending; what is still ringing for a closed incident is noise."""
    from signalforge.tests.warroom.conftest import close_incident

    owner_token, _tenant = owner(api)
    onboard(api, owner_token)
    wire_paging(api, owner_token)
    ic = commander(api, owner_token)
    ic_token = ic["tokens"]["access_token"]
    answered_by = responder(api, owner_token, email="ada@acme.io")
    ringing_for = responder(
        api, owner_token, email="leo@acme.io", roles=("tech_lead",), display_name="Leo"
    )
    incident = declare(api, ic_token, severity="P0")

    board = pages(api, ic_token, incident["id"])
    mine = page_for({"pages": board["pages"]}, answered_by["user"]["id"])
    ack(api, answered_by["tokens"]["access_token"], incident["id"], mine["id"])

    close_incident(api, ic_token, incident["id"], reason="mitigated and validated")

    board = pages(api, ic_token, incident["id"])
    rows = {row["recipient"]: row for row in board["pages"]}
    assert rows[answered_by["user"]["id"]]["state"] == "acked"
    assert rows[ringing_for["user"]["id"]]["state"] == "cancelled"
    # And nothing escalates for a closed incident afterwards.
    api.clock.advance(seconds=DEFAULT_ACK_TIMEOUT_S * 3)
    assert (await api.container.paging.sweep()).considered == 0


@pytest.mark.req("REQ-045", "REQ-033")
async def test_a_page_nobody_could_be_reached_through_fails_rather_than_counting_down(
    api,
):
    """No notification connector is bound, so nobody was reached.

    A countdown here would be a lie: five minutes of a timer nobody is watching,
    followed by an escalation to a channel that also does not exist. The page is
    `failed`, the reason is on the row, and the board says so at once.
    """
    owner_token, _tenant = owner(api)
    onboard(api, owner_token)  # deliberately no wire_paging
    ic = commander(api, owner_token)
    sme = responder(api, owner_token, email="sme@acme.io")
    incident = declare(api, ic["tokens"]["access_token"], severity="P0")

    board = pages(api, ic["tokens"]["access_token"], incident["id"])
    row = page_for({"pages": board["pages"]}, sme["user"]["id"])
    assert row["state"] == "failed"
    assert row["reached"] is False
    assert row["countdown_s"] is None
    assert "no notification connector" in row["detail"]

    published = topics(api, owner_token, "warroom.page.failed")
    assert published, "a page that reached nobody is on the timeline"


@pytest.mark.req("REQ-045")
async def test_a_commander_can_page_somebody_the_radius_did_not_call_for(api):
    owner_token, _tenant = owner(api)
    onboard(api, owner_token)
    wire_paging(api, owner_token)
    ic = commander(api, owner_token)
    ic_token = ic["tokens"]["access_token"]
    dba = responder(
        api, owner_token, email="dba@acme.io", roles=("auditor",), display_name="Dee DBA"
    )
    incident = declare(api, ic_token, severity="P1")

    room = war_room(api, ic_token, incident["id"])
    assert page_for(room, dba["user"]["id"]) is None

    raised = raise_page(
        api,
        ic_token,
        incident["id"],
        user_id=dba["user"]["id"],
        reason="knows the connection pool",
        ack_timeout_s=60,
    )
    assert raised["state"] == "sent"
    assert raised["ack_timeout_s"] == 60
    assert raised["countdown_s"] == pytest.approx(60, abs=1)
    # And the person paged can answer it, holding read_audit and nothing else.
    ack(api, dba["tokens"]["access_token"], incident["id"], raised["id"])
