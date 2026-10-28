"""Notification channels and the /ws socket that carries them (REQ-033).

The design under test is that the durable receipt, not the socket, is the record
of what happened. So: the receipt exists before the transport is touched; nobody
being connected is QUEUED rather than failed; a reconnecting cockpit is caught up
on what is still open; and an acknowledgement is a human act that is idempotent,
terminal and audited.
"""

from __future__ import annotations

import pytest

from signalforge.contracts.canonical import (
    DeliveryStatus,
    Notification,
    NotificationChannel,
)
from signalforge.contracts.errors import AdapterError, NotSupportedError
from signalforge.tests.adapters.conftest import connect

pytestmark = pytest.mark.anyio


def notification(
    notification_id="n-1",
    channel=NotificationChannel.IN_APP,
    recipient="user-1",
    requires_ack=False,
    **kwargs,
):
    return Notification(
        notification_id=notification_id,
        channel=channel,
        recipient=recipient,
        title=kwargs.pop("title", "Checkout error rate above SLO"),
        body=kwargs.pop("body", "Error rate 12% in eu-west-1 for the last 4 minutes."),
        requires_ack=requires_ack,
        **kwargs,
    )


@pytest.fixture
def notify(api):
    signup = api.signup()
    token = signup["tokens"]["access_token"]
    tenant_id = signup["org"]["id"]
    user_id = signup["user"]["id"]

    class Notify:
        tenant = tenant_id
        user = user_id
        access_token = token
        container = api.container

        def __init__(self):
            self._bindings: dict[str, dict] = {}

        def bind(self, adapter_id):
            # One binding per adapter, as the uniqueness key requires: a second
            # at the same scope is a duplicate and the API says so.
            if adapter_id not in self._bindings:
                self._bindings[adapter_id] = connect(
                    api, token, adapter_id, credentials={}
                )
            return self._bindings[adapter_id]

        async def send(self, adapter_id, message):
            return await self.invoke(self.bind(adapter_id), "send", message)

        async def invoke(self, binding, capability, *args):
            return await api.container.invoker.invoke(
                tenant_id, binding["id"], capability, *args
            )

    return Notify()


# --- channels -----------------------------------------------------------------


@pytest.mark.req("REQ-033")
async def test_with_nobody_connected_a_notification_is_queued_not_failed(notify):
    receipt = await notify.send(
        "notify-in-app", notification(recipient=notify.user)
    )
    assert receipt.status is DeliveryStatus.QUEUED
    assert "no cockpit session" in receipt.detail


@pytest.mark.req("REQ-033")
async def test_a_live_session_takes_the_frame_and_the_receipt_says_sent(api, notify):
    with api.client.websocket_connect(
        "/ws", headers=api.auth_header(notify.access_token)
    ) as socket:
        assert socket.receive_json()["type"] == "ready"
        receipt = await notify.send(
            "notify-in-app", notification(recipient=notify.user)
        )
        assert receipt.status is DeliveryStatus.SENT
        frame = socket.receive_json()
        assert frame["type"] == "notification"
        assert frame["notification_id"] == "n-1"
        assert frame["replay"] is False


@pytest.mark.req("REQ-033")
async def test_sending_the_same_notification_twice_is_one_delivery(notify):
    binding = notify.bind("notify-in-app")
    first = await notify.invoke(binding, "send", notification(recipient=notify.user))
    second = await notify.invoke(binding, "send", notification(recipient=notify.user))
    assert first.notification_id == second.notification_id
    assert first.at == second.at, "a responder paged twice for one thing stops trusting it"


@pytest.mark.req("REQ-033")
async def test_the_simulated_pager_records_the_transcript_it_would_have_sent(notify):
    sms = await notify.send(
        "page-sim",
        notification(channel=NotificationChannel.SMS, recipient="+15550000"),
    )
    assert sms.status is DeliveryStatus.SENT
    assert "SIMULATED sms" in sms.detail
    assert "Checkout error rate above SLO" in sms.detail

    binding = notify.bind("page-sim")
    voice = await notify.invoke(
        binding,
        "send",
        notification(
            notification_id="n-voice",
            channel=NotificationChannel.VOICE,
            recipient="+15550000",
            requires_ack=True,
        ),
    )
    assert "Press one to acknowledge" in voice.detail


@pytest.mark.req("REQ-033")
async def test_an_sms_transcript_is_truncated_the_way_an_sms_would_be(notify):
    receipt = await notify.send(
        "page-sim",
        notification(
            channel=NotificationChannel.SMS,
            recipient="+15550000",
            body="x" * 400,
        ),
    )
    body = receipt.detail.split(": ", 1)[1]
    assert len(body) <= 160
    assert body.endswith("...")


@pytest.mark.req("REQ-033")
async def test_a_dm_lands_in_the_simulated_workspace_and_in_the_delivery_log(notify):
    receipt = await notify.send(
        "teams-sim-dm",
        notification(channel=NotificationChannel.DM, recipient=notify.user),
    )
    assert receipt.status is DeliveryStatus.SENT
    async with notify.container.integration_uow_factory() as uow:
        messages = await uow.messages.read(
            notify.tenant, f"dm:{notify.user}", since=None, until=None, limit=10
        )
    assert len(messages) == 1
    assert "Checkout error rate above SLO" in messages[0].text


@pytest.mark.req("REQ-033")
async def test_a_channel_adapter_refuses_a_channel_it_does_not_serve(notify):
    binding = notify.bind("notify-in-app")
    with pytest.raises(NotSupportedError) as refused:
        await notify.invoke(
            binding, "send", notification(channel=NotificationChannel.SMS)
        )
    assert "in_app" in str(refused.value)


@pytest.mark.req("REQ-033")
async def test_status_of_an_unknown_notification_is_an_error_not_a_guess(notify):
    binding = notify.bind("notify-in-app")
    with pytest.raises(AdapterError):
        await notify.invoke(binding, "status", "never-sent")


@pytest.mark.req("REQ-033")
async def test_the_receipt_exists_before_the_transport_is_touched(notify):
    """A page whose delivery succeeded and whose record did not is a page
    nobody can prove happened."""
    binding = notify.bind("notify-in-app")
    await notify.invoke(binding, "send", notification(recipient=notify.user))
    async with notify.container.integration_uow_factory() as uow:
        stored = await uow.notifications.get(notify.tenant, "n-1")
    assert stored is not None
    assert stored.adapter_id == "notify-in-app"
    assert stored.status is DeliveryStatus.QUEUED


# --- the socket ---------------------------------------------------------------


@pytest.mark.req("REQ-033")
async def test_a_reconnecting_cockpit_is_caught_up_on_what_is_still_open(api, notify):
    await notify.send(
        "notify-in-app",
        notification(recipient=notify.user, requires_ack=True),
    )
    with api.client.websocket_connect(
        "/ws", headers=api.auth_header(notify.access_token)
    ) as socket:
        assert socket.receive_json()["type"] == "ready"
        replayed = socket.receive_json()
        assert replayed["notification_id"] == "n-1"
        assert replayed["replay"] is True
        assert replayed["requires_ack"] is True


@pytest.mark.req("REQ-033")
async def test_acknowledging_over_the_socket_is_terminal_and_audited(api, notify):
    await notify.send(
        "notify-in-app",
        notification(recipient=notify.user, requires_ack=True),
    )
    with api.client.websocket_connect(
        "/ws", headers=api.auth_header(notify.access_token)
    ) as socket:
        socket.receive_json()
        socket.receive_json()
        socket.send_json({"type": "ack", "notification_id": "n-1"})
        answer = socket.receive_json()
        assert answer == {
            "type": "ack.ok",
            "notification_id": "n-1",
            "status": "acked",
            "acked_by": notify.user,
        }

        # Idempotent: a double-clicked button is one act.
        socket.send_json({"type": "ack", "notification_id": "n-1"})
        assert socket.receive_json()["status"] == "acked"

    topics = api.audit_topics(notify.tenant)
    assert topics.count("notification.acknowledged") == 1

    with api.client.websocket_connect(
        "/ws", headers=api.auth_header(notify.access_token)
    ) as socket:
        assert socket.receive_json()["type"] == "ready"
        socket.send_json({"type": "ping"})
        assert socket.receive_json() == {"type": "pong"}, (
            "an acknowledged notification is not replayed again"
        )


@pytest.mark.req("REQ-033")
async def test_acknowledging_something_that_does_not_want_one_is_refused(api, notify):
    await notify.send("notify-in-app", notification(recipient=notify.user))
    with api.client.websocket_connect(
        "/ws", headers=api.auth_header(notify.access_token)
    ) as socket:
        socket.receive_json()
        socket.send_json({"type": "ack", "notification_id": "n-1"})
        answer = socket.receive_json()
        assert answer["type"] == "error"
        assert "acknowledgement" in answer["detail"]


@pytest.mark.req("REQ-033")
async def test_acknowledging_an_unknown_id_says_only_that(api, notify):
    with api.client.websocket_connect(
        "/ws", headers=api.auth_header(notify.access_token)
    ) as socket:
        socket.receive_json()
        socket.send_json({"type": "ack", "notification_id": "who-knows"})
        assert socket.receive_json() == {
            "type": "error",
            "detail": "notification not found",
        }


@pytest.mark.req("REQ-033")
async def test_a_session_subscribes_to_the_channels_it_can_render(api, notify):
    with api.client.websocket_connect(
        "/ws", headers=api.auth_header(notify.access_token)
    ) as socket:
        assert socket.receive_json()["channels"] == ["in_app"]

        # A browser-push notification is not delivered to a session that has
        # not said it can raise one.
        receipt = await notify.send(
            "notify-browser-push",
            notification(
                notification_id="n-push", channel=NotificationChannel.BROWSER_PUSH,
                recipient=notify.user,
            ),
        )
        assert receipt.status is DeliveryStatus.QUEUED

        socket.send_json({"type": "hello", "channels": ["in_app", "browser_push"]})
        assert socket.receive_json() == {
            "type": "subscribed",
            "channels": ["browser_push", "in_app"],
        }

        second = await notify.send(
            "notify-browser-push",
            notification(
                notification_id="n-push-2", channel=NotificationChannel.BROWSER_PUSH,
                recipient=notify.user,
            ),
        )
        assert second.status is DeliveryStatus.SENT
        assert socket.receive_json()["notification_id"] == "n-push-2"


@pytest.mark.req("REQ-033")
async def test_an_unknown_frame_gets_an_error_rather_than_silence(api, notify):
    with api.client.websocket_connect(
        "/ws", headers=api.auth_header(notify.access_token)
    ) as socket:
        socket.receive_json()
        socket.send_json({"type": "wibble"})
        assert "unknown frame type" in socket.receive_json()["detail"]
        socket.send_json({"type": "ping"})
        assert socket.receive_json() == {"type": "pong"}, "the socket stayed usable"


@pytest.mark.req("REQ-033")
async def test_the_socket_accepts_the_bearer_subprotocol_a_browser_can_send(api, notify):
    """A browser cannot set an Authorization header on a handshake, and a token
    in the query string would be in every access log on the path."""
    with api.client.websocket_connect(
        "/ws", subprotocols=["bearer", notify.access_token]
    ) as socket:
        assert socket.receive_json()["type"] == "ready"


@pytest.mark.req("REQ-033")
async def test_a_notification_for_someone_else_is_not_delivered_to_this_session(
    api, notify
):
    with api.client.websocket_connect(
        "/ws", headers=api.auth_header(notify.access_token)
    ) as socket:
        socket.receive_json()
        receipt = await notify.send(
            "notify-in-app", notification(recipient="somebody-else")
        )
        assert receipt.status is DeliveryStatus.QUEUED
        socket.send_json({"type": "ping"})
        assert socket.receive_json() == {"type": "pong"}
