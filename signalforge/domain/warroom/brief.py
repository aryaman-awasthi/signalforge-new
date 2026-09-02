"""The join brief: what somebody walking in at minute forty needs (REQ-045).

Template now, model in S5 (REQ-051). That ordering is on purpose rather than a
shortcut. A brief is the first thing a paged responder reads, on a phone, while
still deciding whether to get out of bed - so the version that exists when the
model is down, slow, or not yet built has to be genuinely useful, not a
placeholder that says "summary unavailable".

Everything here is assembled from facts the incident already holds: the
declaration, the severity and where it came from, the evidence detection
attached, what has been done (the transition history and the task board), what
is being asked of the reader, and any change that was in flight. No inference,
no adjectives that were not measured. `BriefSource.TEMPLATE` is stamped on it,
so when a model starts writing these the reader can tell which kind they have -
and a degraded model falls back to exactly this text rather than to nothing.

The one section that matters most is `caveats`. A brief drawn from a declaration
made while the detector was down knows much less than one drawn from a
corroborated proposal, and it says so. A confident brief on thin evidence is how
somebody spends twenty minutes chasing the wrong region.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional, Sequence

from signalforge.domain.incidents.entities import Incident, IncidentTransition
from signalforge.domain.incidents.lifecycle import LifecycleState
from signalforge.domain.warroom.blast import BlastRadius
from signalforge.domain.warroom.entities import (
    BriefSource,
    IncidentTask,
    JoinBrief,
    RoomMember,
    TaskStatus,
)

MAX_EVIDENCE_LINES = 5
MAX_DONE_LINES = 6
MAX_TASK_LINES = 5


def build_brief(
    incident: Incident,
    *,
    radius: BlastRadius,
    now: datetime,
    members: Sequence[RoomMember] = (),
    transitions: Sequence[IncidentTransition] = (),
    tasks: Sequence[IncidentTask] = (),
    for_user: Optional[str] = None,
    for_role_reason: str = "",
) -> JoinBrief:
    """One brief, optionally addressed to the person about to read it.

    `for_user` and `for_role_reason` change only the "what we need" section: a
    responder wants to know what is being asked of *them*, and a brief that
    reads identically to everybody makes each reader work out their own part.
    """
    minutes = int(incident.age_s(now) // 60)
    age = f"{minutes} minute{'s' if minutes != 1 else ''} old"

    return JoinBrief(
        incident_id=incident.id,
        generated_at=now,
        source=BriefSource.TEMPLATE,
        headline=(
            f"{incident.reference}: {incident.severity.value} on {incident.app_id}, "
            f"{incident.state.value}, {age}. {incident.title}"
        ),
        what_is_happening=_happening(incident, radius),
        impact=_impact(radius),
        what_we_know=_known(incident),
        what_has_been_done=_done(incident, transitions, tasks),
        change_context=_changes(incident),
        what_we_need=_needed(incident, tasks, for_user, for_role_reason, members),
        caveats=_caveats(incident, radius),
    )


def _happening(incident: Incident, radius: BlastRadius) -> tuple[str, ...]:
    lines = [radius.summary()]
    if incident.summary:
        lines.append(incident.summary)
    proposal = incident.proposal
    if proposal and proposal.health_score is not None:
        lines.append(
            f"Health score {proposal.health_score:.0f} ({proposal.health_band or 'unknown'}), "
            f"worst region wins."
        )
    if proposal and proposal.stance:
        lines.append(
            f"Detection was at `{proposal.stance}` when the incident was declared."
        )
    return tuple(lines)


def _impact(radius: BlastRadius) -> tuple[str, ...]:
    lines: list[str] = []
    if radius.regions:
        scope = (
            f"all {radius.regions_total} regions"
            if radius.whole_estate
            else f"{len(radius.regions)} of {radius.regions_total or len(radius.regions)} regions"
        )
        lines.append(f"{scope}: {', '.join(radius.regions)}")
    if radius.journeys:
        tier = f" (tier {radius.journey_tier})" if radius.journey_tier else ""
        lines.append(f"Journeys affected{tier}: {', '.join(radius.journeys)}")
    lines.append(
        "Customers are feeling this."
        if radius.customer_facing
        else "No customer-facing journey is implicated by the evidence held."
    )
    if radius.services:
        more = f" (+{len(radius.services)} in the radius)" if radius.truncated else ""
        lines.append(f"Downstream of the fault: {', '.join(radius.services[:8])}{more}")
    return tuple(lines)


def _known(incident: Incident) -> tuple[str, ...]:
    proposal = incident.proposal
    if proposal is None or proposal.unavailable:
        # An *empty* section here would be worse than a short one: the reader
        # would see no "What we know" at all and take that for a formatting
        # quirk rather than for the finding it is.
        reason = proposal.unavailable if proposal else ""
        return (
            "No detection verdict was attached to this declaration"
            + (f" ({reason})" if reason else "")
            + ". Everything below is what a person put in by hand.",
        )
    lines: list[str] = []
    if proposal.corroborated:
        lines.append(
            "Corroborated across independent signal families: "
            + ", ".join(proposal.families)
        )
    elif proposal.families:
        lines.append(
            "Not corroborated - only "
            + ", ".join(proposal.families)
            + " is showing this, so treat the cause as unconfirmed."
        )
    if proposal.success_rate_pct is not None:
        lines.append(f"Success rate at declare: {proposal.success_rate_pct:.1f}%")
    for reason in proposal.reasons[:3]:
        lines.append(reason)
    for ref in proposal.evidence[:MAX_EVIDENCE_LINES]:
        region = f" [{ref.region}]" if ref.region else ""
        lines.append(f"Evidence: {ref.kind} {ref.ref}{region} {ref.detail}".strip())
    if incident.severity_source.value == "chosen":
        proposed = proposal.severity.value if proposal.severity else "nothing"
        lines.append(
            f"The commander declared {incident.severity.value}; the matrix "
            f"proposed {proposed}."
        )
    return tuple(lines)


def _done(
    incident: Incident,
    transitions: Sequence[IncidentTransition],
    tasks: Sequence[IncidentTask],
) -> tuple[str, ...]:
    lines: list[str] = []
    for transition in list(transitions)[-MAX_DONE_LINES:]:
        who = transition.actor_id
        reason = f" - {transition.reason}" if transition.reason else ""
        if transition.from_state is transition.to_state:
            lines.append(
                f"{_hhmm(transition.at)} severity "
                f"{(transition.severity_before.value if transition.severity_before else '?')}"
                f" -> {(transition.severity_after.value if transition.severity_after else '?')}"
                f" by {who}{reason}"
            )
        else:
            lines.append(
                f"{_hhmm(transition.at)} {transition.from_state.value} -> "
                f"{transition.to_state.value} by {who}{reason}"
            )
    done = [task for task in tasks if task.status is TaskStatus.DONE]
    for task in done[:MAX_TASK_LINES]:
        lines.append(f"Done: {task.title}")
    if incident.ticket.exists:
        lines.append(
            f"ITSM ticket {incident.ticket.reference} "
            f"({incident.ticket.status.value if incident.ticket.status else 'open'})"
        )
    if not lines:
        lines.append("Nothing has been recorded yet - this incident is new.")
    return tuple(lines)


def _changes(incident: Incident) -> tuple[str, ...]:
    if not incident.change_windows:
        return ()
    lines = [
        "A change was in flight when this was declared - the first thing to rule "
        "out:"
    ]
    for link in incident.change_windows[:4]:
        version = f" {link.version}" if link.version else ""
        started = f" started {_hhmm(link.started_at)}" if link.started_at else ""
        actor = f" by {link.actor}" if link.actor else ""
        lines.append(f"{link.change_type} on {link.service}{version}{started}{actor}")
    return tuple(lines)


def _needed(
    incident: Incident,
    tasks: Sequence[IncidentTask],
    for_user: Optional[str],
    for_role_reason: str,
    members: Sequence[RoomMember],
) -> tuple[str, ...]:
    lines: list[str] = []
    if for_role_reason:
        lines.append(f"You were paged because: {for_role_reason}")
    mine = [
        task
        for task in tasks
        if task.status is TaskStatus.OPEN and for_user and task.owner_user_id == for_user
    ]
    for task in mine[:MAX_TASK_LINES]:
        due = f" (due {_hhmm(task.due_at)})" if task.due_at else ""
        lines.append(f"Yours: {task.title}{due}")
    unassigned = [
        task
        for task in tasks
        if task.status is TaskStatus.OPEN and not task.owner_user_id
    ]
    for task in unassigned[:MAX_TASK_LINES]:
        lines.append(
            f"Unowned: {task.title}"
            + (f" (committed as {task.owner_label})" if task.owner_label else "")
        )
    if incident.state is LifecycleState.RECOVERING:
        lines.append(
            "The incident is recovering: mitigation is applied and is being "
            "validated. Watch for a regression rather than starting new work."
        )
    present = [member.user_id for member in members if member.present]
    if present:
        lines.append(f"{len(present)} person(s) already in the room.")
    if not lines:
        lines.append(
            "No task is assigned to you yet. Say what you are picking up on the "
            "bridge and it becomes one."
        )
    return tuple(lines)


TEMPLATE_PROVENANCE = "Written from stored facts by a template, not by a model."
"""The last caveat of every deterministic brief: who wrote this.

It is provenance rather than a caveat about the evidence, and the difference
matters where the two are treated differently - a model rewrite must keep every
evidence caveat, and must not keep this one, because it would then be a sentence
saying no model wrote a brief a model wrote."""


def _caveats(incident: Incident, radius: BlastRadius) -> tuple[str, ...]:
    lines: list[str] = []
    proposal = incident.proposal
    if proposal is None or proposal.unavailable:
        reason = proposal.unavailable if proposal else "no verdict was attached"
        lines.append(
            "This brief is drawn from the declaration alone: detection could not "
            f"be read when the incident was declared ({reason}). Nothing here has "
            "been corroborated by the platform."
        )
    elif not proposal.corroborated:
        lines.append(
            "The evidence is not corroborated - one signal family, not two - so "
            "the cause named above is a hypothesis."
        )
    if radius.source == "declaration":
        lines.append(
            "The blast radius is the severity a person typed; no topology or "
            "journey evidence was available to narrow it."
        )
    if not radius.services:
        lines.append(
            "The blast radius names no individual services, so nothing here says "
            "what is downstream of the fault. Check Affected Services for the map "
            "itself."
        )
    lines.append(TEMPLATE_PROVENANCE)
    return tuple(lines)


def _hhmm(moment: Optional[datetime]) -> str:
    return moment.strftime("%H:%M") if moment else "--:--"
