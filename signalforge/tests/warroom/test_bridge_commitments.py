"""The bridge, and commitments becoming tasks with an owner and a due time
(S4-T03, REQ-045).

The pure half is tested as pure code, because the value of a conservative
harvester is entirely in what it *refuses*: a question is not a commitment, a
forecast about a service is not a commitment, and a capitalised word is not
necessarily a person. Those are the assertions that keep the board trustworthy,
and a board people stop trusting is worse than no board.

The wired half goes through the shipped teams-sim collaboration adapter over the
real `ConnectorInvoker`, so what is under test is the same path a real bridge
takes: a connector bound through `POST /connectors`, a message posted through the
frozen SPI, and the harvest job the worker actually runs.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from signalforge.domain.warroom.commitments import harvest
from signalforge.tests.warroom.conftest import (
    APP_ID,
    commander,
    declare,
    onboard,
    owner,
    read_bridge,
    relogin,
    responder,
    say,
    tasks,
    topics,
    war_room,
    wire_bridge,
    wire_paging,
)

pytestmark = pytest.mark.anyio

NOW = datetime(2026, 9, 1, 3, 0, tzinfo=timezone.utc)


def line(text: str, *, author: str = "ada", message_id: str = "m1") -> dict:
    return {"message_id": message_id, "author": author, "text": text, "at": NOW}


def found(*texts: str, **kwargs) -> tuple:
    return harvest(
        [line(text, message_id=f"m{index}") for index, text in enumerate(texts)],
        now=NOW,
        **kwargs,
    )


# --- what it finds ------------------------------------------------------------


@pytest.mark.req("REQ-045")
def test_a_first_person_promise_becomes_a_task_owned_by_the_speaker():
    (commitment,) = found("I'll roll back region-b in 10 minutes")
    assert commitment.title == "roll back region-b"
    assert commitment.first_person is True
    assert commitment.owner_is_speaker is True
    assert commitment.due_at == NOW + timedelta(minutes=10)
    assert commitment.due_label == "in 10 minutes"
    # The sentence is kept verbatim: a task nobody remembers agreeing to is
    # settled by reading what was actually said.
    assert commitment.evidence == "I'll roll back region-b in 10 minutes"


@pytest.mark.req("REQ-045")
def test_a_named_person_becomes_a_label_and_never_a_guessed_owner():
    """The rule that keeps the wrong person from being paged at 03:00."""
    (commitment,) = found("Priya will check the connection pool by 03:30")
    assert commitment.title == "check the connection pool"
    assert commitment.owner_hint == "Priya"
    assert commitment.first_person is False
    assert commitment.due_at == NOW.replace(hour=3, minute=30)
    assert commitment.due_label == "by 03:30"


@pytest.mark.req("REQ-045")
def test_the_bridge_handover_idiom_is_understood():
    (commitment,) = found("@leo to drain the queue right away")
    assert commitment.title == "drain the queue"
    assert commitment.owner_hint == "leo"
    assert commitment.due_at == NOW + timedelta(minutes=5)

    (taking,) = found("taking the ledger reconciliation")
    assert taking.title == "the ledger reconciliation"
    assert taking.owner_is_speaker is True


@pytest.mark.req("REQ-045")
def test_a_commitment_with_no_time_is_still_a_task():
    """"I'll roll back region-b" is work somebody owns; it simply has no
    countdown. Inventing a due time would put overdue work on the board that
    nobody promised."""
    (commitment,) = found("I'll take a look at the connection pool")
    assert commitment.due_at is None and commitment.due_label == ""


# --- what it refuses ----------------------------------------------------------


@pytest.mark.req("REQ-045")
def test_a_question_is_not_a_commitment():
    """A bridge is mostly questions. Turning "should we roll back?" into a
    rollback task assigned to whoever asked is exactly the confident wrongness
    that makes people stop trusting the board."""
    assert found("should we roll back?") == ()
    assert found("could we drain the queue instead") == ()
    assert found("does anyone know who deployed this") == ()
    assert found("maybe we should look at the cache") == ()
    assert found("who can check the pool") == ()


@pytest.mark.req("REQ-045")
def test_a_forecast_about_a_service_is_not_a_commitment_by_a_person():
    """"Payments will be down for a while" is a prediction about a system.

    Two independent guards catch it: the caller names its services, and a promise
    never starts with "be". Both are asserted, because the caller's list is not
    always complete and the platform must not depend on it being so.
    """
    assert found("Payments will be down for a while", not_people=("payments",)) == ()
    assert found("Payments will be down for a while") == ()
    assert found("Checkout will recover once the pool drains", not_people=("checkout",)) == ()
    assert found("Moving to the secondary") == ()


@pytest.mark.req("REQ-045")
def test_acknowledgements_and_filler_are_not_work():
    for filler in ("ok", "yes", "thanks", "ack", "+1", "got it"):
        assert found(filler) == (), f"{filler!r} is not a task"


@pytest.mark.req("REQ-045")
def test_an_option_being_discussed_is_not_a_commitment():
    """`can` is deliberately not a commitment verb. "we can roll back" is an
    option on the table, and a board full of options nobody committed to is a
    board people stop reading."""
    assert found("we can roll back if this gets worse") == ()


# --- idempotence and resumability ---------------------------------------------


@pytest.mark.req("REQ-045")
def test_the_same_sentence_read_twice_yields_the_same_key():
    """What makes the harvest idempotent without a cursor: a restart, a
    redelivery and two workers racing produce one task, not three."""
    messages = [line("I'll roll back region-b", message_id="m1")]
    first = harvest(messages, now=NOW)
    second = harvest(messages, now=NOW)
    assert [c.key for c in first] == [c.key for c in second]
    assert first[0].key != harvest(
        [line("I'll roll back region-b", message_id="m2")], now=NOW
    )[0].key, "a different message is a different commitment"


@pytest.mark.req("REQ-045")
def test_a_cursor_skips_what_has_already_been_read():
    messages = [
        {"message_id": "m1", "author": "ada", "text": "I'll drain the queue", "at": NOW},
        {
            "message_id": "m2",
            "author": "leo",
            "text": "I'll check the pool",
            "at": NOW + timedelta(minutes=5),
        },
    ]
    assert len(harvest(messages, now=NOW)) == 2
    later = harvest(messages, now=NOW, since=NOW)
    assert [c.title for c in later] == ["check the pool"]
    assert harvest(messages, now=NOW, since=NOW + timedelta(minutes=10)) == ()


# --- through the real bridge --------------------------------------------------


@pytest.mark.req("REQ-045", "REQ-033")
async def test_a_room_with_no_collaboration_connector_says_so_rather_than_looking_silent(
    api,
):
    """An install with no bridge still declares, pages and tracks tasks. The
    panel says there is no bridge; it does not render an empty transcript that
    reads as nobody talking."""
    owner_token, _tenant = owner(api)
    onboard(api, owner_token)
    wire_paging(api, owner_token)
    ic = commander(api, owner_token)
    incident = declare(api, ic["tokens"]["access_token"], severity="P1")

    room = war_room(api, ic["tokens"]["access_token"], incident["id"])
    assert room["has_bridge"] is False and room["bridge_channel"] is None

    panel = read_bridge(api, ic["tokens"]["access_token"], incident["id"])
    assert panel["has_bridge"] is False
    assert panel["messages"] == []
    assert "no collaboration connector" in panel["detail"]

    say(api, ic["tokens"]["access_token"], incident["id"], "anybody there?", expect=404)


@pytest.mark.req("REQ-045", "REQ-033")
async def test_declaring_gives_the_room_its_own_bridge_channel(api):
    owner_token, _tenant = owner(api)
    onboard(api, owner_token)
    wire_paging(api, owner_token)
    wire_bridge(api, owner_token)
    ic = commander(api, owner_token)
    incident = declare(api, ic["tokens"]["access_token"], severity="P0")

    room = war_room(api, ic["tokens"]["access_token"], incident["id"])
    assert room["has_bridge"] is True
    assert room["bridge_channel"] == f"incident-{incident['reference'].lower()}"
    assert room["bridge_adapter_id"] == "teams-sim"


@pytest.mark.req("REQ-045")
async def test_speaking_on_the_bridge_puts_you_in_the_room(api):
    """A "who is in the room" panel that omitted the person currently typing
    would be worse than no panel."""
    owner_token, _tenant = owner(api)
    onboard(api, owner_token)
    wire_paging(api, owner_token)
    wire_bridge(api, owner_token)
    ic = commander(api, owner_token)
    sme = responder(api, owner_token, email="sme@acme.io", display_name="Sam SME")
    incident = declare(api, ic["tokens"]["access_token"], severity="P0")

    before = war_room(api, ic["tokens"]["access_token"], incident["id"])
    joined = {m["user_id"]: m for m in before["members"]}
    assert joined[sme["user"]["id"]]["present"] is False

    say(api, sme["tokens"]["access_token"], incident["id"], "here, looking at the pool")

    after = war_room(api, ic["tokens"]["access_token"], incident["id"])
    joined = {m["user_id"]: m for m in after["members"]}
    assert joined[sme["user"]["id"]]["present"] is True

    transcript = read_bridge(api, ic["tokens"]["access_token"], incident["id"])
    assert any(
        "looking at the pool" in message["text"] for message in transcript["messages"]
    )


@pytest.mark.req("REQ-045")
async def test_a_commitment_said_on_the_bridge_becomes_a_task_with_an_owner_and_a_due(
    api,
):
    """End to end, through the real bridge.

    A commitment becomes a task the moment it is said rather than up to a sweep
    later, and it resolves to the speaker - because at that instant the session
    *is* the author, and nothing has to be inferred from a transcript the frozen
    SPI writes under the connector's own identity.
    """
    owner_token, _tenant = owner(api)
    onboard(api, owner_token)
    wire_paging(api, owner_token)
    wire_bridge(api, owner_token)
    ic = commander(api, owner_token)
    sme = responder(api, owner_token, email="sme@acme.io", display_name="Sam SME")
    incident = declare(api, ic["tokens"]["access_token"], severity="P0")

    token = sme["tokens"]["access_token"]
    say(api, token, incident["id"], "I'll roll back region-b in 10 minutes")
    say(api, token, incident["id"], "should we drain the queue as well?")

    # The sweep reads the same transcript back and creates nothing: the two
    # paths compose through the derived key rather than knowing about each other.
    swept = await api.container.bridge.harvest_all()
    assert swept.commitments == 1 and swept.created == 0, str(swept)
    assert swept.duplicates == 1

    board = tasks(api, ic["tokens"]["access_token"], incident["id"])
    titles = {task["title"]: task for task in board["tasks"]}
    assert "roll back region-b" in titles
    task = titles["roll back region-b"]
    assert task["source"] == "bridge"
    assert task["owner_user_id"] == sme["user"]["id"]
    assert task["assigned"] is True
    assert task["due_label"] == "in 10 minutes"
    assert task["due_at"] is not None
    assert "roll back region-b" in task["evidence_ref"]
    assert not any("drain the queue" in title for title in titles), (
        "a question is not a commitment"
    )

    published = topics(api, owner_token, "warroom.task.created")
    assert any(
        event["payload"]["title"] == "roll back region-b" for event in published
    )


@pytest.mark.req("REQ-045")
async def test_harvesting_twice_produces_one_task(api):
    """Written failing-first. The idempotency guard, through the real job.

    A restart, a redelivery and two workers racing must produce one board.
    """
    owner_token, _tenant = owner(api)
    onboard(api, owner_token)
    wire_paging(api, owner_token)
    wire_bridge(api, owner_token)
    ic = commander(api, owner_token)
    incident = declare(api, ic["tokens"]["access_token"], severity="P1")
    say(
        api,
        ic["tokens"]["access_token"],
        incident["id"],
        "I'll check the connection pool in 5 minutes",
    )

    first = await api.container.bridge.harvest_all()
    second = await api.container.bridge.harvest_all()
    third = await api.container.bridge.harvest_all()
    assert first.created == second.created == third.created == 0, (
        "the task was raised when it was said; no sweep raises it a second time"
    )
    assert first.duplicates == 1

    board = tasks(api, ic["tokens"]["access_token"], incident["id"])
    matching = [
        task for task in board["tasks"] if task["title"] == "check the connection pool"
    ]
    assert len(matching) == 1

    created = [
        event
        for event in topics(api, owner_token, "warroom.task.created")
        if event["payload"]["title"] == "check the connection pool"
    ]
    assert len(created) == 1, "one commitment, one event"


@pytest.mark.req("REQ-045")
async def test_an_unresolvable_owner_leaves_the_task_visibly_unassigned(api):
    """A name this platform cannot resolve is kept as a label.

    An unowned task on the board is recoverable in one click; a task owned by
    the wrong Sam is how the wrong person gets paged.
    """
    owner_token, _tenant = owner(api)
    onboard(api, owner_token)
    wire_paging(api, owner_token)
    wire_bridge(api, owner_token)
    ic = commander(api, owner_token)
    incident = declare(api, ic["tokens"]["access_token"], severity="P1")
    say(
        api,
        ic["tokens"]["access_token"],
        incident["id"],
        "Priya will check the replica lag in 15 minutes",
    )

    await api.container.bridge.harvest_all()
    board = tasks(api, ic["tokens"]["access_token"], incident["id"])
    task = next(
        item for item in board["tasks"] if item["title"] == "check the replica lag"
    )
    assert task["owner_user_id"] is None
    assert task["assigned"] is False
    assert task["owner_label"] == "Priya"
    # And anybody in the tenant can close out work that was actually done.
    from signalforge.tests.warroom.conftest import finish_task

    done = finish_task(api, ic["tokens"]["access_token"], incident["id"], task["id"])
    assert done["status"] == "done"


@pytest.mark.req("REQ-045")
async def test_a_service_name_on_the_bridge_never_becomes_a_task_owner(api):
    """The caller tells the domain which capitalised words are systems: the
    application and everything its topology names."""
    owner_token, _tenant = owner(api)
    onboard(api, owner_token)
    wire_paging(api, owner_token)
    wire_bridge(api, owner_token)
    ic = commander(api, owner_token)
    incident = declare(api, ic["tokens"]["access_token"], severity="P1")
    say(
        api,
        ic["tokens"]["access_token"],
        incident["id"],
        "Payments will recover once the pool drains",
    )

    await api.container.bridge.harvest_all()
    board = tasks(api, ic["tokens"]["access_token"], incident["id"])
    assert not [
        task for task in board["tasks"] if task["owner_label"].lower() == APP_ID
    ]
