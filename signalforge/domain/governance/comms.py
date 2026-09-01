"""Governed communications (REQ-064).

The rule this module exists to make structural: **nothing sends itself.** There
is no `send()` on this entity, no auto-issue transition, no timer that promotes a
draft, and no configuration that could add one. A communication reaches an
audience because a named person holding the audience's approval permission moved
it to ISSUED, and the record says who and when.

## The lifecycle

    DRAFT ──approve──▶ APPROVED ──issue──▶ ISSUED
      │                                      │
      │                                      ▼
      └──supersede──▶ (new draft)      CONTRADICTED ──correct──▶ (correction DRAFT)

`CONTRADICTED` is the interesting one and it is entered *automatically*, which
looks like it breaks the rule above but is its other half: the platform may
never send, and it may always warn. When a graph node a communication was
grounded on is superseded or retracted, every issued communication citing it is
flagged - because "we told customers the payment failures were confined to
eu-west-1" stops being true the moment the topology says otherwise, and a status
page nobody flagged is worse than one nobody wrote.

The flag never edits the issued text. Issued words are a matter of record; they
are corrected by a *new* communication carrying a redline, approved by a person,
exactly like the original. Silently rewriting what an organisation told its
customers is not a correction, it is a forgery.

## Grounding

Every draft cites graph node ids, and `assert_grounded` refuses one that cites
nothing or cites a node outside this incident. A communication is the most
consequential text this platform produces; a sentence in it that traces back to
nothing is exactly the sentence that will turn out to be wrong.
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import Enum
from typing import Any, Mapping, Optional, Sequence

from signalforge.contracts.roles import Permission
from signalforge.domain.governance.errors import (
    CommunicationStateError,
    GovernanceValidationError,
    UngroundedCommunicationError,
)

MAX_SUBJECT_CHARS = 200
MAX_BODY_CHARS = 4_000
MAX_REFS = 40


class Audience(str, Enum):
    """Who this is for. The audience decides who may approve it."""

    RESPONDERS = "responders"
    LEADERSHIP = "leadership"
    CUSTOMER_SUPPORT = "customer_support"
    CUSTOMER = "customer"
    """Externally visible - a status page, a customer email. The highest bar in
    the platform: `approve_customer_comms`, held only by the Comms Lead."""


AUDIENCE_PERMISSION: Mapping[Audience, Optional[Permission]] = {
    Audience.RESPONDERS: None,
    Audience.LEADERSHIP: Permission.APPROVE_EXEC_COMMS,
    Audience.CUSTOMER_SUPPORT: Permission.APPROVE_CUSTOMER_COMMS,
    Audience.CUSTOMER: Permission.APPROVE_CUSTOMER_COMMS,
}
"""Which permission from the frozen matrix approves each audience.

`RESPONDERS` needs none, and that is deliberate rather than an oversight: a note
to the people already in the war room is not an external statement, and a
platform that made an SME wait for a Comms Lead to tell their own responders
something would be routed around within a day. It is still drafted, grounded,
approved by a named person and audited - it just does not need a permission
nobody in the room holds."""


class CommunicationState(str, Enum):
    DRAFT = "draft"
    APPROVED = "approved"
    ISSUED = "issued"
    CONTRADICTED = "contradicted"
    CORRECTED = "corrected"
    """Superseded by a correction that has itself been issued."""
    WITHDRAWN = "withdrawn"

    @property
    def public(self) -> bool:
        """It has left the building. Nothing may edit it after this."""
        return self in (
            CommunicationState.ISSUED,
            CommunicationState.CONTRADICTED,
            CommunicationState.CORRECTED,
        )


TRANSITIONS: Mapping[CommunicationState, frozenset[CommunicationState]] = {
    CommunicationState.DRAFT: frozenset(
        {CommunicationState.APPROVED, CommunicationState.WITHDRAWN}
    ),
    CommunicationState.APPROVED: frozenset(
        {CommunicationState.ISSUED, CommunicationState.DRAFT, CommunicationState.WITHDRAWN}
    ),
    CommunicationState.ISSUED: frozenset(
        {CommunicationState.CONTRADICTED, CommunicationState.CORRECTED}
    ),
    CommunicationState.CONTRADICTED: frozenset({CommunicationState.CORRECTED}),
    CommunicationState.CORRECTED: frozenset(),
    CommunicationState.WITHDRAWN: frozenset(),
}
"""`APPROVED -> DRAFT` exists so that an approval can be withdrawn by editing;
`ISSUED -> anything but contradicted/corrected` does not exist at all."""


@dataclass(frozen=True)
class RedlineChange:
    """One claim that changed, old beside new. What a correction must carry."""

    old: str
    new: str
    reason: str = ""
    evidence_refs: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "old": self.old,
            "new": self.new,
            "reason": self.reason,
            "evidence_refs": list(self.evidence_refs),
        }


def redline(old_body: str, new_body: str) -> tuple[RedlineChange, ...]:
    """A line-level diff of two drafts, as claims.

    Line-level rather than word-level on purpose: a communication is read as
    statements, and "the outage is confined to eu-west-1" becoming "the outage
    affects eu-west-1 and eu-central-1" is one changed claim, not four changed
    words. A reviewer approving a correction is approving claims.
    """
    old_lines = [line for line in (old_body or "").splitlines() if line.strip()]
    new_lines = [line for line in (new_body or "").splitlines() if line.strip()]
    changes: list[RedlineChange] = []
    matcher = difflib.SequenceMatcher(a=old_lines, b=new_lines, autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        removed = " ".join(old_lines[i1:i2]).strip()
        added = " ".join(new_lines[j1:j2]).strip()
        changes.append(
            RedlineChange(
                old=removed or "(nothing said)",
                new=added or "(claim withdrawn)",
                reason={
                    "replace": "claim corrected",
                    "delete": "claim withdrawn",
                    "insert": "claim added",
                }[tag],
            )
        )
    return tuple(changes)


@dataclass(frozen=True)
class Communication:
    """One piece of text for one audience, with its whole history attached."""

    id: str
    tenant_id: str
    incident_id: str
    app_id: str
    audience: Audience
    state: CommunicationState
    subject: str
    body: str
    created_at: datetime
    created_by: str
    version: int = 1
    grounded_node_ids: tuple[str, ...] = ()
    """Graph node ids this text is drawn from. Not decoration: the contradiction
    sweep reads exactly this list."""
    evidence_refs: tuple[str, ...] = ()
    model: str = ""
    model_class: str = ""
    degraded: bool = True
    """True when the deterministic writer produced it rather than a model. The
    provenance chip on the card reads this."""
    approved_by: str = ""
    approved_by_label: str = ""
    approved_permission: Optional[Permission] = None
    approved_at: Optional[datetime] = None
    issued_by: str = ""
    issued_at: Optional[datetime] = None
    contradicted_at: Optional[datetime] = None
    contradiction_reason: str = ""
    contradicting_node_ids: tuple[str, ...] = ()
    corrects_id: Optional[str] = None
    corrected_by_id: Optional[str] = None
    redline: tuple[RedlineChange, ...] = ()
    withdrawn_at: Optional[datetime] = None
    note: str = ""
    detail: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.tenant_id or not self.incident_id:
            raise GovernanceValidationError(
                "a communication belongs to one incident in one tenant"
            )
        subject = str(self.subject or "").strip()
        if not subject:
            raise GovernanceValidationError("a communication needs a subject")
        object.__setattr__(self, "subject", subject[:MAX_SUBJECT_CHARS])
        body = str(self.body or "").strip()
        if not body:
            raise GovernanceValidationError("a communication needs a body")
        object.__setattr__(self, "body", body[:MAX_BODY_CHARS])
        object.__setattr__(
            self,
            "grounded_node_ids",
            tuple(dict.fromkeys(self.grounded_node_ids))[:MAX_REFS],
        )

    # -- reads ---------------------------------------------------------------

    @property
    def required_permission(self) -> Optional[Permission]:
        return AUDIENCE_PERMISSION[self.audience]

    @property
    def external(self) -> bool:
        return self.audience in (Audience.CUSTOMER, Audience.CUSTOMER_SUPPORT)

    @property
    def issued(self) -> bool:
        return self.state.public

    def assert_grounded(self, available_node_ids: Sequence[str]) -> None:
        """Every citation resolves to a node of *this* incident, and there is at
        least one. Raised at draft time, so an ungrounded draft never exists."""
        if not self.grounded_node_ids:
            raise UngroundedCommunicationError(
                "a communication must cite the incident-brain nodes it is drawn "
                "from; a claim that traces back to nothing is the claim that "
                "turns out to be wrong"
            )
        known = set(available_node_ids)
        unknown = [ref for ref in self.grounded_node_ids if ref not in known]
        if unknown:
            raise UngroundedCommunicationError(
                f"these citations are not nodes of this incident: {unknown[:5]}"
            )

    # -- moves ---------------------------------------------------------------

    def _moved(self, target: CommunicationState, **changes: Any) -> "Communication":
        if target not in TRANSITIONS[self.state]:
            raise CommunicationStateError(
                f"a {self.state.value} communication cannot become {target.value}; "
                f"legal moves are {sorted(s.value for s in TRANSITIONS[self.state])}"
            )
        return replace(self, state=target, **changes)

    def approved(
        self,
        *,
        at: datetime,
        by: str,
        by_label: str,
        permission: Optional[Permission],
    ) -> "Communication":
        if by == self.created_by and self.external:
            # An externally visible statement is approved by somebody other than
            # its author. Internal notes are not held to this: the responder who
            # wrote it is the responder who meant it.
            raise CommunicationStateError(
                "an externally visible communication is approved by somebody "
                "other than its author"
            )
        return self._moved(
            CommunicationState.APPROVED,
            approved_by=by,
            approved_by_label=by_label,
            approved_permission=permission,
            approved_at=at,
        )

    def issued_now(self, *, at: datetime, by: str) -> "Communication":
        """A person pressed Issue. There is no other caller of this method, and
        no scheduler that could become one."""
        return self._moved(CommunicationState.ISSUED, issued_by=by, issued_at=at)

    def contradicted(
        self, *, at: datetime, reason: str, node_ids: Sequence[str]
    ) -> "Communication":
        """The evidence moved under an issued statement.

        The text is untouched. Only the flag, the reason and which nodes went is
        recorded - and the state change is what puts the warning on the card.
        """
        return self._moved(
            CommunicationState.CONTRADICTED,
            contradicted_at=at,
            contradiction_reason=str(reason or "")[:MAX_SUBJECT_CHARS],
            contradicting_node_ids=tuple(dict.fromkeys(node_ids))[:MAX_REFS],
        )

    def corrected_by(self, correction_id: str) -> "Communication":
        return self._moved(
            CommunicationState.CORRECTED, corrected_by_id=correction_id
        )

    def withdrawn(self, *, at: datetime, by: str) -> "Communication":
        return self._moved(
            CommunicationState.WITHDRAWN,
            withdrawn_at=at,
            detail={**dict(self.detail), "withdrawn_by": by},
        )

    def correction_draft(
        self,
        *,
        communication_id: str,
        at: datetime,
        by: str,
        subject: str,
        body: str,
        grounded_node_ids: Sequence[str],
        model: str = "",
        model_class: str = "",
        degraded: bool = True,
        reason: str = "",
    ) -> "Communication":
        """A new DRAFT that carries the redline against this issued text.

        A correction is a full communication in its own right - it needs its own
        grounding and its own approval - and it names what it corrects, so the
        card can show "we said X; we now say Y, because Z".
        """
        if not self.state.public:
            raise CommunicationStateError(
                "only an issued communication needs a correction; edit the draft "
                "instead"
            )
        changes = redline(self.body, body)
        if reason:
            changes = tuple(replace(c, reason=reason) for c in changes)
        return Communication(
            id=communication_id,
            tenant_id=self.tenant_id,
            incident_id=self.incident_id,
            app_id=self.app_id,
            audience=self.audience,
            state=CommunicationState.DRAFT,
            subject=subject,
            body=body,
            created_at=at,
            created_by=by,
            version=self.version + 1,
            grounded_node_ids=tuple(grounded_node_ids),
            model=model,
            model_class=model_class,
            degraded=degraded,
            corrects_id=self.id,
            redline=changes,
            note=reason,
        )

    def as_payload(self) -> dict[str, Any]:
        return {
            "communication_id": self.id,
            "incident_id": self.incident_id,
            "audience": self.audience.value,
            "state": self.state.value,
            "subject": self.subject,
            "version": self.version,
            "grounded_node_ids": list(self.grounded_node_ids),
            "approved_by": self.approved_by,
            "issued_by": self.issued_by,
            "issued_at": self.issued_at.isoformat() if self.issued_at else None,
            "corrects_id": self.corrects_id,
            "degraded": self.degraded,
            "model": self.model,
        }
