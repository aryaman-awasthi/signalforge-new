"""The live bridge, and turning what is said on it into work (REQ-045).

Two jobs, and the second is the one that earns the file. Reading and writing a
collaboration channel is plumbing over the frozen SPI. Harvesting commitments out
of it is the thing bridges are bad at: somebody says "I'll roll back region-b in
ten minutes", four other things happen, and forty minutes later nobody has and
everybody assumed somebody had.

Three properties hold the harvest together:

- **Idempotent.** A commitment's key is derived from the message and the matched
  text (`domain/warroom/commitments.py`), and the write looks the key up before
  it inserts. Re-reading the transcript, restarting the process and two workers
  racing each other all produce one task.
- **Resumable.** The room carries `last_harvest_at`. A sweep reads from there
  forward rather than re-reading the whole conversation, so a room that has been
  open for six hours costs the same as one open for six minutes.
- **Conservative about people.** A commitment naming somebody the platform
  cannot resolve to a user becomes an *unassigned* task carrying the name as a
  label. Guessing a user id from a first name is how the wrong person gets
  paged, and on a bridge at 03:00 an unowned task on the board is recoverable
  while a wrongly-owned one is not.
- **Redacted at the boundary, at write time.** A bridge message is free text a
  human typed under pressure, and the harvester stores an excerpt of it on a
  task's `evidence_ref` - so a card number pasted onto the bridge would land on
  the task board, in the event stream and in the audit chain. S3-T02's ingest
  floor is the same policy for the same reason, so it is *reused* rather than
  reimplemented: `domain/ingest/redaction.mask` is the only masking code in the
  platform, and the two paths cannot drift apart. It is applied on the way out
  (so nothing unmasked reaches the collaboration tool either) and on the way in
  (so a line typed on the far side, or a scripted drill line, is masked before
  it can become evidence).
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable, Optional, Sequence

from signalforge.application.adapters.invoker import ConnectorInvoker
from signalforge.application.events.bus import EventBus
from signalforge.application.identity.principal import Principal
from signalforge.application.identity.uow import IdentityUnitOfWork
from signalforge.application.warroom.common import (
    BRIDGE_POSTED_TOPIC,
    TASK_CREATED_TOPIC,
    publish_system,
    record,
)
from signalforge.contracts.canonical import CollabMessage
from signalforge.contracts.errors import AdapterError
from signalforge.domain.identity.ports import Clock
from signalforge.domain.ingest.redaction import POLICY_ID as REDACTION_POLICY_ID
from signalforge.domain.ingest.redaction import mask
from signalforge.domain.shared.errors import DomainError
from signalforge.domain.warroom.commitments import Commitment, harvest
from signalforge.domain.warroom.entities import (
    IncidentTask,
    TaskSource,
    TaskStatus,
    WarRoom,
)
from signalforge.domain.warroom.errors import WarRoomNotFoundError

logger = logging.getLogger("signalforge.warroom")

POST = "post"
FETCH = "fetch"
MAX_MESSAGES = 200
HARVEST_LOOKBACK_H = 6
"""How far back a first harvest reads. A room with no cursor yet is read from
its own opening time, bounded by this, so a bridge that was busy before the
incident was declared does not fill the board with pre-incident chatter."""

OPENING_SLACK_S = 1
"""Why "since the room opened" means one second before it.

The frozen collaboration SPI's `fetch(channel, since, limit)` is exclusive -
`sent_at > since` - so a message sent in the same instant the room opened is
invisible to a read that starts exactly at `opened_at`. On wall-clock time that
is a millisecond-wide race nobody would hit. On a **virtual clock** it is
certain: a drill can open a room and play its first scripted line without the
clock moving at all, and the whole transcript would read as silence.

One second of slack costs nothing - the window still starts at the room, and
nothing said before the incident existed is in range - and it makes the boundary
inclusive on every clock. Fixed here rather than in the adapter, because the SPI's
`since` semantics are frozen and already have tests of their own."""


@dataclass(frozen=True)
class BridgeMessage:
    """One line of the transcript, in the shape the S9 panel renders.

    `scripted` distinguishes a rehearsed line from a live one, which the teams-sim
    adapter already tracks. The panel shows both in one stream on purpose - a war
    room that separated them into two panes would rehearse nothing useful - and
    the flag is there so the reader can tell.
    """

    message_id: str
    author: str
    author_display: str
    text: str
    at: datetime
    simulated: bool = False
    thread_id: Optional[str] = None


@dataclass(frozen=True)
class HarvestSweep:
    rooms: int = 0
    commitments: int = 0
    created: int = 0
    duplicates: int = 0

    def __str__(self) -> str:
        return (
            f"{self.rooms} room(s) with a bridge: {self.commitments} commitment(s) "
            f"found, {self.created} new task(s), {self.duplicates} already held"
        )


class BridgeService:
    def __init__(
        self,
        uow_factory: Callable[[], IdentityUnitOfWork],
        bus: EventBus,
        clock: Clock,
        invoker: ConnectorInvoker,
        rooms=None,
        metrics=None,
    ):
        self._uow_factory = uow_factory
        self._bus = bus
        self._clock = clock
        self._invoker = invoker
        self._rooms = rooms
        self._metrics = metrics

    # -- reading and writing -------------------------------------------------

    async def read(
        self, principal: Principal, incident_id: str, *, limit: int = MAX_MESSAGES
    ) -> tuple[BridgeMessage, ...]:
        """The transcript. Empty with a reason when no bridge is bound.

        An install with no collaboration connector still runs incidents, still
        pages and still tracks tasks; the panel says there is no bridge rather
        than the room failing to open, which is what "the platform degrades" has
        to mean in practice.
        """
        room = await self._room(principal.tenant_id, incident_id)
        if not room.has_bridge:
            return ()
        messages = await self._fetch(room, since=_opened(room), limit=limit)
        return tuple(_as_message(message) for message in messages)

    async def post(
        self, principal: Principal, incident_id: str, text: str
    ) -> BridgeMessage:
        """Say something on the bridge.

        Carries no permission, and needs none: the author is the session and
        cannot be anybody else. Speaking also marks you present, because a room
        whose "who is in the room" panel omitted the person currently typing
        would be worse than no panel.
        """
        room = await self._room(principal.tenant_id, incident_id)
        if not room.has_bridge:
            raise WarRoomNotFoundError(
                "no collaboration connector is bound to this application, so this "
                "war room has no bridge to post to"
            )
        typed = str(text or "").strip()
        if not typed:
            raise WarRoomNotFoundError("a bridge message needs text")
        # The floor, before anything else sees the text: the collaboration tool,
        # the transcript, the harvester's evidence excerpt and the event payload
        # all read `body`, and none of them is a place to discover a card number.
        body = mask(typed)
        redacted = body != typed

        posted: CollabMessage = await self._invoker.invoke(
            principal.tenant_id,
            room.bridge_binding_id,
            POST,
            room.bridge_channel,
            f"{principal.display_name or principal.user_id}: {body}"[:4000],
        )
        async with self._uow_factory() as uow:
            await record(
                uow,
                self._bus,
                self._clock,
                principal=principal,
                app_id=room.app_id,
                incident_id=incident_id,
                topic=BRIDGE_POSTED_TOPIC,
                payload={
                    "incident_id": incident_id,
                    "message_id": posted.message_id,
                    "channel": room.bridge_channel,
                    "adapter_id": room.bridge_adapter_id,
                    "chars": len(body),
                    # "something was removed from this message", not "a policy
                    # exists" - the same distinction the ingest envelope draws.
                    "redacted": redacted,
                    "redaction_policy": REDACTION_POLICY_ID if redacted else "",
                },
            )
            await uow.commit()
        # Harvest this one message now, while we know with certainty who said it.
        #
        # The frozen collaboration SPI's `post(channel, text, thread_id)` carries
        # no author: the adapter writes the transcript under the *connector's*
        # identity, which is correct - that is who the far side sees posting. So a
        # later sweep reading the transcript back cannot tell that "I'll roll back
        # region-b" was said by this session, and would leave the task unowned.
        # Here the session *is* the author and there is nothing to infer.
        #
        # The sweep still reads the same message afterwards and creates nothing,
        # because the commitment key is derived from the message id: the two paths
        # compose through the same idempotency guard rather than having to know
        # about each other. What the sweep alone finds - scripted lines, anybody
        # posting from the far side - genuinely has no SignalForge author and
        # stays unassigned, which is the honest answer.
        await self._harvest_one(room, posted, said_by=principal.user_id)
        if self._rooms is not None:
            await self._rooms.mark_present(
                principal.tenant_id,
                incident_id,
                principal.user_id,
                display_name=principal.display_name or "",
            )
        return _as_message(posted)

    async def announce(self, room: WarRoom, text: str) -> None:
        """The platform saying something on the bridge - never raises.

        Used for the join brief and for escalations. A bridge that is down must
        not be able to stop an escalation, so this is the one call in the file
        that swallows.
        """
        if not room.has_bridge:
            return
        try:
            await self._invoker.invoke(
                room.tenant_id, room.bridge_binding_id, POST, room.bridge_channel, text
            )
        except (AdapterError, DomainError) as exc:
            logger.info(
                "the bridge could not be posted to; the war room is unaffected",
                extra={
                    "incident_id": room.incident_id,
                    "error": f"{type(exc).__name__}: {exc}",
                },
            )

    # -- harvesting ----------------------------------------------------------

    async def harvest_all(self, limit: int = 25) -> HarvestSweep:
        """Read every open bridge forward from its cursor. One job per install."""
        async with self._uow_factory() as uow:
            rooms = list(await uow.war_rooms.needing_harvest(limit))
        commitments = created = duplicates = 0
        for room in rooms:
            try:
                found, new = await self.harvest_room(room)
            except Exception as exc:  # pragma: no cover - defensive by design
                logger.warning(
                    "a bridge could not be harvested",
                    extra={
                        "incident_id": room.incident_id,
                        "error": f"{type(exc).__name__}: {exc}",
                    },
                )
                continue
            commitments += found
            created += new
            duplicates += found - new
        return HarvestSweep(
            rooms=len(rooms),
            commitments=commitments,
            created=created,
            duplicates=duplicates,
        )

    async def harvest_room(self, room: WarRoom) -> tuple[int, int]:
        """One room's bridge, from its cursor forward. Returns (found, created)."""
        since = room.last_harvest_at or max(
            _opened(room),
            self._clock.now() - timedelta(hours=HARVEST_LOOKBACK_H),
        )
        messages = await self._fetch(room, since=since, limit=MAX_MESSAGES)
        if not messages:
            await self._advance_cursor(room)
            return 0, 0

        found = harvest(
            [
                {
                    "message_id": message.message_id,
                    "author": _author_id(message),
                    # Masked before the harvester sees it, because what the
                    # harvester keeps is an evidence excerpt on a durable row.
                    # A line typed on the far side never passed the ingest floor.
                    "text": mask(message.text),
                    "at": message.sent_at,
                }
                for message in messages
            ],
            now=self._clock.now(),
            since=since,
            not_people=await self._not_people(room),
        )
        created = 0
        for commitment in found:
            if await self._store(room, commitment):
                created += 1
        await self._advance_cursor(
            room, at=max(message.sent_at for message in messages)
        )
        return len(found), created

    async def _harvest_one(
        self, room: WarRoom, message: CollabMessage, *, said_by: str
    ) -> None:
        """Read one just-posted message for a commitment. Never raises.

        A bad line on the bridge must not fail the post: somebody typing during a
        P0 gets their message through whatever this makes of it.
        """
        try:
            found = harvest(
                [
                    {
                        "message_id": message.message_id,
                        "author": said_by,
                        # Already masked at post time; masked again because this
                        # is the text that becomes durable evidence and the cost
                        # of applying an idempotent floor twice is nothing.
                        "text": mask(message.text),
                        "at": message.sent_at,
                    }
                ],
                now=self._clock.now(),
                not_people=await self._not_people(room),
            )
            for commitment in found:
                await self._store(room, commitment)
        except Exception as exc:  # pragma: no cover - defensive by design
            logger.warning(
                "a bridge message could not be harvested; it is still posted",
                extra={
                    "incident_id": room.incident_id,
                    "error": f"{type(exc).__name__}: {exc}",
                },
            )

    async def _store(self, room: WarRoom, commitment: Commitment) -> bool:
        """One commitment, written once. Returns whether it was new."""
        async with self._uow_factory() as uow:
            existing = await uow.incident_tasks.find_by_key(
                room.tenant_id, room.incident_id, commitment.key
            )
            if existing is not None:
                return False
            owner = await self._resolve_owner(uow, room, commitment)
            task = IncidentTask(
                id=str(uuid.uuid4()),
                tenant_id=room.tenant_id,
                incident_id=room.incident_id,
                app_id=room.app_id,
                title=commitment.title,
                status=TaskStatus.OPEN,
                source=TaskSource.BRIDGE,
                created_at=self._clock.now(),
                created_by=commitment.said_by or "bridge",
                owner_user_id=owner,
                owner_label=commitment.owner_hint
                or (commitment.said_by if commitment.owner_is_speaker else ""),
                due_at=commitment.due_at,
                due_label=commitment.due_label,
                evidence_ref=commitment.evidence,
                detail={"key": commitment.key, "message_id": commitment.message_id},
            )
            await uow.incident_tasks.add(task)
            await publish_system(
                uow,
                self._bus,
                self._clock,
                tenant_id=room.tenant_id,
                app_id=room.app_id,
                incident_id=room.incident_id,
                topic=TASK_CREATED_TOPIC,
                payload={
                    "incident_id": room.incident_id,
                    "task_id": task.id,
                    "title": task.title,
                    "source": task.source.value,
                    "owner_user_id": task.owner_user_id,
                    "owner_label": task.owner_label,
                    "assigned": task.assigned,
                    "due_at": task.due_at.isoformat() if task.due_at else None,
                    "due_label": task.due_label,
                    "said_by": commitment.said_by,
                    "evidence": commitment.evidence,
                },
            )
            await uow.commit()
        if self._metrics is not None:
            self._metrics.increment(
                "signalforge_incident_tasks_total",
                {
                    "source": TaskSource.BRIDGE.value,
                    "assigned": "true" if owner else "false",
                },
            )
        return True

    async def _resolve_owner(
        self, uow: IdentityUnitOfWork, room: WarRoom, commitment: Commitment
    ) -> Optional[str]:
        """A commitment's owner, only when it is unambiguous.

        Three ways to be sure, and nothing else counts:

        1. The speaker committed themselves and the bridge author is a user id
           this tenant holds.
        2. The name matches exactly one user's display name, case-insensitively.
        3. The name matches exactly one user's email local part.

        Two matches is no match. A room with a Sam Patel and a Sam Okafor gets an
        unassigned task labelled `Sam`, which somebody fixes in one click - and
        the alternative is paging the wrong Sam at 03:00.
        """
        members = list(await uow.users.list(room.tenant_id))
        if commitment.owner_is_speaker:
            said_by = commitment.said_by
            return said_by if any(user.id == said_by for user in members) else None
        hint = commitment.owner_hint.strip().lower()
        if not hint:
            return None
        for attribute in ("display_name", "email_local"):
            matches = [
                user
                for user in members
                if _attribute(user, attribute) == hint
            ]
            if len(matches) == 1:
                return matches[0].id
        return None

    async def _not_people(self, room: WarRoom) -> tuple[str, ...]:
        """Capitalised words in this room that are systems rather than humans.

        The application and everything its topology names. Without this,
        "Checkout will recover once the pool drains" becomes a task owned by
        somebody called Checkout.
        """
        names = {room.app_id}
        names.update(room.radius.services)
        return tuple(sorted(name for name in names if name))

    async def _advance_cursor(
        self, room: WarRoom, *, at: Optional[datetime] = None
    ) -> None:
        async with self._uow_factory() as uow:
            current = await uow.war_rooms.get(room.tenant_id, room.incident_id)
            if current is None:
                return
            await uow.war_rooms.update(
                current.harvested(at=at or self._clock.now())
            )
            await uow.commit()

    # -- internals -----------------------------------------------------------

    async def _room(self, tenant_id: str, incident_id: str) -> WarRoom:
        async with self._uow_factory() as uow:
            room = await uow.war_rooms.get(tenant_id, incident_id)
        if room is None:
            raise WarRoomNotFoundError(f"incident {incident_id} has no war room")
        return room

    async def _fetch(
        self, room: WarRoom, *, since: datetime, limit: int
    ) -> list[CollabMessage]:
        try:
            return list(
                await self._invoker.invoke(
                    room.tenant_id,
                    room.bridge_binding_id,
                    FETCH,
                    room.bridge_channel,
                    since,
                    limit,
                )
            )
        except (AdapterError, DomainError) as exc:
            logger.info(
                "the bridge could not be read",
                extra={
                    "incident_id": room.incident_id,
                    "error": f"{type(exc).__name__}: {exc}",
                },
            )
            return []


# --- pure helpers -------------------------------------------------------------


def _opened(room: WarRoom) -> datetime:
    """The room's opening moment, made inclusive. See `OPENING_SLACK_S`."""
    return room.opened_at - timedelta(seconds=OPENING_SLACK_S)


def _attribute(user, which: str) -> str:
    if which == "display_name":
        return (user.display_name or "").strip().lower()
    return (user.email or "").split("@", 1)[0].strip().lower()


def _author_id(message: CollabMessage) -> str:
    return message.author.id if message.author else ""


def _as_message(message: CollabMessage) -> BridgeMessage:
    """One transcript line, masked.

    The S9 panel renders whatever the collaboration tool holds, and that includes
    lines this platform did not write - a scripted drill line, somebody typing in
    Teams. Masking here rather than only at post time means the floor covers the
    transcript by origin-independent construction.
    """
    return BridgeMessage(
        message_id=message.message_id,
        author=_author_id(message),
        author_display=(message.author.display or "") if message.author else "",
        text=mask(message.text),
        at=message.sent_at,
        simulated=message.simulated,
        thread_id=message.thread_id,
    )
