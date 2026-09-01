"""The incident itself, its transition history and its ticket link (REQ-043).

An `Incident` is the record of a decision a person made, so every field that
records a decision names the person and the moment. Nothing here mutates: each
move returns a new value, and the move is checked by the state machine in
`lifecycle.py` before the new value exists - a service cannot construct an
incident in a state the lifecycle would have refused.

`DeclaredProposal` is what detection was saying at the instant Declare was
pressed, copied onto the incident rather than referenced. It is deliberately a
snapshot: the rollups it was drawn from age out in hours, the stance changes
five seconds later, and an incident that could only explain itself by re-reading
live telemetry would explain itself differently every time it was opened.

`TicketSync` is the state of the link to an ITSM system and it has five values
because four would be a lie. `uncertain` is the one that matters: a create that
timed out may or may not have made a ticket, and the difference between "it
failed" and "we do not know" is the difference between retrying safely and
raising a second incident ticket at 3am.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import Enum
from typing import Any, Mapping, Optional

from signalforge.contracts.canonical import TicketStatus
from signalforge.contracts.envelope import ActorType
from signalforge.domain.incidents.errors import IncidentValidationError
from signalforge.domain.incidents.lifecycle import (
    LifecycleState,
    assert_severity_change,
    assert_transition,
)
from signalforge.domain.onboarding.profile import Severity

MAX_TITLE_CHARS = 200
MAX_SUMMARY_CHARS = 4_000
MAX_REASON_CHARS = 1_000


class SeveritySource(str, Enum):
    """Where the severity on an incident came from.

    Both are human acts - somebody pressed Declare either way - but they are not
    the same act, and a review six months later wants to know whether the person
    accepted what the matrix computed or overrode it.
    """

    PROPOSED = "proposed"
    CHOSEN = "chosen"


class TicketSyncState(str, Enum):
    NOT_CONFIGURED = "not_configured"
    PENDING = "pending"
    SYNCED = "synced"
    FAILED = "failed"
    UNCERTAIN = "uncertain"

    @property
    def needs_retry(self) -> bool:
        return self in (
            TicketSyncState.PENDING,
            TicketSyncState.FAILED,
            TicketSyncState.UNCERTAIN,
        )


@dataclass(frozen=True)
class TicketSync:
    state: TicketSyncState = TicketSyncState.PENDING
    ticket_id: Optional[str] = None
    reference: Optional[str] = None
    status: Optional[TicketStatus] = None
    adapter_id: Optional[str] = None
    binding_id: Optional[str] = None
    attempts: int = 0
    detail: str = ""
    last_attempt_at: Optional[datetime] = None
    synced_at: Optional[datetime] = None

    @property
    def exists(self) -> bool:
        """A ticket this platform believes it created. `uncertain` deliberately
        does not qualify - that is the whole reason for reconcile-before-retry."""
        return self.state is TicketSyncState.SYNCED and bool(self.reference)


@dataclass(frozen=True)
class EvidenceLink:
    """One evidence ref carried over from the proposal, kept verbatim."""

    kind: str
    ref: str
    detail: str = ""
    family: Optional[str] = None
    region: str = ""
    customer_facing: bool = False
    at: Optional[datetime] = None


@dataclass(frozen=True)
class DeclaredProposal:
    """What the detector believed when the incident was declared."""

    stance: str
    severity: Optional[Severity] = None
    rule: str = ""
    corroborated: bool = False
    families: tuple[str, ...] = ()
    health_score: Optional[float] = None
    health_band: str = ""
    regions_affected: int = 0
    regions_total: int = 0
    success_rate_pct: Optional[float] = None
    journey_tier: Optional[int] = None
    customer_impacting: bool = False
    reasons: tuple[str, ...] = ()
    evidence: tuple[EvidenceLink, ...] = ()
    detail: str = ""
    unavailable: str = ""
    """Why there is no proposal, when there is none. An empty string means the
    detector answered; anything else is the reason it could not, and a person is
    still allowed to declare - the moment the detector is broken is exactly the
    moment somebody most needs to."""


@dataclass(frozen=True)
class ChangeWindowLink:
    """A change that was still open when the incident was declared."""

    change_id: str
    change_type: str
    service: str
    version: Optional[str] = None
    started_at: Optional[datetime] = None
    actor: Optional[str] = None


@dataclass(frozen=True)
class Incident:
    id: str
    tenant_id: str
    app_id: str
    reference: str
    ordinal: int
    title: str
    severity: Severity
    state: LifecycleState
    declared_at: datetime
    declared_by: str
    updated_at: datetime
    summary: str = ""
    severity_source: SeveritySource = SeveritySource.PROPOSED
    declared_by_display: str = ""
    stance_at_declare: str = ""
    recovering_at: Optional[datetime] = None
    closed_at: Optional[datetime] = None
    closed_by: Optional[str] = None
    closure_reason: str = ""
    proposal: Optional[DeclaredProposal] = None
    change_windows: tuple[ChangeWindowLink, ...] = ()
    ticket: TicketSync = field(default_factory=TicketSync)

    def __post_init__(self) -> None:
        title = str(self.title or "").strip()
        if not title:
            raise IncidentValidationError("an incident needs a title")
        object.__setattr__(self, "title", title[:MAX_TITLE_CHARS])
        object.__setattr__(self, "summary", str(self.summary or "")[:MAX_SUMMARY_CHARS])
        if not self.app_id:
            raise IncidentValidationError("an incident must name the application it is about")
        if not self.declared_by:
            raise IncidentValidationError(
                "an incident must name the person who declared it; there is no "
                "path by which one is declared by nobody"
            )
        if not self.state.declared:
            raise IncidentValidationError(
                f"{self.state.value} is a detection stance, not an incident state; "
                "an Incident row only exists once a person declared one"
            )

    # -- moves --------------------------------------------------------------

    def moved_to(
        self,
        target: LifecycleState,
        *,
        at: datetime,
        by: ActorType,
        actor_id: str,
        reason: str = "",
    ) -> "Incident":
        assert_transition(
            self.state, target, by=by, what=f"incident {self.reference}"
        )
        changes: dict[str, Any] = {"state": target, "updated_at": at}
        if target is LifecycleState.RECOVERING:
            changes["recovering_at"] = at
        if target is LifecycleState.CLOSED:
            changes["closed_at"] = at
            changes["closed_by"] = actor_id
            changes["closure_reason"] = str(reason or "")[:MAX_REASON_CHARS]
        if target is LifecycleState.ACTIVE:
            # A regression during validation. The recovery is no longer true, so
            # the timestamp that claimed it is cleared rather than left to make
            # the incident look like it recovered and stayed that way.
            changes["recovering_at"] = None
        return replace(self, **changes)

    def with_severity(
        self, severity: Severity, *, at: datetime, by: ActorType
    ) -> "Incident":
        assert_severity_change(self.state, by=by)
        if severity is self.severity:
            return self
        return replace(
            self,
            severity=severity,
            severity_source=SeveritySource.CHOSEN,
            updated_at=at,
        )

    def with_ticket(self, ticket: TicketSync, *, at: datetime) -> "Incident":
        return replace(self, ticket=ticket, updated_at=at)

    # -- reads ---------------------------------------------------------------

    @property
    def open(self) -> bool:
        return self.state.open

    @property
    def overrode_detection(self) -> bool:
        """The person did not take what the matrix proposed."""
        proposed = self.proposal.severity if self.proposal else None
        return self.severity_source is SeveritySource.CHOSEN or (
            proposed is not None and proposed is not self.severity
        )

    def age_s(self, now: datetime) -> float:
        end = self.closed_at or now
        return max((end - self.declared_at).total_seconds(), 0.0)


@dataclass(frozen=True)
class IncidentTransition:
    """One move, kept forever.

    Append-only and never summarised into the incident row: "who moved this to
    recovering, when, and what did they say" is the question a post-incident
    review asks first, and a current-state column cannot answer it.
    """

    tenant_id: str
    incident_id: str
    app_id: str
    from_state: LifecycleState
    to_state: LifecycleState
    at: datetime
    actor_type: ActorType
    actor_id: str
    reason: str = ""
    severity_before: Optional[Severity] = None
    severity_after: Optional[Severity] = None
    detail: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "reason", str(self.reason or "")[:MAX_REASON_CHARS])
