"""Hypothesis reasoning: competing explanations, scored deterministically
(REQ-053).

This agent runs *after* the graph write, not with it, and that ordering is the
whole design. The other agents put evidence into the graph and the graph assigns
each node a real id; only then can a hypothesis be linked to the exact node
versions that argued for and against it, which is what makes "why did #2 move
from 65% to 32%?" answerable by reading rows rather than by trusting prose.

Three rules, in descending order of how much damage breaking them would do:

1. **The number is arithmetic, never a model's opinion.** Confidence comes from
   `domain/brain/confidence.score` over weighted, authority-scaled evidence. A
   model asked "how confident are you" answers 83% with equal fluency on three
   corroborating signals and on none.
2. **Competing hypotheses are maintained, not collapsed.** The catalogue below
   always proposes the three first-order explanations of an incident - a change,
   a saturation or dependency fault, and an infrastructure or regional fault -
   plus any the evidence specifically suggests. A single hypothesis presented
   confidently is how a room spends forty minutes on the wrong one.
3. **Evidence against is first-class.** `REFUTES` edges lower the number, and
   the card lists them. A hypothesis with nothing against it is not strong; it is
   untested, and `discriminating_need` says so.

The model's only job here is the narrative sentence on a confidence movement -
prose beside a number it did not produce - and when there is no model, the
deterministic `ConfidenceEntry.explain()` stands in its place and is always
populated regardless.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional, Sequence

from signalforge.application.brain.agents.base import (
    AgentOutcome,
    EdgeProposal,
    ModelBackedAgent,
    NodeProposal,
)
from signalforge.application.brain.agents.change import proximate_change
from signalforge.application.brain.common import HYPOTHESIS_MOVED_TOPIC
from signalforge.application.brain.evidence import IncidentContext
from signalforge.contracts.envelope import Authority
from signalforge.domain.brain.confidence import (
    ConfidenceEntry,
    EvidenceKind,
    Stance,
    WeightedEvidence,
    discriminating_need,
    entry_for,
)
from signalforge.domain.brain.nodes import (
    GraphNode,
    NodeType,
    Relation,
    derived_key,
)
from signalforge.domain.brain.untrusted import SYSTEM_FLOOR
from signalforge.domain.llm.entities import (
    CompletionRequest,
    Message,
    ModelClass,
    TokenUsage,
)

logger = logging.getLogger("signalforge.brain")

PURPOSE = "hypothesis-narrative"
MATERIAL_DELTA = 0.05
"""How much a confidence has to move before it is worth an event and a narrative.
A number that jitters by a percentage point every thirty seconds is a number
people stop reading."""

NARRATIVE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["narratives"],
    "properties": {
        "narratives": {
            "type": "array",
            "maxItems": 6,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["key", "narrative"],
                "properties": {
                    "key": {"type": "string"},
                    "narrative": {
                        "type": "string",
                        "maxLength": 400,
                        "description": (
                            "One or two sentences explaining what moved this "
                            "hypothesis, using only the evidence listed. Do not "
                            "state a confidence number."
                        ),
                    },
                },
            },
        }
    },
}


@dataclass(frozen=True)
class HypothesisSpec:
    """One candidate explanation, before any evidence is attached."""

    slug: str
    title: str
    subject: str
    rationale: str

    @property
    def key(self) -> str:
        return derived_key(NodeType.HYPOTHESIS, self.slug)


class HypothesisAgent(ModelBackedAgent):
    name = "hypothesis-agent"

    async def run(self, context: IncidentContext) -> AgentOutcome:
        # This agent is driven by the governor's second phase, which calls
        # `reason` with the written graph. Running it in phase one would link
        # hypotheses to nodes that do not have ids yet.
        return AgentOutcome(skipped="hypothesis reasoning runs after the graph merge")

    async def reason(
        self,
        context: IncidentContext,
        nodes: Sequence[GraphNode],
        *,
        previous: dict[str, Optional[float]],
        previous_ids: dict[str, Sequence[str]],
        trigger: str = "",
    ) -> tuple[AgentOutcome, list[ConfidenceEntry]]:
        """Score every hypothesis against the graph as it now stands."""
        specs = catalogue(context)
        evidence_nodes = [
            node
            for node in nodes
            if node.node_type
            in (NodeType.EVIDENCE, NodeType.FACT, NodeType.RISK, NodeType.IMPACT)
            and node.current
        ]

        proposals: list[NodeProposal] = []
        edges: list[EdgeProposal] = []
        events: list[tuple[str, dict]] = []
        entries: list[ConfidenceEntry] = []
        moved: list[tuple[HypothesisSpec, ConfidenceEntry, tuple[WeightedEvidence, ...]]] = []

        for spec in specs:
            weighted = tuple(
                link
                for node in evidence_nodes
                for link in (_link(spec, node),)
                if link is not None
            )
            entry = entry_for(
                hypothesis_key=spec.key,
                at=context.now,
                previous=previous.get(spec.key),
                evidence=weighted,
                previous_ids=previous_ids.get(spec.key, ()),
                trigger=trigger,
            )
            entries.append(entry)
            need = discriminating_need(weighted, entry.current)
            proposals.append(
                NodeProposal(
                    node_type=NodeType.HYPOTHESIS,
                    derived_key=spec.key,
                    label=spec.title,
                    content=(
                        f"{spec.rationale}\n\nDiscriminating test: {need}"
                    ),
                    provenance=self.provenance(
                        source=self.name,
                        detail={
                            "scoring": "deterministic weighted evidence",
                            "net_weight": entry.net_weight,
                        },
                    ),
                    confidence=entry.current,
                    detail={
                        "subject": spec.subject,
                        "discriminating_test": need,
                        "net_weight": entry.net_weight,
                        "supporting": list(entry.supporting),
                        "refuting": list(entry.refuting),
                        "explain": entry.explain(),
                    },
                    tags=("hypothesis",),
                )
            )
            for link in weighted:
                edges.append(
                    EdgeProposal(
                        source_key=link.node_id,
                        target_key=spec.key,
                        relation=(
                            Relation.SUPPORTS
                            if link.stance is Stance.SUPPORTS
                            else Relation.REFUTES
                        ),
                        weight=link.weight,
                        note=link.label,
                        detail={"kind": link.kind.value, "by_id": True},
                    )
                )
            if abs(entry.delta) >= MATERIAL_DELTA or entry.previous is None:
                moved.append((spec, entry, weighted))

        result = await self._narratives(context, moved)
        by_key = {}
        if result is not None and isinstance(result.data, dict):
            for raw in result.data.get("narratives", []) or []:
                key = str(raw.get("key") or "")
                if any(spec.key == key for spec, _entry, _e in moved):
                    by_key[key] = str(raw.get("narrative") or "")[:400]

        final_entries: list[ConfidenceEntry] = []
        for entry in entries:
            narrative = by_key.get(entry.hypothesis_key, "")
            degraded = bool(result is None or result.degraded) and not narrative
            final_entries.append(
                ConfidenceEntry(
                    hypothesis_key=entry.hypothesis_key,
                    at=entry.at,
                    previous=entry.previous,
                    current=entry.current,
                    net_weight=entry.net_weight,
                    supporting=entry.supporting,
                    refuting=entry.refuting,
                    added=entry.added,
                    removed=entry.removed,
                    trigger=entry.trigger,
                    narrative=narrative,
                    narrative_degraded=degraded,
                    detail={
                        "explain": entry.explain(),
                        "model": result.model if result is not None else "",
                    },
                )
            )

        for spec, entry, _weighted in moved:
            events.append(
                (
                    HYPOTHESIS_MOVED_TOPIC,
                    {
                        "incident_id": context.incident_id,
                        "hypothesis_key": spec.key,
                        "hypothesis": spec.title,
                        "previous": entry.previous,
                        "current": entry.current,
                        "delta": round(entry.delta, 4),
                        "direction": entry.direction,
                        "net_weight": round(entry.net_weight, 4),
                        "supporting": len(entry.supporting),
                        "refuting": len(entry.refuting),
                        "added_evidence": list(entry.added)[:10],
                        "explain": entry.explain(),
                        "narrative": by_key.get(spec.key, ""),
                        "scoring": "deterministic",
                    },
                )
            )

        return (
            AgentOutcome(
                nodes=tuple(proposals),
                edges=tuple(edges),
                events=tuple(events),
                degraded=bool(result is not None and result.degraded),
                degraded_reason=(
                    result.reason.value if result is not None and result.reason else ""
                ),
                model=result.model if result is not None else "",
                usage=result.usage if result is not None else TokenUsage(),
                notes=(f"{len(specs)} hypothesis(es), {len(moved)} moved",),
            ),
            final_entries,
        )

    async def _narratives(self, context: IncidentContext, moved) -> Optional[object]:
        """Prose beside a number the model did not produce.

        The prompt is given the *computed* delta and the evidence labels and is
        asked to explain, not to score - and the schema forbids a number field
        entirely, so there is nothing for a confidence to arrive in even if the
        model tried.
        """
        if not moved:
            return None
        lines = []
        for spec, entry, weighted in moved[:6]:
            supporting = [
                link.label for link in weighted if link.stance is Stance.SUPPORTS
            ][:5]
            refuting = [
                link.label for link in weighted if link.stance is Stance.REFUTES
            ][:5]
            lines.append(
                f"key={spec.key}\n"
                f"  hypothesis: {spec.title}\n"
                f"  computed movement: {entry.explain()}\n"
                f"  evidence for: {'; '.join(supporting) or 'none'}\n"
                f"  evidence against: {'; '.join(refuting) or 'none'}"
            )
        prompt = (
            "SignalForge has already computed each hypothesis's confidence from "
            "weighted evidence. Write one or two sentences per hypothesis "
            "explaining what moved it, using only the evidence listed.\n\n"
            "Do not state, restate or estimate any confidence number - the "
            "number is computed and is shown beside your sentence. Do not "
            "introduce evidence that is not listed.\n\n" + "\n\n".join(lines)
        )
        return await self.llm.complete(
            CompletionRequest(
                purpose=PURPOSE,
                messages=(
                    Message(role="system", content=SYSTEM_FLOOR),
                    Message(role="user", content=prompt),
                ),
                model_class=ModelClass.GENERAL,
                schema=NARRATIVE_SCHEMA,
                fallback={"narratives": []},
                tenant_id=context.tenant_id,
                incident_id=context.incident_id,
                max_tokens=600,
            )
        )


# --- the catalogue -------------------------------------------------------------


def catalogue(context: IncidentContext) -> tuple[HypothesisSpec, ...]:
    """The competing explanations this incident is scored against.

    Three are always present, because they are the three first-order causes of
    almost every production incident and because a room that sees one hypothesis
    tests one hypothesis. They start at the prior and move only on evidence -
    an unevidenced hypothesis sitting at 35% is an honest statement, and it is
    also the thing that makes a *fourth*, well-evidenced one visibly different.

    Anything the evidence specifically suggests is appended: the proximate
    change by name, and a recurrence of a prior incident's recorded cause.
    """
    specs: list[HypothesisSpec] = []

    change = proximate_change(context)
    if change is not None:
        specs.append(
            HypothesisSpec(
                slug=f"change-{change.change_id}",
                title=(
                    f"The {change.change_type.value} of {change.service}"
                    + (f" {change.version}" if change.version else "")
                    + " introduced the fault"
                ),
                subject=change.service,
                rationale=(
                    "A change to this service was in flight around the onset. "
                    "Temporal proximity is not causality; this hypothesis exists "
                    "so the timing can be tested rather than assumed."
                ),
            )
        )
    else:
        specs.append(
            HypothesisSpec(
                slug="recent-change",
                title="A recent change introduced the fault",
                subject="",
                rationale=(
                    "The standing first question of any incident. No change "
                    "window sits inside the onset window, so nothing supports "
                    "this yet - which is itself worth knowing."
                ),
            )
        )

    worst = _worst_subject(context)
    specs.append(
        HypothesisSpec(
            slug="saturation",
            title=(
                f"{worst or 'A dependency'} is saturated or degraded and callers "
                "are queueing behind it"
            ),
            subject=worst,
            rationale=(
                "The classic shape: one resource slows, its callers retry, and "
                "the symptom appears everywhere except where the cause is."
            ),
        )
    )

    regions = _regions(context)
    specs.append(
        HypothesisSpec(
            slug="infrastructure",
            title=(
                "An infrastructure or regional fault underneath the application"
                + (f" ({', '.join(regions)})" if regions else "")
            ),
            subject=regions[0] if regions else "",
            rationale=(
                "Everything above the platform looks broken because something "
                "below it is. Distinguished from the others by whether the fault "
                "follows the region rather than the service."
            ),
        )
    )

    for prior in context.priors:
        if not prior.confirmed_cause:
            continue
        specs.append(
            HypothesisSpec(
                slug=f"recurrence-{prior.incident_id}",
                title=f"A recurrence of {prior.reference}: {prior.confirmed_cause}",
                subject=prior.confirmed_cause,
                rationale=(
                    f"{prior.reference} closed with this recorded as the cause "
                    "and resembles this incident. That is advisory evidence "
                    "about a past incident, never a finding about this one."
                ),
            )
        )
        break

    return tuple(specs)


def _worst_subject(context: IncidentContext) -> str:
    verdict = context.verdict
    if verdict is None or not verdict.anomalous:
        return ""
    worst = max(verdict.anomalous, key=lambda state: abs(state.deviation or 0.0))
    return worst.signal_key or ""


def _regions(context: IncidentContext) -> tuple[str, ...]:
    if context.room is not None and context.room.radius.regions:
        return tuple(context.room.radius.regions[:3])
    verdict = context.verdict
    if verdict is not None:
        return tuple(sorted({s.region for s in verdict.anomalous if s.region})[:3])
    return ()


# --- linking evidence to hypotheses ---------------------------------------------


def _link(spec: HypothesisSpec, node: GraphNode) -> Optional[WeightedEvidence]:
    """Whether this node argues for or against this hypothesis, and how hard.

    Deterministic and deliberately literal: a node is linked when its recorded
    subject matches the hypothesis's subject, or when its kind is one that bears
    on that hypothesis by construction (a change window on the change
    hypothesis, a topology path on the saturation one). A looser matcher would
    link everything to everything and the confidence numbers would converge,
    which is the same as having no numbers.
    """
    detail = dict(node.detail or {})
    raw_kind = str(detail.get("evidence_kind") or "")
    if not raw_kind:
        return None
    try:
        kind = EvidenceKind(raw_kind)
    except ValueError:
        return None

    subject = str(detail.get("subject") or "").strip().lower()
    wanted = spec.subject.strip().lower()
    strength = float(detail.get("strength") or 1.0)
    stance = Stance.SUPPORTS

    if spec.slug.startswith("change-"):
        if kind is not EvidenceKind.CHANGE_PROXIMITY:
            return None
        if not detail.get("proximate"):
            # A change outside the onset window is evidence *against* a
            # change-induced hypothesis: it is the deploy nobody can blame.
            stance = Stance.REFUTES
            strength = 0.4
    elif spec.slug == "recent-change":
        if kind is not EvidenceKind.CHANGE_PROXIMITY:
            return None
    elif spec.slug == "saturation":
        if kind not in (
            EvidenceKind.TELEMETRY_ANOMALY,
            EvidenceKind.SLO_BURN,
            EvidenceKind.TOPOLOGY_PATH,
            EvidenceKind.CORROBORATION,
            EvidenceKind.ALERT_CLUSTER,
            EvidenceKind.HUMAN_STATEMENT,
            EvidenceKind.CONTRADICTION,
            EvidenceKind.DIAGNOSTIC_RESULT,
        ):
            return None
        if kind is EvidenceKind.CONTRADICTION:
            # A human insisting a component is healthy while it is measured as
            # failing weakens a hypothesis that rests on that component - and
            # strengthens the case that somebody is looking at the wrong thing.
            stance = Stance.REFUTES
        if wanted and subject and wanted not in subject and subject not in wanted:
            if kind in (EvidenceKind.TELEMETRY_ANOMALY, EvidenceKind.TOPOLOGY_PATH):
                return None
    elif spec.slug == "infrastructure":
        if kind not in (
            EvidenceKind.PROBE_FAILURE,
            EvidenceKind.HEALTH_SCORE,
            EvidenceKind.CORROBORATION,
            EvidenceKind.ALERT_CLUSTER,
            EvidenceKind.TOPOLOGY_PATH,
        ):
            return None
        # Corroboration across families is the signature of something below the
        # application; a single-family anomaly argues the other way.
        if kind is EvidenceKind.CORROBORATION and not detail.get("corroborated", True):
            stance = Stance.REFUTES
    elif spec.slug.startswith("recurrence-"):
        if kind is not EvidenceKind.HISTORICAL_SIMILARITY:
            return None

    authority = node.provenance.authority if node.provenance else Authority.INFERRED
    return WeightedEvidence(
        node_id=node.id,
        kind=kind,
        stance=stance,
        authority=authority,
        strength=max(0.0, min(2.0, strength)),
        label=node.label,
    )
