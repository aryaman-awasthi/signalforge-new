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
import re
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
class ResourceSignature:
    """A root cause the evidence can name outright, and the words that name it.

    `title` and `rationale` are per cause because the sentence an incident
    commander reads has to say what kind of thing is broken: an expired license
    is not a saturated resource, and a card that called it one would send the
    room looking for load that is not there.
    """

    root_cause: str
    mechanism: str
    markers: tuple[str, ...]
    title: str
    rationale: str
    subject_pattern: str = ""
    """A regex whose first group is the thing the evidence says is broken -
    "payment processor" in "payment processor license expired". A journey or a
    signal key is the node's subject; the component the application itself
    named is a better one, and only the text has it."""
    fallback_subject: str = ""
    """What to call the subject when neither the text nor the node names one.
    Empty means "use the worst signal", which is right for a saturated resource
    and wrong for a license, where the worst signal is a symptom."""


_EXHAUSTION_RATIONALE = (
    "A resource is not slow, it is gone: operations fail rather than queue. The "
    "remediation has to remove what is consuming the resource, which is a "
    "different action from undoing a change that happened to be nearby."
)

RESOURCE_SIGNATURES: tuple[ResourceSignature, ...] = (
    # Ordered most specific first: a filesystem that is full is a different
    # incident from a service that is merely slow, and the remediation that
    # removes it is a different action. Markers match as whole words (see
    # `_contains`), so "oom" is not found inside "war room".
    ResourceSignature(
        root_cause="license_expiry",
        mechanism="expired_license_refuses_requests",
        markers=(
            "license expired", "licence expired", "license has expired",
            "licence has expired", "expired license", "expired licence",
            "license lapsed", "licence lapsed", "license is expired",
            "invalid license", "license invalid", "entitlement expired",
            "subscription expired", "activation expired", "license_expired",
        ),
        title="An expired {subject} license is refusing every request outright",
        rationale=(
            "A credential-shaped failure, not a capacity one: nothing is slow and "
            "nothing is saturated; a dependency is refusing requests because an "
            "entitlement has lapsed. The remediation is a renewal - a rollback or "
            "a failover does not touch it."
        ),
        # Letters, digits and spaces only in the group: an error code such as
        # LICENSE_EXPIRED sits in the same sentence, and a class that admitted
        # underscores captured it as part of the name.
        subject_pattern=(
            r"\b([a-z][a-z0-9 ]{2,40}?)\s+licen[cs]e\s+"
            r"(?:key\s+)?(?:has\s+)?(?:expired|lapsed|is\s+expired|is\s+invalid)"
        ),
        fallback_subject="dependency",
    ),
    ResourceSignature(
        root_cause="disk_saturation",
        mechanism="log_and_temp_file_growth",
        markers=(
            "no space left on device", "disk full", "filesystem full",
            "disk saturation", "disk utilisation", "disk utilization",
            "disk usage", "disk_used", "disk", "filesystem", "file system",
            "inode", "volume full", "storage full", "enospc", "/var/log",
        ),
        title="Disk saturation on {subject} is failing operations outright",
        rationale=_EXHAUSTION_RATIONALE,
    ),
    ResourceSignature(
        root_cause="memory_exhaustion",
        mechanism="memory_growth",
        markers=(
            "out of memory", "oom", "heap exhaust", "memory exhaust",
            "memory usage", "memory_used", "swap",
        ),
        title="Memory exhaustion on {subject} is failing operations outright",
        rationale=_EXHAUSTION_RATIONALE,
    ),
    ResourceSignature(
        root_cause="connection_pool_exhaustion",
        mechanism="connection_pool_starvation",
        markers=(
            "connection pool", "pool exhaust", "pool saturation",
            "connection_pool", "no connections available",
        ),
        title="Connection pool exhaustion on {subject} is failing operations outright",
        rationale=_EXHAUSTION_RATIONALE,
    ),
)


@dataclass(frozen=True)
class ResourceMatch:
    signature: ResourceSignature
    subject: str


_MARKER_PATTERNS: dict[str, "re.Pattern[str]"] = {}


def _contains(marker: str, text: str) -> bool:
    """Whole-word presence of `marker` in lowercase `text`.

    A bare substring test found "oom" in "war room" and "swap" in "swapped",
    and renamed the incident. Word boundaries are letters and digits; a space
    in the marker matches a space, an underscore or a hyphen, so the error code
    `LICENSE_EXPIRED` and the sentence "license expired" are the same marker.
    """
    pattern = _MARKER_PATTERNS.get(marker)
    if pattern is None:
        body = re.escape(marker).replace(r"\ ", r"[\s_\-]+")
        pattern = re.compile(rf"(?<![a-z0-9]){body}(?![a-z0-9])")
        _MARKER_PATTERNS[marker] = pattern
    return pattern.search(text) is not None

DEPLOYMENT_LINK_MARKERS: tuple[str, ...] = (
    "log level", "logging level", "log verbosity", "verbosity", "debug logging",
    "debug log", "trace logging", "log volume increase", "increased logging",
    "logging enabled", "new log",
)
"""What has to be said somewhere in the record before a change is treated as the
*cause* of a resource being consumed rather than merely a thing that happened
nearby.

This is the whole rollback question in one constant. "A deployment exists and
the disk is full" is a coincidence until something connects the two, and a
platform that rolls back on the coincidence is a platform that restarts the
estate without freeing a byte."""


@dataclass(frozen=True)
class HypothesisSpec:
    """One candidate explanation, before any evidence is attached."""

    slug: str
    title: str
    subject: str
    rationale: str
    root_cause: str = ""
    """The taxonomy name for what is broken - `disk_saturation`,
    `deployment_change`. Read by the next-best-action engine, which asks of every
    candidate action how it removes *this*."""
    mechanism: str = ""
    """How the root cause produces the symptom - `log_and_temp_file_growth`. The
    remediation has to act on the mechanism; the root cause alone is not enough
    to choose between rotating logs and rolling back."""
    missing_evidence: tuple[str, ...] = ()
    """What would settle this that the platform does not hold. Named rather than
    assumed absent, because "we have no evidence the deploy caused it" and "we
    never looked" are different sentences and only one of them justifies waiting."""

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
        evidence_nodes = [
            node
            for node in nodes
            if node.node_type
            in (NodeType.EVIDENCE, NodeType.FACT, NodeType.RISK, NodeType.IMPACT)
            and node.current
        ]
        # The catalogue is built from the evidence rather than from the incident
        # alone: a filesystem that is measurably full is a named root cause with
        # a named mechanism, not a generic "something is saturated".
        specs = catalogue(context, nodes=evidence_nodes)

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
                        # The RCA the remediation planner consumes. Written on
                        # the node rather than derived at read time so that the
                        # action ranked at 03:14 can be justified from the row
                        # six months later, against the belief that was actually
                        # held then.
                        "root_cause": spec.root_cause,
                        "causal_mechanism": spec.mechanism,
                        "supporting_evidence": [
                            link.label
                            for link in weighted
                            if link.stance is Stance.SUPPORTS
                        ][:10],
                        "contradicting_evidence": [
                            link.label
                            for link in weighted
                            if link.stance is Stance.REFUTES
                        ][:10],
                        "missing_evidence": list(spec.missing_evidence),
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


def catalogue(
    context: IncidentContext, *, nodes: Sequence[GraphNode] = ()
) -> tuple[HypothesisSpec, ...]:
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

    resource = _resource_exhaustion(context, nodes)
    change = proximate_change(context)
    linked = _deployment_link(context, nodes)

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
                root_cause="deployment_change",
                mechanism=(
                    f"deployment_induced_{resource.signature.mechanism}"
                    if resource is not None and linked
                    else "change_introduced_fault"
                ),
                missing_evidence=(
                    ()
                    if linked or resource is None
                    else (
                        "evidence connecting this change to the "
                        f"{resource.signature.root_cause.replace('_', ' ')} - a "
                        "log-volume, configuration or verbosity difference across "
                        "the deploy",
                    )
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
                root_cause="deployment_change",
                mechanism="change_introduced_fault",
                missing_evidence=("a change window inside the onset window",),
            )
        )

    worst = _worst_subject(context)
    if resource is not None:
        # The saturation slot, made specific. Same slug, so the node keeps its
        # identity and simply gains a version as the belief sharpens - which is
        # what the graph's versioning is for, and what lets the card show "we
        # thought a dependency was slow; it is the filesystem".
        signature = resource.signature
        subject = resource.subject
        specs.append(
            HypothesisSpec(
                slug="saturation",
                title=signature.title.format(
                    subject=subject
                    or signature.fallback_subject
                    or worst
                    or "the application"
                ),
                subject=subject or worst,
                rationale=signature.rationale,
                root_cause=signature.root_cause,
                mechanism=(
                    f"deployment_induced_{signature.mechanism}"
                    if linked
                    else signature.mechanism
                ),
                missing_evidence=(
                    ()
                    if linked or change is None
                    else (
                        "evidence connecting the change in the onset window to "
                        "the resource growth",
                    )
                ),
            )
        )
    else:
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
                root_cause="dependency_saturation",
                mechanism="callers_queueing_behind_a_slow_dependency",
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
            root_cause="infrastructure_fault",
            mechanism="fault_below_the_application_follows_the_region",
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


def _evidence_text(
    context: IncidentContext, nodes: Sequence[GraphNode]
) -> tuple[str, ...]:
    """Everything the incident has actually said, as lowercase text.

    Nodes first, because they are the platform's own measurements; bridge
    messages after, because a person naming what they are looking at is evidence
    even when nothing has measured it yet.
    """
    parts: list[str] = []
    for node in nodes:
        detail = dict(node.detail or {})
        parts.append(
            " ".join(
                str(value)
                for value in (
                    node.label,
                    node.content,
                    detail.get("subject"),
                    detail.get("signal_key"),
                    # What the application answered a failing probe with.
                    detail.get("last_detail"),
                )
                if value
            ).lower()
        )
    for message in tuple(getattr(context, "messages", ()) or ())[:50]:
        text = str(message.get("text") or message.get("body") or "")
        if text:
            parts.append(text.lower())
    if context.verdict is not None:
        parts.extend(
            f"{state.signal_key} {state.family}".lower()
            for state in context.verdict.anomalous
            if state.signal_key
        )
    return tuple(part for part in parts if part)


def _resource_exhaustion(
    context: IncidentContext, nodes: Sequence[GraphNode]
) -> Optional[ResourceMatch]:
    """The signature the evidence names outright, and the subject it names it on,
    when a resource has been exhausted - or an entitlement has lapsed - rather
    than merely degraded.

    Read from what the incident holds, never from the severity or the symptom: a
    payment failure rate tells you customers are hurting and tells you nothing
    about which resource ran out.
    """
    corpus = _evidence_text(context, nodes)
    if not corpus:
        return None
    for signature in RESOURCE_SIGNATURES:
        for part in corpus:
            hit = next(
                (marker for marker in signature.markers if _contains(marker, part)),
                None,
            )
            if hit is None:
                continue
            subject = ""
            if signature.subject_pattern:
                # The component the application itself named, read from the
                # sentence that named it. "payment processor license expired"
                # says what is broken better than the journey that noticed.
                for text in corpus:
                    found = re.search(signature.subject_pattern, text)
                    if found:
                        subject = re.sub(
                            r"^(?:the|a|an|our|its)\s+", "", found.group(1).strip()
                        )
                        break
            if not subject and not signature.subject_pattern:
                for node in nodes:
                    detail = dict(node.detail or {})
                    blob = (
                        f"{node.label} {node.content} {detail.get('last_detail') or ''}"
                    ).lower()
                    if _contains(hit, blob):
                        subject = (
                            str(detail.get("component") or "")
                            or str(detail.get("subject") or "")
                            or subject
                        )
                        if subject:
                            break
            return ResourceMatch(signature=signature, subject=subject)
    return None


def names_a_resource(text: str) -> bool:
    """Whether the text names one of the resource signatures outright.

    Used where a first-hand observation has to be read for *what it says*
    rather than for the fact that it failed: a probe refused with
    LICENSE_EXPIRED is not evidence of a fault below the application, however
    many of them there are.
    """
    lowered = (text or "").lower()
    if not lowered:
        return False
    return any(
        _contains(marker, lowered)
        for signature in RESOURCE_SIGNATURES
        for marker in signature.markers
    )


def _deployment_link(
    context: IncidentContext, nodes: Sequence[GraphNode]
) -> bool:
    """Whether anything connects a change to the resource being consumed.

    A change in the onset window is not this. Something has to say that the
    change altered what generates the load - a log level, a verbosity, a new
    writer - and until it does, rolling back is an action against a coincidence.
    """
    if proximate_change(context) is None:
        return False
    corpus = _evidence_text(context, nodes) + tuple(
        f"{change.service} {change.version or ''} {change.diff_ref or ''}".lower()
        for change in context.changes
    )
    return any(
        _contains(marker, part) for part in corpus for marker in DEPLOYMENT_LINK_MARKERS
    )


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
            # A failing synthetic probe is first-hand evidence that callers are
            # failing at the dependency this slot names - and when the probe's
            # detail carries the application's own refusal, it is the evidence
            # the slot was named from.
            EvidenceKind.PROBE_FAILURE,
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
        if kind is EvidenceKind.HUMAN_STATEMENT and detail.get("claims_healthy"):
            # Somebody has looked and says the component is fine. That argues
            # against a hypothesis that it is the fault. Without this, a room
            # that had just cleared a suspect would watch its confidence keep
            # rising, which is the fastest way to make people stop reading it.
            stance = Stance.REFUTES
        if kind is EvidenceKind.CORROBORATION and not detail.get("corroborated", True):
            # One family is not two. A saturation hypothesis that rests on a
            # single signal is weaker for it, and the number should say so.
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
        if kind is EvidenceKind.PROBE_FAILURE and names_a_resource(
            str(detail.get("last_detail") or node.content or "")
        ):
            # The probe was *answered* - with a refusal that names what is
            # wrong. A fault beneath the application does not write "license
            # expired" into a 403. Every such probe argues against this slot,
            # and without this a room full of them would rank "something
            # below the application" level with the cause the probes named.
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
