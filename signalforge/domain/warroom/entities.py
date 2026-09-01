"""The war room, who is in it, and the work it produces (REQ-045).

A war room exists because an incident was declared, and it is created by the
same act - there is no "open a war room" button, because a P0 with no room is a
state nobody wants to be able to reach. It ends when the incident closes, and it
is kept: what was decided in a room, by whom, at what time, is what a
post-incident review reads.

`RoomMember` records *why* somebody is in the room, not only that they are. The
distinction between required, present and paged is carried explicitly:

- **required** - the blast radius calls for this role
- **paged** - a page was sent, and its state is on the page row
- **present** - the person actually turned up (joined, or spoke on the bridge)

A room where all three are the same set is a room that went well. Where they
differ, the difference is the useful thing on the screen, and collapsing them
into one "members" list is how "we paged four people and two came" becomes
invisible.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import Enum
from typing import Any, Mapping, Optional

from signalforge.contracts.roles import Role
from signalforge.domain.onboarding.profile import Severity
from signalforge.domain.warroom.blast import BlastRadius, MembershipGap
from signalforge.domain.warroom.errors import WarRoomValidationError

MAX_TITLE_CHARS = 200
MAX_NOTE_CHARS = 2_000
MAX_MEMBERS = 200


class MemberSource(str, Enum):
    """How somebody came to be in the room.

    `blast_radius` is the platform's own answer; the other three are people
    acting. They are distinguished because "the room assembled itself and nobody
    came" and "three people joined on their own" look identical if you only
    store the list.
    """

    BLAST_RADIUS = "blast_radius"
    ESCALATION = "escalation"
    JOINED = "joined"
    INVITED = "invited"


@dataclass(frozen=True)
class RoomMember:
    tenant_id: str
    incident_id: str
    user_id: str
    role: Optional[Role]
    reason: str
    source: MemberSource
    added_at: datetime
    display_name: str = ""
    required: bool = False
    paged: bool = False
    present: bool = False
    joined_at: Optional[datetime] = None
    last_seen_at: Optional[datetime] = None

    def __post_init__(self) -> None:
        if not self.user_id:
            raise WarRoomValidationError("a room member must name a user")

    def arrived(self, *, at: datetime) -> "RoomMember":
        """Somebody joined, or spoke. Idempotent in the sense that matters: the
        first arrival time is kept, and only `last_seen_at` moves afterwards."""
        return replace(
            self,
            present=True,
            joined_at=self.joined_at or at,
            last_seen_at=at,
        )

    def was_paged(self) -> "RoomMember":
        return replace(self, paged=True)


class BriefSource(str, Enum):
    """Who wrote the join brief.

    `template` is deterministic text assembled from the incident's own facts.
    S5's model writes `model`, and `degraded` is what a failed model falls back
    to. The field exists now, before there is a model, so the screen that renders
    a brief never has to guess whether it is reading generated prose - and so the
    day an LLM starts writing these, every brief already says which ones it
    wrote.
    """

    TEMPLATE = "template"
    MODEL = "model"
    DEGRADED = "degraded"


@dataclass(frozen=True)
class JoinBrief:
    """What somebody walking into the room at minute forty needs to know.

    Sections rather than a paragraph, because the reader is on a phone with one
    hand and is looking for one thing: what is broken, how bad, what has been
    tried, what is being asked of them.
    """

    incident_id: str
    generated_at: datetime
    source: BriefSource
    headline: str
    what_is_happening: tuple[str, ...] = ()
    impact: tuple[str, ...] = ()
    what_we_know: tuple[str, ...] = ()
    what_has_been_done: tuple[str, ...] = ()
    what_we_need: tuple[str, ...] = ()
    change_context: tuple[str, ...] = ()
    caveats: tuple[str, ...] = ()
    """What this brief cannot tell you. Always populated when detection had no
    verdict at declare time, because a brief that reads confidently from thin
    evidence is worse than a short one."""

    def as_text(self) -> str:
        """Flat text, for a channel with no structure - an SMS, a voice call, a
        bridge post. One place, so the phone and the screen cannot drift."""
        blocks = [self.headline]
        for title, lines in (
            ("What is happening", self.what_is_happening),
            ("Impact", self.impact),
            ("What we know", self.what_we_know),
            ("What has been done", self.what_has_been_done),
            ("Recent changes", self.change_context),
            ("What we need", self.what_we_need),
            ("Caveats", self.caveats),
        ):
            if lines:
                blocks.append(f"{title}:\n" + "\n".join(f"- {line}" for line in lines))
        return "\n\n".join(blocks)


@dataclass(frozen=True)
class WarRoom:
    id: str
    tenant_id: str
    incident_id: str
    app_id: str
    opened_at: datetime
    opened_by: str
    severity: Severity
    radius: BlastRadius
    bridge_channel: str = ""
    """The collaboration channel this room's bridge lives in. Empty when no
    collaboration connector is bound - the room still exists and still pages;
    it simply has no transcript, which the screen says rather than hides."""
    bridge_adapter_id: str = ""
    bridge_binding_id: str = ""
    gaps: tuple[MembershipGap, ...] = ()
    closed_at: Optional[datetime] = None
    last_harvest_at: Optional[datetime] = None
    """Up to when the bridge has been read for commitments. The harvest is
    resumable rather than re-reading the whole transcript, and a restart picks up
    where it left off instead of raising every commitment a second time."""

    def __post_init__(self) -> None:
        if not self.incident_id:
            raise WarRoomValidationError("a war room belongs to an incident")

    @property
    def open(self) -> bool:
        return self.closed_at is None

    @property
    def has_bridge(self) -> bool:
        return bool(self.bridge_channel and self.bridge_binding_id)

    def closed(self, *, at: datetime) -> "WarRoom":
        return replace(self, closed_at=self.closed_at or at)

    def harvested(self, *, at: datetime) -> "WarRoom":
        return replace(self, last_harvest_at=at)

    def with_bridge(
        self, *, channel: str, adapter_id: str, binding_id: str
    ) -> "WarRoom":
        return replace(
            self,
            bridge_channel=channel,
            bridge_adapter_id=adapter_id,
            bridge_binding_id=binding_id,
        )


class TaskStatus(str, Enum):
    OPEN = "open"
    DONE = "done"
    CANCELLED = "cancelled"

    @property
    def settled(self) -> bool:
        return self in (TaskStatus.DONE, TaskStatus.CANCELLED)


class TaskSource(str, Enum):
    BRIDGE = "bridge"
    """Harvested from something somebody said on the bridge."""
    MANUAL = "manual"


@dataclass(frozen=True)
class IncidentTask:
    """A commitment somebody made, with an owner and a due time.

    The two fields that make it a task rather than a note are `owner_user_id` and
    `due_at`, and both are allowed to be empty - because a commitment harvested
    from a bridge often names a person this platform cannot resolve ("Priya will
    check the pool") or a time it cannot pin ("shortly"). An unresolved owner is
    kept as `owner_label` and the task is visibly unassigned, which somebody can
    fix in one click. Guessing would put a name against work nobody agreed to,
    and on a bridge at 03:00 that is how two people both think the other has it.
    """

    id: str
    tenant_id: str
    incident_id: str
    app_id: str
    title: str
    status: TaskStatus
    source: TaskSource
    created_at: datetime
    created_by: str
    owner_user_id: Optional[str] = None
    owner_label: str = ""
    due_at: Optional[datetime] = None
    due_label: str = ""
    note: str = ""
    evidence_ref: str = ""
    """The bridge message this came from, verbatim enough to find. A task nobody
    remembers agreeing to is settled by reading what was actually said."""
    completed_at: Optional[datetime] = None
    completed_by: Optional[str] = None
    updated_at: Optional[datetime] = None
    detail: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        title = str(self.title or "").strip()
        if not title:
            raise WarRoomValidationError("a task needs a title")
        object.__setattr__(self, "title", title[:MAX_TITLE_CHARS])
        object.__setattr__(self, "note", str(self.note or "")[:MAX_NOTE_CHARS])

    @property
    def assigned(self) -> bool:
        return bool(self.owner_user_id)

    def overdue(self, now: datetime) -> bool:
        return (
            self.status is TaskStatus.OPEN
            and self.due_at is not None
            and self.due_at <= now
        )

    def completed(self, *, at: datetime, by: str, note: str = "") -> "IncidentTask":
        if self.status is TaskStatus.DONE:
            return self
        if self.status is TaskStatus.CANCELLED:
            raise WarRoomValidationError(
                f"task {self.id} was cancelled and cannot be completed"
            )
        return replace(
            self,
            status=TaskStatus.DONE,
            completed_at=at,
            completed_by=by,
            note=note or self.note,
            updated_at=at,
        )

    def cancelled(self, *, at: datetime, by: str, note: str = "") -> "IncidentTask":
        if self.status is TaskStatus.DONE:
            raise WarRoomValidationError(
                f"task {self.id} is done; cancelling it would rewrite the record"
            )
        return replace(
            self,
            status=TaskStatus.CANCELLED,
            completed_at=at,
            completed_by=by,
            note=note or self.note,
            updated_at=at,
        )

    def reassigned(
        self,
        *,
        at: datetime,
        owner_user_id: Optional[str],
        owner_label: str = "",
        due_at: Optional[datetime] = None,
        due_label: str = "",
    ) -> "IncidentTask":
        if self.status.settled:
            raise WarRoomValidationError(
                f"task {self.id} is {self.status.value} and is no longer work"
            )
        return replace(
            self,
            owner_user_id=owner_user_id,
            owner_label=owner_label or self.owner_label,
            due_at=due_at if due_at is not None else self.due_at,
            due_label=due_label or self.due_label,
            updated_at=at,
        )
