"""How a lifecycle state is expressed as a ticket (REQ-043).

Pure mapping, no adapter and no I/O, so the rule can be read and tested without
a ticket queue: an incident that is being run is a ticket **in progress**, and an
incident a person closed is a ticket **closed**.

    active      -> in_progress
    recovering  -> in_progress
    closed      -> closed

`recovering` is deliberately *not* `resolved`. Recovering means a mitigation was
applied and is being validated; resolved is a claim about the outcome, and the
platform has no business making that claim on a person's behalf while the
validation it is describing has not finished. It also removes the only backwards
move the lifecycle could produce - a regression from recovering to active leaves
the ticket exactly where it is.

Because ITSM systems refuse skipped states (SignalForge's own itsm-lite will not
take `in_progress -> closed`), `ticket_path` walks the canonical order one step
at a time. Forward is a sequence; backwards is a single move, offered as-is, and
an ITSM that refuses it is a failed sync with the vendor's own reason rather
than something this module pretends it can work around.
"""

from __future__ import annotations

from typing import Mapping, Optional

from signalforge.contracts.canonical import TicketStatus
from signalforge.domain.incidents.entities import Incident
from signalforge.domain.incidents.lifecycle import LifecycleState

TICKET_ORDER: tuple[TicketStatus, ...] = (
    TicketStatus.OPEN,
    TicketStatus.IN_PROGRESS,
    TicketStatus.RESOLVED,
    TicketStatus.CLOSED,
)

LIFECYCLE_TICKET_STATUS: Mapping[LifecycleState, TicketStatus] = {
    LifecycleState.ACTIVE: TicketStatus.IN_PROGRESS,
    LifecycleState.RECOVERING: TicketStatus.IN_PROGRESS,
    LifecycleState.CLOSED: TicketStatus.CLOSED,
}


def ticket_status_for(state: LifecycleState) -> TicketStatus:
    """A ticket only exists for a declared incident, so a stance has no mapping
    and asking for one is a programming error rather than a runtime state."""
    try:
        return LIFECYCLE_TICKET_STATUS[state]
    except KeyError:  # pragma: no cover - guarded by Incident.__post_init__
        raise ValueError(f"{state.value} is a stance and has no ticket") from None


def ticket_path(
    current: Optional[TicketStatus], target: TicketStatus
) -> tuple[TicketStatus, ...]:
    """Every status update needed to get a ticket from where it is to where the
    incident says it should be, in order.

    An unknown current status (no ticket yet) is one step: create it at the
    target. Moving forward walks the intermediate states, because `in_progress`
    to `closed` skips the resolution an ITSM expects to record. Moving backwards
    is offered directly - it is a reopening, and a system that allows one allows
    it in a single call.
    """
    if current is None or current is target:
        return (target,) if current is None else ()
    here = TICKET_ORDER.index(current)
    there = TICKET_ORDER.index(target)
    if there > here:
        return tuple(TICKET_ORDER[here + 1: there + 1])
    return (target,)


def ticket_fields(incident: Incident) -> dict[str, object]:
    """What an update carries beside the status.

    Only fields the canonical `Ticket` model defines, so any ITSM adapter can
    take them. The description is the incident's own summary; adapters that
    declare a redaction policy apply it at write time (S3-T02).
    """
    return {
        "title": incident.title,
        "severity": incident.severity.value,
        "description": incident.summary or incident.title,
    }


def ticket_title(incident: Incident) -> str:
    return f"[{incident.severity.value}] {incident.reference} {incident.title}"[:200]
