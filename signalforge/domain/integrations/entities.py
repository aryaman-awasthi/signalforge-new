"""Entities the shipped adapters keep (REQ-031, REQ-033, REQ-035).

Every one of these is durable on purpose. A simulated pager whose pages vanish
on restart cannot be used to rehearse an escalation, and a knowledge index that
forgets is not an index. "Simulated" means the *integrated system* is faked, not
that the record of what SignalForge did is.

The state machines are here rather than in the adapters because two adapters
write deliveries (in-app and browser push are different transports for the same
promise) and because the illegal transitions are the interesting ones: a failed
delivery must not become acknowledged, and a ticket that a human closed must not
be reopened by a retrying machine.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any, Mapping, Optional

from signalforge.contracts.canonical import (
    DeliveryStatus,
    KnowledgeDocType,
    NotificationChannel,
    TicketStatus,
)
from signalforge.contracts.envelope import ActorType
from signalforge.domain.shared.errors import DomainValidationError

# --- itsm ---------------------------------------------------------------------

TICKET_TRANSITIONS: Mapping[TicketStatus, frozenset[TicketStatus]] = {
    TicketStatus.OPEN: frozenset(
        {TicketStatus.OPEN, TicketStatus.IN_PROGRESS, TicketStatus.RESOLVED}
    ),
    TicketStatus.IN_PROGRESS: frozenset(
        {TicketStatus.IN_PROGRESS, TicketStatus.RESOLVED, TicketStatus.OPEN}
    ),
    # A resolved ticket can be reopened - incidents regress - but a closed one
    # cannot: closure is the record that a human signed off, and a machine
    # retrying an update must not undo it.
    TicketStatus.RESOLVED: frozenset(
        {TicketStatus.RESOLVED, TicketStatus.CLOSED, TicketStatus.OPEN}
    ),
    TicketStatus.CLOSED: frozenset({TicketStatus.CLOSED}),
}

TICKET_MUTABLE_FIELDS = frozenset(
    {"title", "status", "description", "severity", "assignee", "links"}
)


@dataclass(frozen=True)
class StoredTicket:
    tenant_id: str
    app_id: str
    ticket_id: str
    reference: str
    title: str
    status: TicketStatus
    created_at: datetime
    updated_at: datetime
    description: Optional[str] = None
    severity: Optional[str] = None
    assignee: Optional[str] = None
    links: tuple[str, ...] = ()

    def with_fields(
        self, fields: Mapping[str, Any], *, at: datetime
    ) -> "StoredTicket":
        unknown = sorted(set(fields) - TICKET_MUTABLE_FIELDS)
        if unknown:
            raise DomainValidationError(
                f"ticket fields {unknown} cannot be updated; updatable fields are "
                f"{sorted(TICKET_MUTABLE_FIELDS)}"
            )
        changes: dict[str, Any] = {}
        for name, value in fields.items():
            if name == "status":
                changes["status"] = self._checked_status(value)
            elif name == "links":
                changes["links"] = tuple(str(link) for link in value or ())
            elif name == "title":
                title = str(value).strip()
                if not title:
                    raise DomainValidationError("ticket title must not be empty")
                changes["title"] = title
            else:
                changes[name] = None if value is None else str(value)
        return replace(self, updated_at=at, **changes)

    def _checked_status(self, value: Any) -> TicketStatus:
        try:
            target = TicketStatus(value)
        except ValueError as exc:
            raise DomainValidationError(
                f"{value!r} is not a ticket status; "
                f"one of {[s.value for s in TicketStatus]}"
            ) from exc
        if target not in TICKET_TRANSITIONS[self.status]:
            raise DomainValidationError(
                f"ticket {self.ticket_id} is {self.status.value} and cannot become "
                f"{target.value}"
            )
        return target


# --- collaboration ------------------------------------------------------------


@dataclass(frozen=True)
class StoredMessage:
    """One message in a simulated collaboration channel.

    `scripted` separates what a drill script produced from what a human typed
    into the bridge, so a replay can tell the rehearsal apart from the people.
    """

    tenant_id: str
    app_id: Optional[str]
    channel: str
    message_id: str
    author_type: ActorType
    author_id: str
    text: str
    sent_at: datetime
    author_display: Optional[str] = None
    thread_id: Optional[str] = None
    scripted: bool = False


@dataclass(frozen=True)
class ScriptedMessage:
    """A line of a collaboration script, offset from the channel's first use."""

    after_s: float
    author_id: str
    text: str
    author_type: ActorType = ActorType.HUMAN
    author_display: Optional[str] = None
    thread_id: Optional[str] = None

    def __post_init__(self) -> None:
        if self.after_s < 0:
            raise DomainValidationError("script entries cannot be scheduled in the past")
        if not str(self.text).strip():
            raise DomainValidationError("a script entry must carry text")
        if not str(self.author_id).strip():
            raise DomainValidationError("a script entry must name an author")


# --- notifications ------------------------------------------------------------

DELIVERY_TRANSITIONS: Mapping[DeliveryStatus, frozenset[DeliveryStatus]] = {
    DeliveryStatus.QUEUED: frozenset(
        {
            DeliveryStatus.QUEUED,
            DeliveryStatus.SENT,
            DeliveryStatus.DELIVERED,
            DeliveryStatus.FAILED,
            DeliveryStatus.ACKED,
        }
    ),
    DeliveryStatus.SENT: frozenset(
        {DeliveryStatus.SENT, DeliveryStatus.DELIVERED, DeliveryStatus.FAILED,
         DeliveryStatus.ACKED}
    ),
    DeliveryStatus.DELIVERED: frozenset(
        {DeliveryStatus.DELIVERED, DeliveryStatus.ACKED}
    ),
    # Terminal. A failed page that later reports "acknowledged" would let an
    # escalation be silenced by a delivery that never happened.
    DeliveryStatus.FAILED: frozenset({DeliveryStatus.FAILED}),
    DeliveryStatus.ACKED: frozenset({DeliveryStatus.ACKED}),
}


@dataclass(frozen=True)
class NotificationDelivery:
    tenant_id: str
    notification_id: str
    adapter_id: str
    channel: NotificationChannel
    recipient: str
    title: str
    body: str
    status: DeliveryStatus
    at: datetime
    app_id: Optional[str] = None
    incident_id: Optional[str] = None
    requires_ack: bool = False
    simulated: bool = False
    detail: Optional[str] = None
    acked_by: Optional[str] = None
    acked_at: Optional[datetime] = None

    def moved_to(
        self, status: DeliveryStatus, *, at: datetime, detail: Optional[str] = None
    ) -> "NotificationDelivery":
        if status not in DELIVERY_TRANSITIONS[self.status]:
            raise DomainValidationError(
                f"notification {self.notification_id} is {self.status.value} and "
                f"cannot become {status.value}"
            )
        return replace(self, status=status, at=at, detail=detail or self.detail)

    def acknowledged(self, *, at: datetime, by: str) -> "NotificationDelivery":
        """The human override: a person says "I have this".

        Idempotent, because an operator double-clicking an acknowledge button
        and a socket redelivering the same frame must not be different events.
        """
        if not self.requires_ack:
            raise DomainValidationError(
                f"notification {self.notification_id} does not ask for acknowledgement"
            )
        if self.status is DeliveryStatus.ACKED:
            return self
        return replace(
            self.moved_to(DeliveryStatus.ACKED, at=at),
            acked_by=by,
            acked_at=at,
        )

    @property
    def open(self) -> bool:
        """Still wants a human. What a reconnecting cockpit is caught up with."""
        return self.requires_ack and self.status not in (
            DeliveryStatus.ACKED,
            DeliveryStatus.FAILED,
        )


def delivery_frame(delivery: NotificationDelivery, *, replay: bool = False) -> dict:
    """The shape a cockpit receives, built in one place.

    Two components emit it - the channel adapter pushing a live notification and
    the socket replaying what is still open - and a cockpit that had to tell two
    shapes apart would be one bug away from rendering neither. `replay` is the
    only thing that differs, and it is what lets a client distinguish "this just
    happened" from "this was waiting for you".
    """
    return {
        "type": "notification",
        "replay": replay,
        "notification_id": delivery.notification_id,
        "adapter_id": delivery.adapter_id,
        "channel": delivery.channel.value,
        "title": delivery.title,
        "body": delivery.body,
        "incident_id": delivery.incident_id,
        "requires_ack": delivery.requires_ack,
        "simulated": delivery.simulated,
        "status": delivery.status.value,
        "at": delivery.at.isoformat(),
    }


# --- knowledge ----------------------------------------------------------------


@dataclass(frozen=True)
class IndexedDocument:
    tenant_id: str
    doc_id: str
    title: str
    body: str
    doc_type: KnowledgeDocType
    updated_at: datetime
    indexed_at: datetime
    tokens: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
    app_id: Optional[str] = None
    embedding: tuple[float, ...] = ()
    embedding_model: Optional[str] = None
    redaction_policy_id: Optional[str] = None

    @property
    def embedded(self) -> bool:
        return bool(self.embedding)
