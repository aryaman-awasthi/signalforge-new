"""Idempotency and reconciliation in the war room (S4-T03, REQ-045).

The failure this file is about is the one that destroys a pager's credibility:
**a responder woken twice for one thing**. Once that happens people mute the
pager, and a muted pager is how the next P0 is missed. So the guard is not a
retry counter or a lock - it is that a notification id is *derived* from the page
and the channel, so the same page handed to the same connector produces the same
id however many times any code path runs, and `NotifyAdapter.send` returns the
first receipt without transmitting again.

The second half is reconciliation. An acknowledgement can arrive two ways - the
war room's own ack route, or the /ws socket the S3 notification inbox already
serves - and both are a real person really answering. The escalation sweep reads
the delivery receipt back before it escalates, which is the same shape as the
ITSM sync asking the queue what it holds before creating a second ticket.
"""

from __future__ import annotations

import pytest

from signalforge.contracts.canonical import DeliveryStatus
from signalforge.domain.warroom.paging import (
    DEFAULT_ACK_TIMEOUT_S,
    PageState,
    channels_for,
)
from signalforge.domain.onboarding.profile import Severity
from signalforge.tests.warroom.conftest import (
    APP_ID,
    ack,
    commander,
    declare,
    join,
    my_pages,
    onboard,
    owner,
    page_for,
    pages,
    relogin,
    responder,
    topics,
    war_room,
    wire_paging,
)

pytestmark = pytest.mark.anyio


async def deliveries_for(api, tenant_id: str, page: dict) -> list:
    async with api.container.uow_factory() as uow:
        return [
            await uow.notifications.get(tenant_id, link["notification_id"])
            for link in page["deliveries"]
        ]


@pytest.mark.req("REQ-045", "REQ-033")
async def test_a_notification_id_is_derived_so_one_page_is_one_notification(api):
    """Written failing-first. The duplicate-page guard, at its root.

    Re-paging is driven by calling the shipped `page_room` hook again with the
    same room, which is exactly what a retried declare, a restarted process or
    two workers racing would do. The receipts must not multiply.
    """
    owner_token, tenant_id = owner(api)
    onboard(api, owner_token)
    wire_paging(api, owner_token)
    ic = commander(api, owner_token)
    sme = responder(api, owner_token, email="sme@acme.io")
    incident = declare(api, ic["tokens"]["access_token"], severity="P0")

    board = pages(api, ic["tokens"]["access_token"], incident["id"])
    row = page_for({"pages": board["pages"]}, sme["user"]["id"])
    ids = [link["notification_id"] for link in row["deliveries"]]
    assert ids, "the page went out on at least one channel"
    assert len(ids) == len(set(ids)), "one channel, one notification id"

    async with api.container.uow_factory() as uow:
        before = [
            (await uow.notifications.get(tenant_id, notification_id)).at
            for notification_id in ids
        ]

    # Re-run the shipped paging hook against the same room. Nothing new is sent.
    async with api.container.uow_factory() as uow:
        room = await uow.war_rooms.get(tenant_id, incident["id"])
        members = list(await uow.room_members.list(tenant_id, incident["id"]))
        stored = await uow.incidents.get(tenant_id, incident["id"])
    await api.container.paging.page_room(room, stored, members)

    async with api.container.uow_factory() as uow:
        after = [
            (await uow.notifications.get(tenant_id, notification_id)).at
            for notification_id in ids
        ]
    assert before == after, (
        "a re-run of the paging hook must not re-transmit; the receipt is "
        "untouched, which is what "
        "'a responder paged twice for one thing stops trusting the pager' means"
    )


@pytest.mark.req("REQ-045")
async def test_declaring_twice_cannot_open_a_second_room_or_a_second_set_of_pages(api):
    """Two commanders pressing Declare seconds apart is a normal thing for a
    pager to cause. The second joins the first rather than opening a second war
    room, which is what makes the whole paging path safe to retry."""
    owner_token, tenant_id = owner(api)
    onboard(api, owner_token)
    wire_paging(api, owner_token)
    ic = commander(api, owner_token)
    other = commander(api, owner_token, email="ic2@acme.io")
    responder(api, owner_token, email="sme@acme.io")

    incident = declare(api, ic["tokens"]["access_token"], severity="P0")
    first = war_room(api, ic["tokens"]["access_token"], incident["id"])

    # The second declaration is refused by the lifecycle's own guard (S4-T02).
    declare(api, other["tokens"]["access_token"], severity="P0", expect=409)

    # And re-running the room hook is idempotent on the room itself.
    async with api.container.uow_factory() as uow:
        stored = await uow.incidents.get(tenant_id, incident["id"])
    from signalforge.application.identity.principal import Principal

    again = await api.container.war_rooms.open_for(
        stored,
        Principal(
            user_id=ic["user"]["id"],
            tenant_id=tenant_id,
            session_id="s-1",
            email="ic@acme.io",
            display_name="Ida Commander",
            bindings=(),
        ),
    )
    assert again.id == first["id"], "one incident, one room"

    second = war_room(api, ic["tokens"]["access_token"], incident["id"])
    assert len(second["pages"]) == len(first["pages"])
    assert len(second["members"]) == len(first["members"])


@pytest.mark.req("REQ-045")
async def test_acknowledging_twice_is_one_act(api):
    """Idempotent and terminal. A double-tapped button on a train, a redelivered
    socket frame and a retried request are the same acknowledgement."""
    owner_token, _tenant = owner(api)
    onboard(api, owner_token)
    wire_paging(api, owner_token)
    ic = commander(api, owner_token)
    sme = responder(api, owner_token, email="sme@acme.io")
    incident = declare(api, ic["tokens"]["access_token"], severity="P0")

    board = pages(api, ic["tokens"]["access_token"], incident["id"])
    row = page_for({"pages": board["pages"]}, sme["user"]["id"])
    token = sme["tokens"]["access_token"]

    first = ack(api, token, incident["id"], row["id"], note="on it")
    second = ack(api, token, incident["id"], row["id"], note="still on it")
    third = ack(api, token, incident["id"], row["id"])

    assert first["acked_at"] == second["acked_at"] == third["acked_at"]
    assert first["ack_note"] == second["ack_note"] == "on it", (
        "the first answer is the record; a second call does not rewrite it"
    )

    answered = [
        event
        for event in topics(api, owner_token, "warroom.page.acknowledged")
        if event["payload"]["page_id"] == row["id"]
    ]
    assert len(answered) == 1, "one acknowledgement, one event"
    assert answered[0]["payload"]["answered_in_s"] is not None


@pytest.mark.req("REQ-045", "REQ-033")
async def test_a_page_answered_over_the_socket_is_reconciled_and_not_escalated(api):
    """Reconcile before acting, the same shape the ITSM sync uses.

    The S3 notification inbox is a real acknowledgement path - it is how the
    cockpit's socket answers a page - and escalating past one would wake a second
    person for work that already has an owner. So the sweep reads the delivery
    receipt back before it escalates.
    """
    owner_token, tenant_id = owner(api)
    onboard(api, owner_token)
    wire_paging(api, owner_token)
    ic = commander(api, owner_token)
    sme = responder(api, owner_token, email="sme@acme.io")
    incident = declare(api, ic["tokens"]["access_token"], severity="P0")

    board = pages(api, ic["tokens"]["access_token"], incident["id"])
    row = page_for({"pages": board["pages"]}, sme["user"]["id"])
    in_app = next(
        link for link in row["deliveries"] if link["channel"] == "in_app"
    )

    # Answer it the S3 way, through the notification inbox the socket serves.
    await api.container.notification_inbox.acknowledge(
        tenant_id, in_app["notification_id"], by=sme["user"]["id"]
    )

    api.clock.advance(seconds=DEFAULT_ACK_TIMEOUT_S + 60)
    swept = await api.container.paging.sweep()
    assert swept.acked_elsewhere >= 1, str(swept)

    token = relogin(api)
    board = pages(api, token, incident["id"])
    settled = page_for({"pages": board["pages"]}, sme["user"]["id"])
    assert settled["state"] == "acked"
    assert settled["acked_by"] == sme["user"]["id"]
    assert "in_app channel" in settled["ack_note"]

    escalations = [
        event
        for event in topics(api, token, "warroom.page.escalated")
        if event["payload"]["page_id"] == row["id"]
    ]
    assert not escalations, "the socket's answer is a real answer"


@pytest.mark.req("REQ-045")
async def test_the_sweep_is_safe_to_run_repeatedly(api):
    """A worker that ran twice, or a node that woke up late, must not produce a
    second escalation for the same page."""
    owner_token, _tenant = owner(api)
    onboard(api, owner_token)
    wire_paging(api, owner_token)
    ic = commander(api, owner_token)
    first = responder(api, owner_token, email="first@acme.io")
    second = responder(
        api, owner_token, email="second@acme.io", roles=("auditor",), display_name="Leo"
    )
    from signalforge.tests.warroom.conftest import rotate

    rotate(api, owner_token, first["user"]["id"], second["user"]["id"])
    incident = declare(api, ic["tokens"]["access_token"], severity="P0")

    board = pages(api, ic["tokens"]["access_token"], incident["id"])
    row = page_for({"pages": board["pages"]}, first["user"]["id"])
    api.clock.advance(seconds=row["countdown_s"] + 5)

    await api.container.paging.sweep()
    await api.container.paging.sweep()
    await api.container.paging.sweep()

    token = relogin(api)
    escalations = [
        event
        for event in topics(api, token, "warroom.page.escalated")
        if event["payload"]["page_id"] == row["id"]
    ]
    assert len(escalations) == 1, "one page, one escalation, however often it runs"

    board = pages(api, token, incident["id"])
    for_second = [
        item for item in board["pages"] if item["recipient"] == second["user"]["id"]
    ]
    assert len(for_second) == 1, "the successor is raised once"


@pytest.mark.req("REQ-045", "REQ-033")
async def test_a_page_that_partly_failed_still_counts_as_reaching_somebody(api):
    """Partial completion. One channel down does not mean nobody was told.

    The in-app channel is bound and the SMS one is not, so a P0 goes out on
    fewer channels than the severity asks for. The page is `sent`, the board says
    which channels carried it, and the countdown runs normally - because somebody
    genuinely was reached.
    """
    from signalforge.tests.adapters.conftest import connect

    owner_token, _tenant = owner(api)
    onboard(api, owner_token)
    connect(api, owner_token, "notify-in-app", app_id=APP_ID)  # no page-sim
    ic = commander(api, owner_token)
    sme = responder(api, owner_token, email="sme@acme.io")
    incident = declare(api, ic["tokens"]["access_token"], severity="P0")

    board = pages(api, ic["tokens"]["access_token"], incident["id"])
    row = page_for({"pages": board["pages"]}, sme["user"]["id"])
    channels = {link["channel"] for link in row["deliveries"]}
    assert channels == {"in_app"}
    assert "sms" in {channel.value for channel in channels_for(Severity.P0)}, (
        "a P0 asks for SMS; this install simply has no connector that carries it"
    )
    assert row["state"] == "sent" and row["reached"] is True
    assert row["countdown_s"] > 0

    # Nobody was connected to the socket, so it is queued rather than sent -
    # which is not a failure, and the responder still finds it on their pager.
    assert {link["status"] for link in row["deliveries"]} == {
        DeliveryStatus.QUEUED.value
    }
    assert my_pages(api, sme["tokens"]["access_token"])["count"] == 1
