"""What a connected cockpit reads and writes back (REQ-033).

Two operations, both of which exist because a socket is not a reliable delivery
mechanism and must not be treated as one:

- **catch_up** is what a session asks for the moment it connects. Anything that
  still wants a human - `requires_ack` and not yet acknowledged or failed - is
  handed over, so a notification produced while nobody was looking is not lost,
  merely early.
- **acknowledge** is the human override. It is idempotent (a double-clicked
  button and a redelivered frame are one act), it is terminal (an acknowledged
  page cannot be un-acknowledged by a machine), and it is audited in the same
  transaction that records it, so an escalation timer and the audit chain can
  never disagree about whether somebody answered.
"""

from __future__ import annotations

from typing import Callable, Optional, Sequence

from signalforge.application.identity.uow import IdentityUnitOfWork
from signalforge.contracts.canonical import NotificationChannel
from signalforge.domain.identity.ports import Clock
from signalforge.domain.integrations.entities import NotificationDelivery
from signalforge.domain.identity.errors import PermissionDeniedError
from signalforge.domain.shared.errors import NotFoundError

ACK_TOPIC = "notification.acknowledged"


class NotificationInbox:
    def __init__(
        self,
        uow_factory: Callable[[], IdentityUnitOfWork],
        clock: Clock,
    ):
        self._uow_factory = uow_factory
        self._clock = clock

    async def catch_up(
        self,
        tenant_id: str,
        recipient: str,
        *,
        channels: Sequence[NotificationChannel] = (),
        limit: int = 50,
    ) -> list[NotificationDelivery]:
        async with self._uow_factory() as uow:
            deliveries = await uow.notifications.list_for_recipient(
                tenant_id,
                recipient,
                channels=tuple(channels),
                open_only=True,
                limit=limit,
            )
        # Oldest first: the thing that has been waiting longest is the thing the
        # responder should see at the top of a freshly opened cockpit.
        return sorted(deliveries, key=lambda delivery: delivery.at)

    async def acknowledge(
        self,
        tenant_id: str,
        notification_id: str,
        *,
        by: str,
        app_id: Optional[str] = None,
    ) -> NotificationDelivery:
        now = self._clock.now()
        async with self._uow_factory() as uow:
            delivery = await uow.notifications.get(tenant_id, notification_id)
            if delivery is None:
                raise NotFoundError(f"notification {notification_id} not found")
            if delivery.recipient and delivery.recipient != by:
                # "I have this" is said by the person it was sent to. A session
                # in the same tenant acknowledging somebody else's page would
                # silence an escalation that person never answered.
                raise PermissionDeniedError(
                    "a notification is acknowledged by the person it was sent to"
                )
            if delivery.status.value == "acked":
                return delivery
            acknowledged = delivery.acknowledged(at=now, by=by)
            await uow.notifications.upsert(acknowledged)
            await uow.audit.append(
                tenant_id=tenant_id,
                topic=ACK_TOPIC,
                actor_type="human",
                actor_id=by,
                app_id=app_id or delivery.app_id,
                payload={
                    "notification_id": delivery.notification_id,
                    "channel": delivery.channel.value,
                    "adapter_id": delivery.adapter_id,
                    "incident_id": delivery.incident_id,
                    "recipient": delivery.recipient,
                    "simulated": delivery.simulated,
                },
            )
            await uow.commit()
        return acknowledged
