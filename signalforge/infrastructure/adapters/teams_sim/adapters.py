"""teams-sim: a bridge that is scripted and live at the same time (REQ-033).

A war room needs a conversation to reason about, and a demo machine has no Teams
tenant. teams-sim is the substitute: a durable channel that plays a written
script *and* accepts whatever a person types into the bridge now, with both
ending up as messages in one stream. Everything it produces is labelled
SIMULATED, from the manifest through the canonical message to the screen.

The script is materialised rather than streamed - the whole conversation is
written at its scheduled times when the channel is first touched, and reads are
a time window over rows. Three things follow, and each is why it is done this
way:

- it follows the platform clock, so a drill at 100x plays the conversation at
  100x with no drill-aware code here;
- it is idempotent, because message ids are derived from the channel and the
  line's position, so replaying, restarting or racing produce one conversation;
- it survives a restart, because a schedule made of rows has nothing to lose.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from signalforge.contracts.canonical import CollabMessage, NotificationChannel
from signalforge.contracts.envelope import Actor, ActorType
from signalforge.contracts.errors import AdapterError
from signalforge.contracts.spi import CollaborationAdapter
from signalforge.domain.integrations.entities import StoredMessage
from signalforge.domain.integrations.ports import IntegrationUnitOfWork
from signalforge.domain.integrations.script import materialize, parse_script
from signalforge.domain.shared.errors import DomainValidationError
from signalforge.infrastructure.adapters.base import GenericAdapter
from signalforge.infrastructure.adapters.notify.adapters import NotifyAdapter

DEFAULT_POST_AS = "SignalForge"
DM_CHANNEL_PREFIX = "dm:"


class TeamsSimCollaborationAdapter(GenericAdapter, CollaborationAdapter):
    async def ping(self) -> bool:
        async with self.services.uow_factory() as uow:
            await uow.messages.channel_epoch(self.tenant_id, self._probe_channel())
        return True

    async def post(
        self, channel: str, text: str, thread_id: Optional[str] = None
    ) -> CollabMessage:
        body = (text or "").strip()
        if not body:
            raise AdapterError("a bridge message needs text")
        now = self.now()
        message = StoredMessage(
            tenant_id=self.tenant_id,
            app_id=self.app_id,
            channel=self._checked_channel(channel),
            message_id=f"live-{uuid.uuid4().hex[:24]}",
            author_type=ActorType.SYSTEM,
            author_id=str(self.config("post_as", DEFAULT_POST_AS)),
            author_display=str(self.config("post_as", DEFAULT_POST_AS)),
            text=body,
            sent_at=now,
            thread_id=thread_id,
            scripted=False,
        )
        async with self.services.uow_factory() as uow:
            await self._lay_down_script(uow, message.channel, fallback_epoch=now)
            await uow.messages.add(message)
            await uow.commit()
        return self._to_contract(message)

    async def fetch(
        self, channel: str, since: datetime, limit: int
    ) -> list[CollabMessage]:
        now = self.now()
        checked = self._checked_channel(channel)
        async with self.services.uow_factory() as uow:
            await self._lay_down_script(uow, checked, fallback_epoch=now)
            await uow.commit()
            messages = await uow.messages.read(
                self.tenant_id,
                checked,
                since=since,
                # The upper bound is what makes a script arrive in order: lines
                # dated after now exist as rows and are simply not visible yet.
                until=now,
                limit=min(limit, self.spec.limits.max_rows),
            )
        return [self._to_contract(message) for message in messages]

    # -- script ------------------------------------------------------------

    async def _lay_down_script(
        self, uow: IntegrationUnitOfWork, channel: str, *, fallback_epoch: datetime
    ) -> None:
        try:
            script = parse_script(self.config("script"))
        except DomainValidationError as exc:
            raise AdapterError(f"{self.spec.id} script is invalid: {exc}") from exc
        if not script:
            return
        epoch = (
            await uow.messages.channel_epoch(self.tenant_id, channel) or fallback_epoch
        )
        for message in materialize(
            tenant_id=self.tenant_id,
            app_id=self.app_id,
            channel=channel,
            script=script,
            epoch=epoch,
        ):
            # add() is a no-op for an id that already exists, so this is safe to
            # call on every read.
            await uow.messages.add(message)

    # -- helpers -----------------------------------------------------------

    def _probe_channel(self) -> str:
        return str(self.config("default_channel", "bridge"))

    def _checked_channel(self, channel: str) -> str:
        name = (channel or "").strip() or self._probe_channel()
        if len(name) > 200:
            raise AdapterError("channel names are at most 200 characters")
        return name

    def _to_contract(self, message: StoredMessage) -> CollabMessage:
        return CollabMessage(
            message_id=message.message_id,
            channel=message.channel,
            author=Actor(
                type=message.author_type,
                id=message.author_id,
                display=message.author_display,
            ),
            text=self.redact(message.text),
            sent_at=message.sent_at,
            thread_id=message.thread_id,
            simulated=True,
        )


class TeamsSimDmAdapter(NotifyAdapter):
    """Direct messages, as a notifications channel rather than a bridge.

    A DM is a notification with a receipt, not a conversation: what matters is
    whether the person was reached and whether they acknowledged. It writes into
    the same simulated message store so a responder sees one history, and into
    the delivery store so escalation can ask "were they told".
    """

    channels = frozenset({NotificationChannel.DM})

    async def _transmit(self, notification, delivery):
        now = self.now()
        channel = f"{DM_CHANNEL_PREFIX}{notification.recipient}"
        message = StoredMessage(
            tenant_id=self.tenant_id,
            app_id=self.app_id,
            channel=channel,
            message_id=f"dm-{notification.notification_id}",
            author_type=ActorType.SYSTEM,
            author_id=str(self.config("post_as", DEFAULT_POST_AS)),
            author_display=str(self.config("post_as", DEFAULT_POST_AS)),
            text=f"{notification.title}\n\n{notification.body}",
            sent_at=now,
            thread_id=notification.incident_id,
            scripted=False,
        )
        async with self.services.uow_factory() as uow:
            written = await uow.messages.add(message)
            await uow.commit()
        return self.sent(
            "delivered to the simulated DM channel "
            f"{channel}{'' if written else ' (already present)'}"
        )
