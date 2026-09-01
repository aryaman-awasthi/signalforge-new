"""The page: its states, its countdown and where it escalates to (REQ-045).

A page is a question put to one person - "can you take this?" - and the whole
design is about the case where they do not answer. Three rules are permanent and
are enforced here rather than in a service:

1. **Acknowledgement is terminal and idempotent.** A double-clicked button, a
   redelivered socket frame and a mobile page tapped twice on a train are one
   act. Nothing moves a page out of `acked` - not an escalation sweep, not a
   commander, not a redelivery.
2. **The countdown is derived from stored times, never from a timer.** A page
   holds `paged_at` and `ack_deadline_at`; how long is left is arithmetic
   against a clock the caller passes in. A process that restarts, a worker that
   was asleep and a screen that was closed all read the same number, and a
   virtual clock at 100x makes a drill's countdown run at 100x with no
   drill-aware code.
3. **Escalation is bounded and never silent.** Each hop is recorded, the chain
   cannot loop back to somebody already paged for this page, and running out of
   people raises rather than stopping quietly - because the moment nobody is left
   to page is the moment a human has to be told.

The state machine is small and every edge is here:

    pending ──sent──▶ sent ──ack──▶ acked        (terminal, human only)
       │                │
       │                ├──timeout──▶ escalated  (a successor page exists)
       └──no channel──▶ failed                   (terminal: nobody was reached)
                        │
       cancelled ◀──incident closed──┘           (terminal)
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from enum import Enum
from typing import Mapping, Optional, Sequence

from signalforge.contracts.canonical import NotificationChannel
from signalforge.contracts.roles import Role
from signalforge.domain.identity.oncall import (
    EscalationPolicy,
    EscalationTargetType,
    OnCallRotation,
)
from signalforge.domain.onboarding.profile import Severity
from signalforge.domain.warroom.errors import (
    EscalationExhaustedError,
    NotYourPageError,
    PageAlreadySettledError,
    WarRoomValidationError,
)

DEFAULT_ACK_TIMEOUT_S = 300
"""Five minutes to answer a page, when no escalation policy says otherwise.

Long enough to reach a phone and short enough that a P0 does not sit unanswered
for a quarter of an hour. It is the fallback, not the policy: an org that
configured escalation steps gets its own timers."""

MIN_ACK_TIMEOUT_S = 30
MAX_ACK_TIMEOUT_S = 3_600
MAX_ESCALATION_HOPS = 10
"""The same ceiling `EscalationPolicy` puts on steps. A chain longer than this is
not an escalation, it is a phone tree."""


class PageState(str, Enum):
    PENDING = "pending"
    SENT = "sent"
    ACKED = "acked"
    ESCALATED = "escalated"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @property
    def settled(self) -> bool:
        """Nothing more will happen to this page on its own."""
        return self in (
            PageState.ACKED,
            PageState.ESCALATED,
            PageState.FAILED,
            PageState.CANCELLED,
        )

    @property
    def awaiting(self) -> bool:
        """Still counting down. What the ack board's timer column is drawn from."""
        return self in (PageState.PENDING, PageState.SENT)


PAGE_TRANSITIONS: Mapping[PageState, frozenset[PageState]] = {
    PageState.PENDING: frozenset(
        {
            PageState.SENT,
            PageState.ACKED,
            PageState.FAILED,
            PageState.ESCALATED,
            PageState.CANCELLED,
        }
    ),
    PageState.SENT: frozenset(
        {PageState.ACKED, PageState.ESCALATED, PageState.CANCELLED}
    ),
    # All terminal. `failed` in particular: a page that never reached anybody
    # must not later report that somebody answered it, or an escalation could be
    # silenced by a delivery that did not happen (the same floor the S3 delivery
    # state machine holds).
    PageState.ACKED: frozenset(),
    PageState.ESCALATED: frozenset(),
    PageState.FAILED: frozenset(),
    PageState.CANCELLED: frozenset(),
}


@dataclass(frozen=True)
class PageDelivery:
    """A link to one delivery receipt (S3), not a second copy of it.

    `notification_deliveries` remains the system of record for whether a person
    was told; this row records which receipts belong to this page, so a board can
    show "SMS and in-app, one of them acknowledged" without the war room owning a
    second, divergent notion of delivery.
    """

    notification_id: str
    adapter_id: str
    channel: NotificationChannel
    status: str = "queued"
    simulated: bool = False
    detail: str = ""


CHANNELS_BY_SEVERITY: Mapping[Severity, tuple[NotificationChannel, ...]] = {
    Severity.P0: (
        NotificationChannel.VOICE,
        NotificationChannel.SMS,
        NotificationChannel.IN_APP,
        NotificationChannel.BROWSER_PUSH,
        NotificationChannel.DM,
    ),
    Severity.P1: (
        NotificationChannel.SMS,
        NotificationChannel.IN_APP,
        NotificationChannel.BROWSER_PUSH,
        NotificationChannel.DM,
    ),
    Severity.P2: (
        NotificationChannel.IN_APP,
        NotificationChannel.BROWSER_PUSH,
        NotificationChannel.DM,
    ),
    Severity.P3: (NotificationChannel.IN_APP, NotificationChannel.DM),
}
"""Which channels a page tries, by severity. A P0 rings a phone; a P3 does not.

Waking somebody at 03:00 for a P3 is how a pager gets muted, and a muted pager is
how a P0 is missed - so the escalation ladder starts with the severity rather
than with a per-page choice somebody has to remember to make."""


def channels_for(severity: Severity) -> tuple[NotificationChannel, ...]:
    return CHANNELS_BY_SEVERITY.get(severity, CHANNELS_BY_SEVERITY[Severity.P2])


@dataclass(frozen=True)
class Page:
    id: str
    tenant_id: str
    incident_id: str
    app_id: str
    recipient: str
    """The user id this page is addressed to. The only person who may
    acknowledge it, whatever anybody's role says."""
    role: Optional[Role]
    reason: str
    state: PageState
    created_at: datetime
    ack_timeout_s: int = DEFAULT_ACK_TIMEOUT_S
    recipient_display: str = ""
    severity: Severity = Severity.P2
    paged_at: Optional[datetime] = None
    ack_deadline_at: Optional[datetime] = None
    acked_at: Optional[datetime] = None
    acked_by: Optional[str] = None
    ack_note: str = ""
    escalated_at: Optional[datetime] = None
    escalated_to_page_id: Optional[str] = None
    escalated_from_page_id: Optional[str] = None
    hop: int = 0
    """How many escalations deep this page is. Zero is the first person asked."""
    rotation_id: Optional[str] = None
    policy_id: Optional[str] = None
    policy_step: int = 0
    detail: str = ""
    deliveries: tuple[PageDelivery, ...] = ()
    requested_by: str = ""
    requested_by_type: str = "system"

    def __post_init__(self) -> None:
        if not self.recipient:
            raise WarRoomValidationError(
                "a page must name the person it is addressed to; a page to nobody "
                "is a countdown nobody can stop"
            )
        if not 0 <= self.hop <= MAX_ESCALATION_HOPS:
            raise WarRoomValidationError(
                f"escalation hop must be 0..{MAX_ESCALATION_HOPS}"
            )
        timeout = int(self.ack_timeout_s)
        object.__setattr__(
            self,
            "ack_timeout_s",
            max(MIN_ACK_TIMEOUT_S, min(timeout, MAX_ACK_TIMEOUT_S)),
        )

    # -- reads ---------------------------------------------------------------

    def countdown_s(self, now: datetime) -> Optional[float]:
        """Seconds left to answer; negative once overdue, None once settled.

        Derived rather than stored, so every reader agrees: the ack board, the
        escalation sweep and a mobile page opened ten minutes later all compute
        it from the same two timestamps.
        """
        if self.state.settled or self.ack_deadline_at is None:
            return None
        return (self.ack_deadline_at - now).total_seconds()

    def overdue(self, now: datetime) -> bool:
        remaining = self.countdown_s(now)
        return remaining is not None and remaining <= 0

    @property
    def reached_anyone(self) -> bool:
        """At least one channel took the notification. A page whose every channel
        refused is a page nobody received, and it escalates immediately rather
        than after a countdown nobody is watching."""
        return any(
            delivery.status not in ("failed", "") for delivery in self.deliveries
        )

    # -- moves ---------------------------------------------------------------

    def _moved(self, target: PageState) -> None:
        if target not in PAGE_TRANSITIONS[self.state]:
            raise PageAlreadySettledError(
                f"page {self.id} is {self.state.value} and cannot become "
                f"{target.value}"
            )

    def sent(
        self, *, at: datetime, deliveries: Sequence[PageDelivery], detail: str = ""
    ) -> "Page":
        self._moved(PageState.SENT)
        return replace(
            self,
            state=PageState.SENT,
            paged_at=at,
            ack_deadline_at=at + timedelta(seconds=self.ack_timeout_s),
            deliveries=tuple(deliveries),
            detail=detail or self.detail,
        )

    def failed(self, *, at: datetime, detail: str) -> "Page":
        """No channel could carry it. Terminal, and the reason is kept."""
        self._moved(PageState.FAILED)
        return replace(self, state=PageState.FAILED, paged_at=at, detail=detail)

    def acknowledged(self, *, at: datetime, by: str, note: str = "") -> "Page":
        """The human override. Idempotent, terminal, and only by the addressee.

        `by` is checked here rather than in the service because it is the whole
        control: this route carries no permission (the frozen matrix gives the
        SME none), so "the page names you" *is* the authorization.
        """
        if by != self.recipient:
            raise NotYourPageError(
                "a page is acknowledged by the person it was addressed to"
            )
        if self.state is PageState.ACKED:
            # Not an error. A redelivered frame and a second tap are one act.
            return self
        self._moved(PageState.ACKED)
        return replace(
            self,
            state=PageState.ACKED,
            acked_at=at,
            acked_by=by,
            ack_note=str(note or "")[:500],
        )

    def escalated(self, *, at: datetime, to_page_id: str) -> "Page":
        self._moved(PageState.ESCALATED)
        return replace(
            self,
            state=PageState.ESCALATED,
            escalated_at=at,
            escalated_to_page_id=to_page_id,
        )

    def cancelled(self, *, at: datetime, detail: str = "") -> "Page":
        """The incident closed, or the room stood down, while this was ringing.

        Cancelling an acknowledged page is refused by the transition table: what
        somebody already took responsibility for is not un-taken by the incident
        ending.
        """
        self._moved(PageState.CANCELLED)
        return replace(
            self,
            state=PageState.CANCELLED,
            escalated_at=at,
            detail=detail or "the incident was closed while this page was open",
        )

    def with_deliveries(self, deliveries: Sequence[PageDelivery]) -> "Page":
        return replace(self, deliveries=tuple(deliveries))


# --- escalation ---------------------------------------------------------------


@dataclass(frozen=True)
class EscalationTarget:
    """Who the next page goes to, and the sentence explaining the hop."""

    user_id: str
    reason: str
    rotation_id: Optional[str] = None
    policy_id: Optional[str] = None
    policy_step: int = 0
    timeout_s: int = DEFAULT_ACK_TIMEOUT_S


def next_target(
    page: Page,
    *,
    rotation: Optional[OnCallRotation],
    policy: Optional[EscalationPolicy],
    already_paged: Sequence[str] = (),
    candidates: Sequence[str] = (),
) -> EscalationTarget:
    """Who to wake next when a page goes unanswered.

    Three sources, tried in the order that respects what an org actually
    configured:

    1. **The rotation this page came from.** `next_in_rotation` is the on-call
       promise: the next person on the roster, which is who the team agreed
       would answer. Members already paged for this chain are skipped, so a
       three-person rotation escalates twice and then stops rather than ringing
       the first person again.
    2. **The escalation policy's next step**, when the rotation is exhausted or
       there was never one. A step naming a rotation resolves to whoever it puts
       first among people not yet paged.
    3. **Anybody else holding a required role**, as a last resort, so a room with
       a bound tech lead and no configured rotation still escalates to somebody.

    Running out raises `EscalationExhaustedError`. That is not a failure of this
    function: it is the finding that nobody answered, and it is meant to be
    loud.
    """
    if page.hop >= MAX_ESCALATION_HOPS:
        raise EscalationExhaustedError(
            f"page {page.id} has escalated {page.hop} times, which is the ceiling; "
            "nobody in the chain answered"
        )

    seen = {page.recipient, *already_paged}

    if rotation is not None and page.recipient in rotation.user_ids:
        member = page.recipient
        for _hop in range(len(rotation.user_ids)):
            member = rotation.next_in_rotation(member)
            if member not in seen:
                return EscalationTarget(
                    user_id=member,
                    reason=(
                        f"next in the {rotation.name} rotation after "
                        "the page went unanswered"
                    ),
                    rotation_id=rotation.id,
                    policy_id=page.policy_id,
                    policy_step=page.policy_step,
                    timeout_s=page.ack_timeout_s,
                )

    if policy is not None:
        step_target = _next_policy_step(page, policy, rotation, seen)
        if step_target is not None:
            return step_target

    for candidate in candidates:
        if candidate and candidate not in seen:
            return EscalationTarget(
                user_id=candidate,
                reason=(
                    "no rotation or escalation policy covers this application, so "
                    "the next holder of a required role was paged"
                ),
                timeout_s=page.ack_timeout_s,
            )

    raise EscalationExhaustedError(
        f"every escalation target for page {page.id} has already been paged and "
        f"none acknowledged ({len(seen)} person(s) tried); this needs a human"
    )


def _next_policy_step(
    page: Page,
    policy: EscalationPolicy,
    rotation: Optional[OnCallRotation],
    seen: set[str],
) -> Optional[EscalationTarget]:
    for index in range(page.policy_step + 1, len(policy.steps)):
        step = policy.steps[index]
        if step.target_type is EscalationTargetType.USER:
            if step.target_id in seen:
                continue
            return EscalationTarget(
                user_id=step.target_id,
                reason=f"escalation policy {policy.name}, step {index + 1}",
                policy_id=policy.id,
                policy_step=index,
                timeout_s=step.timeout_s,
            )
        # A rotation step: whoever it puts first among people not yet paged.
        members = rotation.user_ids if rotation and rotation.id == step.target_id else ()
        for member in members:
            if member not in seen:
                return EscalationTarget(
                    user_id=member,
                    reason=(
                        f"escalation policy {policy.name}, step {index + 1} "
                        f"({step.target_id})"
                    ),
                    rotation_id=step.target_id,
                    policy_id=policy.id,
                    policy_step=index,
                    timeout_s=step.timeout_s,
                )
    return None


def first_timeout_s(
    policy: Optional[EscalationPolicy], *, default: int = DEFAULT_ACK_TIMEOUT_S
) -> int:
    """How long the first page has to be answered, from the policy's own step 1."""
    if policy is None or not policy.steps:
        return default
    return int(policy.steps[0].timeout_s)
