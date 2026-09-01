"""Change intelligence: what was moving when this started (REQ-052).

The first question in every incident is "what changed", and the platform already
holds the answer as change windows folded from deployment hooks (S4-T02). This
agent's job is to turn that into *evidence*, and its entire discipline is one
sentence:

**Temporal proximity is not causality.**

A deployment that started twelve minutes before the incident is a strong hint and
a weak proof. So this agent never writes a fact saying a change caused anything.
It writes an `EVIDENCE` node stating the observed timing, weighted as
`CHANGE_PROXIMITY` - a mid-weight in the confidence policy, chosen so that a
change window alone cannot carry a hypothesis past even odds - and lets the
hypothesis agent link it. The label says "started N minutes before onset",
which is what was observed, rather than "caused by", which was not.

Proximity is scored on a curve rather than a cutoff: a change that started two
minutes before the first anomaly is much more interesting than one that started
eighty, and a binary "within the window" would treat them identically.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from signalforge.application.brain.agents.base import (
    Agent,
    AgentOutcome,
    NodeProposal,
    authoritative,
)
from signalforge.application.brain.evidence import IncidentContext
from signalforge.domain.brain.nodes import (
    ConfirmationState,
    NodeType,
    derived_key,
)

ONSET_WINDOW_S = 90 * 60.0
"""How close a change has to be to the onset to count as proximate. Ninety
minutes: wide enough for a slow rollout, narrow enough that "something was
deployed today" is not evidence."""

MAX_CHANGES = 8


class ChangeIntelligenceAgent(Agent):
    name = "change-agent"

    async def run(self, context: IncidentContext) -> AgentOutcome:
        if not context.changes:
            return AgentOutcome(
                skipped="no change window is recorded for this application",
                nodes=(
                    NodeProposal(
                        node_type=NodeType.FACT,
                        derived_key=derived_key(NodeType.FACT, "no-change"),
                        label="No change was recorded around the onset of this incident",
                        content=(
                            "Either nothing was deployed, or the deployment "
                            "pipeline is not announcing through the ingest API. "
                            "The two are indistinguishable from here, and the "
                            "second is the more common."
                        ),
                        provenance=authoritative(
                            "change-window", result_ref=f"changes:{context.app_id}"
                        ),
                        confirmation=ConfirmationState.CONFIRMED,
                    ),
                ),
            )

        onset = _onset(context)
        nodes: list[NodeProposal] = []
        for change in list(context.changes)[:MAX_CHANGES]:
            offset_s = (onset - change.started_at).total_seconds()
            proximate = 0.0 <= offset_s <= ONSET_WINDOW_S
            minutes = int(abs(offset_s) // 60)
            when = (
                f"started {minutes} minute(s) before the incident was declared"
                if offset_s >= 0
                else f"started {minutes} minute(s) after the incident was declared"
            )
            nodes.append(
                NodeProposal(
                    node_type=NodeType.EVIDENCE,
                    derived_key=derived_key(
                        NodeType.EVIDENCE, "change", change.change_id
                    ),
                    label=(
                        f"{change.change_type.value} on {change.service}"
                        + (f" {change.version}" if change.version else "")
                        + f" {when}"
                    ),
                    content=(
                        f"{change.detail(context.now)} "
                        + (
                            "Still in flight - nothing has announced its completion."
                            if change.open
                            else f"Ended {change.status.value}."
                        )
                        + " Temporal proximity is not causality: this is evidence "
                        "for a hypothesis, not a cause."
                    ),
                    provenance=authoritative(
                        change.source_system or "change-window",
                        result_ref=change.diff_ref or f"change:{change.change_id}",
                        detail={
                            "change_id": change.change_id,
                            "actor": change.actor or "",
                            "diff_ref": change.diff_ref or "",
                        },
                    ),
                    confirmation=ConfirmationState.CONFIRMED,
                    detail={
                        "evidence_kind": "change_proximity",
                        # The curve: 1.0 at the moment of onset, decaying to
                        # roughly a third at the edge of the window, and near
                        # zero outside it.
                        "strength": _proximity(offset_s),
                        "subject": change.service,
                        "proximate": proximate,
                        "offset_s": offset_s,
                        "open": change.open,
                        "status": change.status.value,
                        "version": change.version or "",
                        "diff_ref": change.diff_ref or "",
                    },
                    tags=("proximate",) if proximate else (),
                )
            )

        proximate_count = sum(1 for node in nodes if "proximate" in node.tags)
        return AgentOutcome(
            nodes=tuple(nodes),
            notes=(
                f"{proximate_count} of {len(nodes)} change(s) fall inside the "
                f"{int(ONSET_WINDOW_S // 60)}-minute onset window",
            ),
        )


def _onset(context: IncidentContext) -> datetime:
    """When the trouble started, as well as the platform can say.

    The earliest `anomalous_since` the detector holds, falling back to the
    declaration. Deliberately not the declaration alone: a P1 declared forty
    minutes after the first anomaly would make every change in those forty
    minutes look proximate, including the one deployed *in response*.
    """
    verdict = context.verdict
    if verdict is not None:
        starts = [
            state.anomalous_since
            for state in verdict.anomalous
            if state.anomalous_since is not None
        ]
        if starts:
            return min(starts)
    return context.incident.declared_at


def _proximity(offset_s: float) -> float:
    """Strength as a function of how long before onset the change started."""
    if offset_s < 0:
        # It started *after* the incident. Often the mitigation, which is
        # evidence about the response rather than about the cause.
        return 0.1
    if offset_s > ONSET_WINDOW_S:
        return 0.1
    return round(max(0.2, 1.0 - (offset_s / ONSET_WINDOW_S) * 0.7), 3)


def proximate_change(context: IncidentContext) -> Optional[object]:
    """The single most temporally proximate change, if any is in the window.

    Used by the hypothesis agent to name a change-induced hypothesis after the
    thing that actually moved, rather than after "a change".
    """
    onset = _onset(context)
    best = None
    best_offset = None
    for change in context.changes:
        offset = (onset - change.started_at).total_seconds()
        if not 0.0 <= offset <= ONSET_WINDOW_S:
            continue
        if best_offset is None or offset < best_offset:
            best, best_offset = change, offset
    return best
