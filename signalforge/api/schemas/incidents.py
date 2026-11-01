"""Response shapes for the incident lifecycle (S4-T02).

Two constants are published as fields rather than left implicit, for the same
reason the detection schemas publish `requires_human` and `declared`: a client
must not be able to render one of these as the other.

- `declared_by_human` is always true on an incident. There is no code path that
  produces one any other way, and a UI that showed "declared by SignalForge"
  would be describing a product this is deliberately not.
- `source` on the lifecycle view says whether the state is a *stance* the
  detector recomputed seconds ago or a *decision* a person made. The same six
  words name both, and conflating them is exactly the mistake this sprint
  exists to prevent.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field

from signalforge.application.incidents.service import IncidentView, LifecycleView
from signalforge.domain.incidents.changes import (
    CHANGE_WINDOW_MAX_S,
    ChangeWindow,
    ChangeWindowView,
)
from signalforge.domain.incidents.entities import Incident, IncidentTransition


class DeclareIn(BaseModel):
    title: Optional[str] = Field(
        default=None,
        max_length=200,
        description="Left out, the incident is titled from the severity and the "
        "application. There is no automatic summarisation here.",
    )
    summary: str = Field(default="", max_length=4000)
    severity: Optional[str] = Field(
        default=None,
        description="P0, P1, P2 or P3. Omitted, the severity detection's matrix "
        "proposed is accepted - which is still a human act, because a person "
        "pressed Declare. Supplied, it is recorded as chosen rather than proposed.",
    )
    reason: str = Field(default="", max_length=1000)


class SeverityIn(BaseModel):
    severity: str
    reason: str = Field(default="", max_length=1000)


class StateIn(BaseModel):
    state: str = Field(description="active or recovering. Closing has its own route.")
    reason: str = Field(default="", max_length=1000)


class CloseIn(BaseModel):
    reason: str = Field(default="", max_length=1000)


class EvidenceRefOut(BaseModel):
    kind: str
    ref: str
    detail: str = ""
    family: Optional[str] = None
    region: str = ""
    customer_facing: bool = False
    at: Optional[datetime] = None


class ProposalOut(BaseModel):
    """What detection was saying when Declare was pressed - a snapshot, not a
    live read, so it still explains the incident after the rollups age out."""

    stance: str
    severity: Optional[str] = None
    rule: str = ""
    corroborated: bool = False
    families: list[str] = Field(default_factory=list)
    health_score: Optional[float] = None
    health_band: str = ""
    regions_affected: int = 0
    regions_total: int = 0
    success_rate_pct: Optional[float] = None
    journey_tier: Optional[int] = None
    customer_impacting: bool = False
    reasons: list[str] = Field(default_factory=list)
    evidence: list[EvidenceRefOut] = Field(default_factory=list)
    detail: str = ""
    unavailable: str = Field(
        default="",
        description="Why there is no proposal, when there is none. A person may "
        "always declare without one: the moment the detector is broken is the "
        "moment somebody most needs to.",
    )


class ChangeWindowLinkOut(BaseModel):
    change_id: str
    change_type: str
    service: str
    version: Optional[str] = None
    started_at: Optional[datetime] = None
    actor: Optional[str] = None


class TicketSyncOut(BaseModel):
    state: str = Field(
        description="not_configured, pending, synced, failed or uncertain. "
        "`uncertain` means a call timed out and the ticket may or may not exist; "
        "the retry reconciles before it creates."
    )
    ticket_id: Optional[str] = None
    reference: Optional[str] = None
    status: Optional[str] = None
    adapter_id: Optional[str] = None
    attempts: int = 0
    detail: str = ""
    last_attempt_at: Optional[datetime] = None
    synced_at: Optional[datetime] = None


class TransitionOut(BaseModel):
    kind: str = Field(
        default="state",
        description="`state` for a lifecycle move, `severity` for a re-grade "
        "(from_state equals to_state and the severity fields carry the change). "
        "The timeline renders the two differently and used to read a field "
        "that was never sent.",
    )
    from_state: str
    to_state: str
    at: datetime
    actor_type: str
    actor_id: str
    reason: str = ""
    severity_before: Optional[str] = None
    severity_after: Optional[str] = None


class IncidentOut(BaseModel):
    id: str
    reference: str
    app_id: str
    title: str
    summary: str = ""
    state: str
    severity: str
    severity_source: str
    stance_at_declare: str = ""
    declared_at: datetime
    declared_by: str
    declared_by_display: str = ""
    updated_at: datetime
    recovering_at: Optional[datetime] = None
    closed_at: Optional[datetime] = None
    closed_by: Optional[str] = None
    closure_reason: str = ""
    open: bool
    overrode_detection: bool = False
    declared_by_human: bool = Field(
        default=True,
        description="Always true. Only a person declares an incident (REQ-043).",
    )
    proposal: Optional[ProposalOut] = None
    change_windows: list[ChangeWindowLinkOut] = Field(default_factory=list)
    ticket: TicketSyncOut


class IncidentDetailOut(IncidentOut):
    transitions: list[TransitionOut] = Field(default_factory=list)


class IncidentListOut(BaseModel):
    count: int
    incidents: list[IncidentOut] = Field(default_factory=list)


class ChangeWindowItemOut(BaseModel):
    change_id: str
    change_type: str
    service: str
    status: str
    started_at: datetime
    completed_at: Optional[datetime] = None
    version: Optional[str] = None
    actor: Optional[str] = None
    diff_ref: Optional[str] = None
    source_system: str = ""
    simulated: bool = False
    announced_started: bool = False
    announced_completed: bool = False
    open: bool
    stale: bool
    age_s: float
    duration_s: Optional[float] = None
    detail: str = ""


class ChangeWindowOut(BaseModel):
    app_id: str
    at: datetime
    open: bool = Field(
        description="A deployment announced its start and never announced its "
        "completion. This is the chip."
    )
    max_age_s: float = Field(
        default=CHANGE_WINDOW_MAX_S,
        description="Past this, an unfinished change is stale rather than open: "
        "the pipeline stopped talking, and a chip that stays lit for days is a "
        "chip people stop seeing.",
    )
    windows: list[ChangeWindowItemOut] = Field(default_factory=list)
    stale: list[ChangeWindowItemOut] = Field(default_factory=list)
    recent: list[ChangeWindowItemOut] = Field(default_factory=list)
    detail: str = ""


class LifecycleOut(BaseModel):
    app_id: str
    state: str
    source: str = Field(
        description="`detection` - the state is a stance recomputed from "
        "measurements - or `incident`, a decision a person made."
    )
    at: datetime
    stance: str
    declared: bool
    declare_requires_human: bool = Field(
        default=True,
        description="Always true. Detection proposes; a person declares (REQ-043).",
    )
    severity: Optional[str] = None
    proposed_severity: Optional[str] = None
    corroborated: bool = False
    health_score: Optional[float] = None
    health_band: str = ""
    reasons: list[str] = Field(default_factory=list)
    detection_error: str = Field(
        default="",
        description="Why the detector could not be read, when it could not. "
        "A person can still declare.",
    )
    next_states: list[str] = Field(default_factory=list)
    human_moves: list[str] = Field(
        default_factory=list,
        description="Which of next_states only a person may make.",
    )
    incident: Optional[IncidentOut] = None
    change_window: ChangeWindowOut


# --- mappers ------------------------------------------------------------------


def incident_out(incident: Incident) -> IncidentOut:
    return IncidentOut(**_incident_fields(incident))


def incident_detail_out(view: IncidentView) -> IncidentDetailOut:
    return IncidentDetailOut(
        **_incident_fields(view.incident),
        transitions=[_transition_out(row) for row in view.transitions],
    )


def incident_list_out(incidents) -> IncidentListOut:
    rows = [incident_out(incident) for incident in incidents]
    return IncidentListOut(count=len(rows), incidents=rows)


def change_window_out(view: ChangeWindowView) -> ChangeWindowOut:
    return ChangeWindowOut(
        app_id=view.app_id,
        at=view.at,
        open=view.change_window_open,
        windows=[_window_out(window, view.at) for window in view.open],
        stale=[_window_out(window, view.at) for window in view.stale],
        recent=[_window_out(window, view.at) for window in view.recent],
        detail=view.detail,
    )


def lifecycle_out(view: LifecycleView) -> LifecycleOut:
    return LifecycleOut(
        app_id=view.app_id,
        state=view.state.value,
        source=view.source,
        at=view.at,
        stance=view.stance,
        declared=view.declared,
        severity=view.severity.value if view.severity else None,
        proposed_severity=(
            view.proposed_severity.value if view.proposed_severity else None
        ),
        corroborated=view.corroborated,
        health_score=view.health_score,
        health_band=view.health_band,
        reasons=list(view.reasons),
        detection_error=view.detection_error,
        next_states=list(view.next_states),
        human_moves=list(view.human_moves),
        incident=incident_out(view.incident) if view.incident else None,
        change_window=change_window_out(
            view.change_window
            if view.change_window is not None
            else ChangeWindowView(app_id=view.app_id, at=view.at)
        ),
    )


def _incident_fields(incident: Incident) -> dict:
    return {
        "id": incident.id,
        "reference": incident.reference,
        "app_id": incident.app_id,
        "title": incident.title,
        "summary": incident.summary,
        "state": incident.state.value,
        "severity": incident.severity.value,
        "severity_source": incident.severity_source.value,
        "stance_at_declare": incident.stance_at_declare,
        "declared_at": incident.declared_at,
        "declared_by": incident.declared_by,
        "declared_by_display": incident.declared_by_display,
        "updated_at": incident.updated_at,
        "recovering_at": incident.recovering_at,
        "closed_at": incident.closed_at,
        "closed_by": incident.closed_by,
        "closure_reason": incident.closure_reason,
        "open": incident.open,
        "overrode_detection": incident.overrode_detection,
        "proposal": _proposal_out(incident),
        "change_windows": [
            ChangeWindowLinkOut(
                change_id=link.change_id,
                change_type=link.change_type,
                service=link.service,
                version=link.version,
                started_at=link.started_at,
                actor=link.actor,
            )
            for link in incident.change_windows
        ],
        "ticket": TicketSyncOut(
            state=incident.ticket.state.value,
            ticket_id=incident.ticket.ticket_id,
            reference=incident.ticket.reference,
            status=incident.ticket.status.value if incident.ticket.status else None,
            adapter_id=incident.ticket.adapter_id,
            attempts=incident.ticket.attempts,
            detail=incident.ticket.detail,
            last_attempt_at=incident.ticket.last_attempt_at,
            synced_at=incident.ticket.synced_at,
        ),
    }


def _proposal_out(incident: Incident) -> Optional[ProposalOut]:
    proposal = incident.proposal
    if proposal is None:
        return None
    return ProposalOut(
        stance=proposal.stance,
        severity=proposal.severity.value if proposal.severity else None,
        rule=proposal.rule,
        corroborated=proposal.corroborated,
        families=list(proposal.families),
        health_score=proposal.health_score,
        health_band=proposal.health_band,
        regions_affected=proposal.regions_affected,
        regions_total=proposal.regions_total,
        success_rate_pct=proposal.success_rate_pct,
        journey_tier=proposal.journey_tier,
        customer_impacting=proposal.customer_impacting,
        reasons=list(proposal.reasons),
        evidence=[
            EvidenceRefOut(
                kind=ref.kind,
                ref=ref.ref,
                detail=ref.detail,
                family=ref.family,
                region=ref.region,
                customer_facing=ref.customer_facing,
                at=ref.at,
            )
            for ref in proposal.evidence
        ],
        detail=proposal.detail,
        unavailable=proposal.unavailable,
    )


def _transition_out(transition: IncidentTransition) -> TransitionOut:
    regrade = (
        transition.from_state is transition.to_state
        and transition.severity_after is not None
    )
    return TransitionOut(
        kind="severity" if regrade else "state",
        from_state=transition.from_state.value,
        to_state=transition.to_state.value,
        at=transition.at,
        actor_type=transition.actor_type.value,
        actor_id=transition.actor_id,
        reason=transition.reason,
        severity_before=(
            transition.severity_before.value if transition.severity_before else None
        ),
        severity_after=(
            transition.severity_after.value if transition.severity_after else None
        ),
    )


def _window_out(window: ChangeWindow, at: datetime) -> ChangeWindowItemOut:
    return ChangeWindowItemOut(
        change_id=window.change_id,
        change_type=window.change_type.value,
        service=window.service,
        status=window.status.value,
        started_at=window.started_at,
        completed_at=window.completed_at,
        version=window.version,
        actor=window.actor,
        diff_ref=window.diff_ref,
        source_system=window.source_system,
        simulated=window.simulated,
        announced_started=window.announced_started,
        announced_completed=window.announced_completed,
        open=window.open,
        stale=window.stale(at),
        age_s=window.age_s(at),
        duration_s=window.duration_s(),
        detail=window.detail(at),
    )
