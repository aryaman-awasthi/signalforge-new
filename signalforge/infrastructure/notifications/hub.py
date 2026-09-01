"""The in-process fan-out behind /ws (REQ-033).

`deliver` returns how many sockets actually took the frame, and everything about
the design follows from that number being honest:

- Zero subscribers is not a failure. Nobody is looking at the cockpit at 03:00,
  and the notification stays QUEUED with a durable receipt; the socket asks for
  what is still open when it connects, so nothing is lost by being early.
- A subscriber whose queue is full does not count as delivered either. Back
  pressure on one slow browser must not block the sender or silently drop a page
  and call it sent; the frame is skipped, the subscription is marked lagging,
  and that client catches up from the store on its next connect.
- Delivery is per (tenant, recipient, channel). The tenant check is here as well
  as in the socket's authentication, because a fan-out is exactly the kind of
  component where a missing predicate becomes a cross-tenant leak.

In-process is the right scope for the single-node deployment (CLAUDE.md
environment). On a split api/worker deployment a notification produced on the
worker reaches a socket on the API node through the event bus rather than
through this object, which is why the durable receipt - not this queue - is the
record of what happened.
"""

from __future__ import annotations

import asyncio
import itertools
import logging
from dataclasses import dataclass, field
from typing import Any, Optional

from signalforge.contracts.canonical import NotificationChannel
from signalforge.domain.integrations.ports import NotificationSink

logger = logging.getLogger("signalforge.notifications")

QUEUE_MAXSIZE = 256

DEFAULT_CHANNELS = frozenset({NotificationChannel.IN_APP})


@dataclass
class Subscription:
    id: int
    tenant_id: str
    user_id: str
    queue: "asyncio.Queue[dict[str, Any]]"
    loop: Optional[asyncio.AbstractEventLoop] = None
    """The loop the socket is being served on, captured at subscribe time. A
    notification produced on another loop - a worker thread, or a test driving
    the adapters directly - has to be handed over rather than pushed, because
    waking an asyncio.Queue from the wrong loop does not wake anybody."""
    channels: set[NotificationChannel] = field(default_factory=lambda: set(DEFAULT_CHANNELS))
    lagged: int = 0

    def accepts(self, channel: NotificationChannel) -> bool:
        return channel in self.channels


class InProcessNotificationHub(NotificationSink):
    def __init__(self) -> None:
        self._subscriptions: dict[int, Subscription] = {}
        self._ids = itertools.count(1)

    def subscribe(
        self,
        tenant_id: str,
        user_id: str,
        channels: Optional[set[NotificationChannel]] = None,
    ) -> Subscription:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:  # subscribed outside a loop; nothing to hand over to
            loop = None
        subscription = Subscription(
            id=next(self._ids),
            tenant_id=tenant_id,
            user_id=user_id,
            queue=asyncio.Queue(maxsize=QUEUE_MAXSIZE),
            loop=loop,
            channels=set(channels or DEFAULT_CHANNELS),
        )
        self._subscriptions[subscription.id] = subscription
        return subscription

    def unsubscribe(self, subscription: Subscription) -> None:
        self._subscriptions.pop(subscription.id, None)

    @property
    def subscriptions(self) -> tuple[Subscription, ...]:
        return tuple(self._subscriptions.values())

    def subscriber_count(self, tenant_id: str, recipient: str) -> int:
        return sum(
            1
            for s in self._subscriptions.values()
            if s.tenant_id == tenant_id and s.user_id == recipient
        )

    def deliver(
        self,
        tenant_id: str,
        recipient: str,
        channel: NotificationChannel,
        message: dict,
    ) -> int:
        delivered = 0
        for subscription in list(self._subscriptions.values()):
            if subscription.tenant_id != tenant_id or subscription.user_id != recipient:
                continue
            if not subscription.accepts(channel):
                continue
            if self._offer(subscription, dict(message), channel):
                delivered += 1
        return delivered

    def _offer(
        self, subscription: Subscription, message: dict, channel: NotificationChannel
    ) -> bool:
        if subscription.queue.full():
            subscription.lagged += 1
            logger.warning(
                "notification socket is not keeping up; frame skipped",
                extra={"channel": channel.value, "lagged": subscription.lagged},
            )
            return False
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if subscription.loop is None or running is subscription.loop:
            try:
                subscription.queue.put_nowait(message)
            except asyncio.QueueFull:
                subscription.lagged += 1
                return False
            return True
        # Different loop: hand the frame to the loop that owns the socket.
        subscription.loop.call_soon_threadsafe(subscription.queue.put_nowait, message)
        return True
