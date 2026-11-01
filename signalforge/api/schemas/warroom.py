"""Response shapes for the war room (S4-T03).

Three things are published as separate fields rather than collapsed, and each is
a distinction the platform would lose if a client had to infer it.

- `required`, `paged` and `present` on a member. "Four were required, four were
  paged, two are here" is the sentence an incident commander needs at minute ten,
  and a single `status` cannot say it.
- `countdown_s` beside `ack_deadline_at` on a page. The deadline is the fact; the
  countdown is the same fact computed against the server's clock at the moment of
  the read, so a screen with a skewed clock still shows a timer that agrees with
  the escalation sweep.
- `source` on a brief. `template` today, `model` when S5's writer lands. A reader
  who cannot tell generated prose from assembled facts cannot calibrate how much
  to trust it, and the field is here before there is a model so no screen has to
  change when there is one.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import AwareDatetime, BaseModel, Field

from signalforge.application.warroom.bridge import BridgeMessage
from signalforge.application.warroom.service import WarRoomView
from signalforge.domain.warroom.entities import IncidentTask, JoinBrief, RoomMember
from signalforge.domain.warroom.paging import Page


# --- requests -----------------------------------------------------------------


class JoinIn(BaseModel):
    note: str = Field(
        default="",
        max_length=200,
        description="Optional: what you are here for. You always join as "
        "yourself - there is no field for joining somebody else.",
    )


class AckIn(BaseModel):
    note: str = Field(
        default="",
        max_length=500,
        description="Optional: what you are picking up. The acknowledgement is "
        "valid without it.",
    )


class PageIn(BaseModel):
    user_id: str = Field(description="Who to page. They must be in this tenant.")
    reason: str = Field(default="", max_length=500)
    role: Optional[str] = Field(
        default=None, description="The role they are being paged as, if any."
    )
    ack_timeout_s: Optional[int] = Field(
        default=None,
        ge=30,
        le=3600,
        description="How long they have before this escalates. Left out, the "
        "application's escalation policy decides, and failing that five minutes.",
    )


class BridgePostIn(BaseModel):
    text: str = Field(min_length=1, max_length=4000)


class TaskIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    owner_user_id: Optional[str] = None
    owner_label: str = Field(
        default="",
        max_length=200,
        description="A name for an owner this platform cannot resolve to a user. "
        "The task stays visibly unassigned rather than being given to a guess.",
    )
    due_at: Optional[AwareDatetime] = Field(
        default=None,
        description="Timezone-aware. A bare wall-clock time is refused: the "
        "board compares it against the server's clock to say *overdue*.",
    )
    note: str = Field(default="", max_length=2000)


class TaskPatchIn(BaseModel):
    owner_user_id: Optional[str] = None
    due_at: Optional[AwareDatetime] = None
    cancel: bool = False
    note: str = Field(default="", max_length=2000)


class TaskDoneIn(BaseModel):
    note: str = Field(default="", max_length=2000)


# --- responses ----------------------------------------------------------------


class BlastRadiusOut(BaseModel):
    summary: str
    regions: list[str] = []
    regions_total: int = 0
    journeys: list[str] = []
    services: list[str] = []
    customer_facing: bool = False
    journey_tier: Optional[int] = None
    truncated: bool = False
    source: str = Field(
        description="`proposal` when detection had a verdict at declare time, "
        "`declaration` when only the severity a person typed was available."
    )


class MembershipGapOut(BaseModel):
    role: str
    reason: str
    detail: str = ""


class RoleRequirementOut(BaseModel):
    role: str
    reason: str
    paged: bool = Field(
        description="False means present rather than woken. An executive is in "
        "the room for a P0; they are not paged for one."
    )


class MemberOut(BaseModel):
    user_id: str
    display_name: str = ""
    role: Optional[str] = None
    reason: str = ""
    source: str
    required: bool = False
    paged: bool = False
    present: bool = False
    joined_at: Optional[datetime] = None


class PageDeliveryOut(BaseModel):
    notification_id: str
    adapter_id: str
    channel: str
    status: str
    simulated: bool = False
    detail: str = ""


class PageOut(BaseModel):
    id: str
    incident_id: str
    recipient: str
    recipient_display: str = ""
    role: Optional[str] = None
    reason: str = ""
    state: str
    severity: str
    hop: int = 0
    ack_timeout_s: int
    created_at: datetime
    paged_at: Optional[datetime] = None
    ack_deadline_at: Optional[datetime] = None
    countdown_s: Optional[float] = Field(
        default=None,
        description="Seconds left to answer, computed against the server clock "
        "at the moment of this read. Negative once overdue, null once settled.",
    )
    overdue: bool = False
    acked_at: Optional[datetime] = None
    acked_by: Optional[str] = None
    ack_note: str = ""
    escalated_to_page_id: Optional[str] = None
    escalated_from_page_id: Optional[str] = None
    reached: bool = Field(
        default=False,
        description="At least one channel took it. False means nobody was "
        "reached, which escalates rather than waiting out a countdown.",
    )
    detail: str = ""
    deliveries: list[PageDeliveryOut] = []


class TaskOut(BaseModel):
    id: str
    incident_id: str
    title: str
    status: str
    source: str
    created_at: datetime
    created_by: str = ""
    owner_user_id: Optional[str] = None
    owner_label: str = ""
    assigned: bool = False
    due_at: Optional[datetime] = None
    due_label: str = ""
    overdue: bool = False
    note: str = ""
    evidence_ref: str = Field(
        default="",
        description="For a harvested commitment, what was actually said on the "
        "bridge. A task nobody remembers agreeing to is settled by reading it.",
    )
    completed_at: Optional[datetime] = None
    completed_by: Optional[str] = None


class WarRoomOut(BaseModel):
    id: str
    incident_id: str
    reference: str
    app_id: str
    severity: str
    state: str
    title: str
    opened_at: datetime
    opened_by: str
    closed_at: Optional[datetime] = None
    bridge_channel: Optional[str] = None
    bridge_adapter_id: Optional[str] = None
    has_bridge: bool = False
    blast_radius: BlastRadiusOut
    required_roles: list[RoleRequirementOut] = []
    gaps: list[MembershipGapOut] = Field(
        default=[],
        description="Required roles nobody holds for this application. Stated "
        "rather than silently omitted: a P0 with an empty comms lead seat is "
        "something to find out at minute two.",
    )
    members: list[MemberOut] = []
    present_count: int = 0
    pages: list[PageOut] = []
    tasks: list[TaskOut] = []


class BriefOut(BaseModel):
    incident_id: str
    generated_at: datetime
    source: str
    headline: str
    what_is_happening: list[str] = []
    impact: list[str] = []
    what_we_know: list[str] = []
    what_has_been_done: list[str] = []
    change_context: list[str] = []
    what_we_need: list[str] = []
    caveats: list[str] = []
    text: str = Field(
        description="The same brief flattened, for a channel with no structure - "
        "an SMS, a voice call, a bridge post. Built in one place so the phone "
        "and the screen cannot drift."
    )


class BridgeMessageOut(BaseModel):
    message_id: str
    author: str
    author_display: str = ""
    text: str
    at: datetime
    simulated: bool = False
    thread_id: Optional[str] = None


class BridgeOut(BaseModel):
    incident_id: str
    channel: Optional[str] = None
    has_bridge: bool = False
    detail: str = ""
    messages: list[BridgeMessageOut] = []


class PageListOut(BaseModel):
    count: int
    pages: list[PageOut] = []


class TaskListOut(BaseModel):
    count: int
    tasks: list[TaskOut] = []


# --- mappers ------------------------------------------------------------------


def page_out(page: Page, *, now: datetime) -> PageOut:
    return PageOut(
        id=page.id,
        incident_id=page.incident_id,
        recipient=page.recipient,
        recipient_display=page.recipient_display,
        role=page.role.value if page.role else None,
        reason=page.reason,
        state=page.state.value,
        severity=page.severity.value,
        hop=page.hop,
        ack_timeout_s=page.ack_timeout_s,
        created_at=page.created_at,
        paged_at=page.paged_at,
        ack_deadline_at=page.ack_deadline_at,
        countdown_s=page.countdown_s(now),
        overdue=page.overdue(now),
        acked_at=page.acked_at,
        acked_by=page.acked_by,
        ack_note=page.ack_note,
        escalated_to_page_id=page.escalated_to_page_id,
        escalated_from_page_id=page.escalated_from_page_id,
        reached=page.reached_anyone,
        detail=page.detail,
        deliveries=[
            PageDeliveryOut(
                notification_id=link.notification_id,
                adapter_id=link.adapter_id,
                channel=link.channel.value,
                status=link.status,
                simulated=link.simulated,
                detail=link.detail,
            )
            for link in page.deliveries
        ],
    )


def member_out(member: RoomMember) -> MemberOut:
    return MemberOut(
        user_id=member.user_id,
        display_name=member.display_name,
        role=member.role.value if member.role else None,
        reason=member.reason,
        source=member.source.value,
        required=member.required,
        paged=member.paged,
        present=member.present,
        joined_at=member.joined_at,
    )


def task_out(task: IncidentTask, *, now: datetime) -> TaskOut:
    return TaskOut(
        id=task.id,
        incident_id=task.incident_id,
        title=task.title,
        status=task.status.value,
        source=task.source.value,
        created_at=task.created_at,
        created_by=task.created_by,
        owner_user_id=task.owner_user_id,
        owner_label=task.owner_label,
        assigned=task.assigned,
        due_at=task.due_at,
        due_label=task.due_label,
        overdue=task.overdue(now),
        note=task.note,
        evidence_ref=task.evidence_ref,
        completed_at=task.completed_at,
        completed_by=task.completed_by,
    )


def war_room_out(view: WarRoomView, *, now: datetime) -> WarRoomOut:
    room = view.room
    return WarRoomOut(
        id=room.id,
        incident_id=room.incident_id,
        reference=view.incident.reference,
        app_id=room.app_id,
        severity=room.severity.value,
        state=view.incident.state.value,
        title=view.incident.title,
        opened_at=room.opened_at,
        opened_by=room.opened_by,
        closed_at=room.closed_at,
        bridge_channel=room.bridge_channel or None,
        bridge_adapter_id=room.bridge_adapter_id or None,
        has_bridge=room.has_bridge,
        blast_radius=BlastRadiusOut(
            summary=room.radius.summary(),
            regions=list(room.radius.regions),
            regions_total=room.radius.regions_total,
            journeys=list(room.radius.journeys),
            services=list(room.radius.services),
            customer_facing=room.radius.customer_facing,
            journey_tier=room.radius.journey_tier,
            truncated=room.radius.truncated,
            source=room.radius.source,
        ),
        required_roles=[
            RoleRequirementOut(
                role=requirement.role.value,
                reason=requirement.reason,
                paged=requirement.paged,
            )
            for requirement in view.requirements
        ],
        gaps=[
            MembershipGapOut(role=gap.role.value, reason=gap.reason, detail=gap.detail)
            for gap in room.gaps
        ],
        members=[member_out(member) for member in view.members],
        present_count=len(view.present),
        pages=[page_out(page, now=now) for page in view.pages],
        tasks=[task_out(task, now=now) for task in view.tasks],
    )


def brief_out(brief: JoinBrief) -> BriefOut:
    return BriefOut(
        incident_id=brief.incident_id,
        generated_at=brief.generated_at,
        source=brief.source.value,
        headline=brief.headline,
        what_is_happening=list(brief.what_is_happening),
        impact=list(brief.impact),
        what_we_know=list(brief.what_we_know),
        what_has_been_done=list(brief.what_has_been_done),
        change_context=list(brief.change_context),
        what_we_need=list(brief.what_we_need),
        caveats=list(brief.caveats),
        text=brief.as_text(),
    )


def bridge_out(
    incident_id: str,
    messages: tuple[BridgeMessage, ...],
    *,
    channel: str = "",
    has_bridge: bool = False,
) -> BridgeOut:
    return BridgeOut(
        incident_id=incident_id,
        channel=channel or None,
        has_bridge=has_bridge,
        detail=(
            ""
            if has_bridge
            else "no collaboration connector is bound to this application, so this "
            "war room has no bridge"
        ),
        messages=[
            BridgeMessageOut(
                message_id=message.message_id,
                author=message.author,
                author_display=message.author_display,
                text=message.text,
                at=message.at,
                simulated=message.simulated,
                thread_id=message.thread_id,
            )
            for message in messages
        ],
    )


def page_list_out(pages, *, now: datetime) -> PageListOut:
    rows = [page_out(page, now=now) for page in pages]
    return PageListOut(count=len(rows), pages=rows)


def task_list_out(tasks, *, now: datetime) -> TaskListOut:
    rows = [task_out(task, now=now) for task in tasks]
    return TaskListOut(count=len(rows), tasks=rows)
