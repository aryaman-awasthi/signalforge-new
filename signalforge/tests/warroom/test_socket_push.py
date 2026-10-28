"""The cockpit stops polling blind: war-room changes arrive over /ws (REQ-033, REQ-045).

The S3 gap S4 carried and did not close was that the screens poll. The reason
was real - the cockpit's session token lives in an httpOnly cookie and a
cross-origin handshake has nothing to authenticate with - and the answer is the
socket ticket, whose credential properties are asserted in
tests/security/test_socket_ticket.py. This file asserts the other half: that
something actually arrives.

Two decisions are under test here rather than merely implemented.

**A nudge is not data.** `room.changed` names the incident, the topic and the
sequence number. Nothing about the incident travels down the socket, so the
socket never becomes a second read path with its own authorisation to get
wrong; the screen re-reads through the route it was already polling and the
403 it would have got, it still gets.

**A nudge is tenant-scoped.** Watching is a client-supplied incident id, so the
fan-out - not the client - has to be the thing that refuses. A socket in one
tenant watching another tenant's incident id receives nothing at all.
"""

from __future__ import annotations

import pytest

from signalforge.tests.warroom.conftest import (
    APP_ID,
    commander,
    declare,
    onboard,
    owner,
    responder,
    say,
    wire_bridge,
    wire_paging,
)

pytestmark = pytest.mark.anyio


def ws_ticket(api, token: str) -> str:
    response = api.client.post(
        "/auth/ws-ticket", json={}, headers=api.auth_header(token)
    )
    assert response.status_code == 201, response.text
    return response.json()["ws_ticket"]


def drain(socket, *, until: str, limit: int = 60) -> list[dict]:
    """Frames up to and including the first one of `until`. Bounded, because a
    socket that never sends it should fail as a test rather than as a hang."""
    seen: list[dict] = []
    for _ in range(limit):
        frame = socket.receive_json()
        seen.append(frame)
        if frame.get("type") == until:
            return seen
    raise AssertionError(f"no {until!r} frame in {seen}")


async def dispatch(api) -> None:
    """One dispatcher pass, and it must have been a *clean* one.

    Asserted rather than assumed, after a real defect: the nudge frame is sent
    before the consumer's metric is counted, so a consumer that raised on the
    metric still delivered the frame - every assertion in this file passed while
    every event dead-lettered. The dispatcher's own report is the only place that
    difference is visible, so it is read here.
    """
    report = await api.container.dispatcher.run_once()
    assert report.dead_lettered == 0, (
        "the cockpit-socket consumer dead-lettered an event it appeared to handle"
    )
    assert report.retried == 0, "the cockpit-socket consumer raised and was retried"


def round_trip(socket, *, limit: int = 60) -> list[dict]:
    """Everything still queued for this socket, flushed by a ping.

    A round trip rather than a timeout: anything the server had already sent is
    ahead of the pong in the same queue, so "no nudge arrived" is proven rather
    than waited for. Notifications are *not* nudges and legitimately appear here
    - the commander is paged by their own P0 - so the caller filters.
    """
    socket.send_json({"type": "ping"})
    frames = drain(socket, until="pong", limit=limit)
    return [frame for frame in frames if frame.get("type") == "room.changed"]


@pytest.mark.req("REQ-033", "REQ-045")
async def test_a_watched_room_pushes_a_nudge_that_carries_no_incident_data(api):
    owner_token, _tenant = owner(api)
    onboard(api, owner_token)
    wire_paging(api, owner_token)
    wire_bridge(api, owner_token)
    ic = commander(api, owner_token)
    token = ic["tokens"]["access_token"]
    responder(api, owner_token, email="sme@acme.io")
    incident = declare(api, token, severity="P0")

    with api.client.websocket_connect(
        "/ws", subprotocols=["ticket", ws_ticket(api, token)]
    ) as socket:
        assert socket.receive_json()["type"] == "ready"
        socket.send_json({"type": "watch", "incident_ids": [incident["id"]]})
        watching = drain(socket, until="watching")[-1]
        assert watching["incident_ids"] == [incident["id"]]

        # Something happens in the room. The bridge post is a good probe: it is
        # exactly the kind of change a five-second poll shows late.
        say(api, token, incident["id"], "rolling back region-b now")
        await dispatch(api)

        nudges = round_trip(socket, limit=80)
        assert nudges, "the watched room pushed nothing"
        topics = {nudge["topic"] for nudge in nudges}
        assert "incident.declared" in topics, "declaring the incident is a change"
        assert "warroom.bridge.posted" in topics, "so is somebody speaking on it"
        nudge = nudges[-1]
        assert nudge["incident_id"] == incident["id"]
        assert isinstance(nudge["seq"], int)
        # The whole frame, and nothing else in it. A payload here would be a
        # read path that never met an authorisation check.
        assert set(nudge) == {"type", "incident_id", "topic", "seq"}
        assert "rolling back region-b now" not in str(nudge)


@pytest.mark.req("REQ-013", "REQ-033", "REQ-045")
async def test_a_socket_in_another_tenant_is_nudged_by_nothing(api):
    """The client chooses what to watch, so the fan-out is where the tenant
    predicate has to live. Asserted by watching the *right* id from the *wrong*
    tenant - the one case a missing predicate would let through."""
    owner_token, _tenant = owner(api)
    onboard(api, owner_token)
    wire_paging(api, owner_token)
    wire_bridge(api, owner_token)
    ic = commander(api, owner_token)
    token = ic["tokens"]["access_token"]
    responder(api, owner_token, email="sme@acme.io")
    incident = declare(api, token, severity="P0")

    stranger = api.signup(org="Globex", email="stranger@globex.io")
    stranger_token = stranger["tokens"]["access_token"]

    with api.client.websocket_connect(
        "/ws", subprotocols=["ticket", ws_ticket(api, stranger_token)]
    ) as socket:
        assert socket.receive_json()["type"] == "ready"
        socket.send_json({"type": "watch", "incident_ids": [incident["id"]]})
        assert socket.receive_json()["type"] == "watching"

        say(api, token, incident["id"], "rolling back region-b now")
        await dispatch(api)

        assert round_trip(socket) == [], (
            "a socket in another tenant was nudged about this room"
        )

    # And the read the stranger's screen would make is refused anyway, which is
    # the compensating control the nudge design leans on.
    assert (
        api.client.get(
            f"/incidents/{incident['id']}/war-room",
            headers=api.auth_header(stranger_token),
        ).status_code
        == 404
    )


@pytest.mark.req("REQ-033", "REQ-045")
async def test_a_socket_that_watches_nothing_receives_no_nudges(api):
    """The default. A cockpit on the apps list is not subscribed to every room
    in its tenant by accident."""
    owner_token, _tenant = owner(api)
    onboard(api, owner_token)
    wire_paging(api, owner_token)
    wire_bridge(api, owner_token)
    ic = commander(api, owner_token)
    token = ic["tokens"]["access_token"]
    responder(api, owner_token, email="sme@acme.io")
    incident = declare(api, token, severity="P0")

    with api.client.websocket_connect(
        "/ws", subprotocols=["ticket", ws_ticket(api, token)]
    ) as socket:
        assert socket.receive_json()["type"] == "ready"
        say(api, token, incident["id"], "rolling back region-b now")
        await dispatch(api)
        assert round_trip(socket) == [], "an unwatched room pushed a nudge"


@pytest.mark.req("REQ-033", "REQ-045")
async def test_a_malformed_watch_frame_subscribes_to_nothing(api):
    """Failing safe in the direction that matters: a client that meant to watch
    one room and got the field wrong falls back to polling rather than being
    subscribed to everything."""
    owner_token, _tenant = owner(api)
    onboard(api, owner_token)
    ic = commander(api, owner_token)
    token = ic["tokens"]["access_token"]

    with api.client.websocket_connect(
        "/ws", subprotocols=["ticket", ws_ticket(api, token)]
    ) as socket:
        assert socket.receive_json()["type"] == "ready"
        socket.send_json({"type": "watch", "incident_ids": "not-a-list"})
        assert drain(socket, until="watching")[-1] == {
            "type": "watching",
            "incident_ids": [],
        }


@pytest.mark.req("REQ-013", "REQ-033")
async def test_a_notification_is_acknowledged_over_the_socket_only_by_its_recipient(api):
    """Written failing-first. The `ack` frame used to acknowledge any
    notification in the tenant: a commander's cockpit could silence the page
    ringing on an SME's phone, and the escalation that page was counting down
    to with it. The socket now answers with an error and the delivery stands;
    the person it was sent to acknowledges it as before."""
    owner_token, _tenant = owner(api)
    onboard(api, owner_token)
    wire_paging(api, owner_token)
    ic = commander(api, owner_token)
    ic_token = ic["tokens"]["access_token"]
    sme = responder(api, owner_token, email="sme@acme.io")
    incident = declare(api, ic_token, severity="P0")

    from signalforge.tests.warroom.conftest import page_for, pages

    row = page_for(pages(api, ic_token, incident["id"]), sme["user"]["id"])
    assert row is not None, "the SME was paged by the P0"
    notification_id = next(
        link["notification_id"]
        for link in row["deliveries"]
        if link["channel"] == "in_app"
    )

    with api.client.websocket_connect(
        "/ws", subprotocols=["ticket", ws_ticket(api, ic_token)]
    ) as socket:
        assert socket.receive_json()["type"] == "ready"
        socket.send_json({"type": "ack", "notification_id": notification_id})
        # Flushed by a ping rather than waited for: an `ack.ok` here would
        # otherwise be a hang, not a failure.
        socket.send_json({"type": "ping"})
        frames = drain(socket, until="pong")
        assert not [f for f in frames if f.get("type") == "ack.ok"], frames
        answer = next(f for f in frames if f.get("type") == "error")
        assert "person it was sent to" in answer["detail"]

    with api.client.websocket_connect(
        "/ws", subprotocols=["ticket", ws_ticket(api, sme["tokens"]["access_token"])]
    ) as socket:
        assert socket.receive_json()["type"] == "ready"
        socket.send_json({"type": "ack", "notification_id": notification_id})
        answer = drain(socket, until="ack.ok")[-1]
        assert answer["acked_by"] == sme["user"]["id"]
