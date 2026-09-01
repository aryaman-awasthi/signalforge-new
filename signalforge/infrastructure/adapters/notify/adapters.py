"""Notification adapters (REQ-033).

Four transports, one promise: every notification gets a durable receipt before
anything is transmitted, and the receipt is what "was this person told" is
answered from. That ordering is the point. A page whose delivery succeeded and
whose record did not is a page nobody can prove happened, and an escalation
timer built on the transport rather than the record would silence itself on a
lost frame.

The states are honest about what actually happened:

| Outcome | Status | Why |
|---|---|---|
| a live cockpit took the frame | SENT | measured: a subscriber received it |
| nobody is connected | QUEUED | not a failure - the socket asks for open notifications when it connects |
| the subscriber's queue is full | QUEUED | back pressure on one browser is not delivery |
| the simulated pager wrote its transcript | SENT | the simulation completed |
| a person acknowledged | ACKED | the human override, and terminal |

Sending the same `notification_id` twice returns the first receipt without
transmitting again: notifications are produced by retried workflows, and a
responder paged twice for one thing stops trusting the pager.
"""

from __future__ import annotations

from abc import abstractmethod
from dataclasses import dataclass

from signalforge.contracts.canonical import (
    DeliveryReceipt,
    DeliveryStatus,
    Notification,
    NotificationChannel,
)
from signalforge.contracts.errors import AdapterError, NotSupportedError
from signalforge.contracts.spi import NotificationsAdapter
from signalforge.domain.integrations.entities import (
    NotificationDelivery,
    delivery_frame,
)
from signalforge.infrastructure.adapters.base import GenericAdapter

SMS_MAX_CHARS = 160


@dataclass(frozen=True)
class Transmission:
    status: DeliveryStatus
    detail: str


class NotifyAdapter(GenericAdapter, NotificationsAdapter):
    channels: frozenset = frozenset()

    async def ping(self) -> bool:
        """The delivery store is this adapter's system of record, so the ping
        reads it: a channel that cannot write a receipt is genuinely down, even
        if its transport is fine."""
        async with self.services.uow_factory() as uow:
            await uow.notifications.get(self.tenant_id, "__ping__")
        return True

    async def send(self, notification: Notification) -> DeliveryReceipt:
        self._assert_channel(notification.channel)
        now = self.now()
        async with self.services.uow_factory() as uow:
            existing = await uow.notifications.get(
                self.tenant_id, notification.notification_id
            )
            if existing is not None:
                # One notification, one delivery. Retried workflows must not
                # page a responder twice for the same thing.
                return _receipt(existing)
            delivery = NotificationDelivery(
                tenant_id=self.tenant_id,
                notification_id=notification.notification_id,
                adapter_id=self.spec.id,
                channel=notification.channel,
                recipient=notification.recipient,
                title=notification.title,
                body=self.redact(notification.body),
                status=DeliveryStatus.QUEUED,
                at=now,
                app_id=self.app_id,
                incident_id=notification.incident_id,
                requires_ack=notification.requires_ack,
                simulated=self.spec.simulated,
            )
            # The receipt exists before the transport is touched, so a crash
            # between the two leaves a queued notification rather than nothing.
            await uow.notifications.upsert(delivery)
            await uow.commit()

        outcome = await self._transmit(notification, delivery)
        delivery = delivery.moved_to(outcome.status, at=self.now(), detail=outcome.detail)
        async with self.services.uow_factory() as uow:
            await uow.notifications.upsert(delivery)
            await uow.commit()
        self._count(delivery)
        return _receipt(delivery)

    async def status(self, notification_id: str) -> DeliveryReceipt:
        async with self.services.uow_factory() as uow:
            delivery = await uow.notifications.get(self.tenant_id, notification_id)
        if delivery is None:
            raise AdapterError(f"notification {notification_id} was never handed to {self.spec.id}")
        return _receipt(delivery)

    # -- for subclasses ----------------------------------------------------

    @abstractmethod
    async def _transmit(
        self, notification: Notification, delivery: NotificationDelivery
    ) -> Transmission:
        """Do the channel's actual work and say what happened."""

    @staticmethod
    def sent(detail: str) -> Transmission:
        return Transmission(DeliveryStatus.SENT, detail)

    @staticmethod
    def queued(detail: str) -> Transmission:
        return Transmission(DeliveryStatus.QUEUED, detail)

    @staticmethod
    def failed(detail: str) -> Transmission:
        return Transmission(DeliveryStatus.FAILED, detail)

    def _assert_channel(self, channel: NotificationChannel) -> None:
        if channel not in self.channels:
            raise NotSupportedError(
                f"{self.spec.id} delivers "
                f"{sorted(c.value for c in self.channels)}, not {channel.value}"
            )

    @staticmethod
    def _frame(notification: Notification, delivery: NotificationDelivery) -> dict:
        return delivery_frame(delivery)

    def _count(self, delivery: NotificationDelivery) -> None:
        if self.services.metrics is None:
            return
        self.services.metrics.increment(
            "signalforge_notifications_total",
            {"channel": delivery.channel.value, "status": delivery.status.value},
        )


class InAppNotifyAdapter(NotifyAdapter):
    """Delivered over the /ws socket the cockpit holds open."""

    channels = frozenset({NotificationChannel.IN_APP})

    async def _transmit(self, notification, delivery) -> Transmission:
        taken = self.services.notifications.deliver(
            self.tenant_id,
            notification.recipient,
            NotificationChannel.IN_APP,
            self._frame(notification, delivery),
        )
        if taken:
            return self.sent(f"delivered to {taken} open cockpit session(s)")
        return self.queued(
            "no cockpit session is open for this recipient; held for catch-up on "
            "the next connect"
        )


class BrowserPushNotifyAdapter(NotifyAdapter):
    """Raised as a browser notification by the cockpit's service worker.

    Delivery rides the same authenticated socket rather than a third-party push
    service: the frame is addressed to the browser-push channel and the page
    raises the OS notification. A deployment with no outbound internet still
    gets a real browser notification, and no notification content is handed to
    a third party - which for incident text is the point.
    """

    channels = frozenset({NotificationChannel.BROWSER_PUSH})

    async def _transmit(self, notification, delivery) -> Transmission:
        taken = self.services.notifications.deliver(
            self.tenant_id,
            notification.recipient,
            NotificationChannel.BROWSER_PUSH,
            self._frame(notification, delivery),
        )
        if taken:
            return self.sent(f"pushed to {taken} subscribed browser session(s)")
        return self.queued(
            "no browser session has subscribed to push for this recipient; held "
            "for catch-up"
        )


class PageSimNotifyAdapter(NotifyAdapter):
    """A simulated pager: SMS and voice, written to the delivery log.

    The transcript is rendered exactly as the channel would render it - SMS
    truncated at 160 characters, voice as the words that would be spoken - so a
    rehearsal shows what the person would actually have received rather than a
    tick in a box.
    """

    channels = frozenset({NotificationChannel.SMS, NotificationChannel.VOICE})

    async def _transmit(self, notification, delivery) -> Transmission:
        if notification.channel is NotificationChannel.SMS:
            transcript = _sms(delivery)
        else:
            transcript = _voice(delivery)
        return self.sent(f"SIMULATED {notification.channel.value} to {notification.recipient}: {transcript}")


def _sms(delivery: NotificationDelivery) -> str:
    text = f"{delivery.title}: {delivery.body}".replace("\n", " ").strip()
    if len(text) <= SMS_MAX_CHARS:
        return text
    return text[: SMS_MAX_CHARS - 3] + "..."


def _voice(delivery: NotificationDelivery) -> str:
    ack = (
        " Press one to acknowledge."
        if delivery.requires_ack
        else " This call is for information only."
    )
    return (
        f"This is an automated SignalForge call. {delivery.title}. "
        f"{delivery.body}.{ack}"
    ).replace("\n", " ")


def _receipt(delivery: NotificationDelivery) -> DeliveryReceipt:
    return DeliveryReceipt(
        notification_id=delivery.notification_id,
        status=delivery.status,
        at=delivery.at,
        detail=delivery.detail,
    )
