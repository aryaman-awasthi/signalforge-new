"""Response shapes for the Incident Brain (S5-T05).

Four distinctions are published as separate fields rather than collapsed,
because each one is something a client would otherwise have to infer - and
inferring any of them wrongly is the failure this sprint exists to prevent.

- **`confirmation`** on an impact: `confirmed` means something was measured on
  that service, `predicted` means it merely depends on something that was. A
  cockpit that rendered them alike would page four teams for an outage that
  never happened.
- **`confidence` beside `history`** on a hypothesis. The number is the platform's
  arithmetic; the history is why it moved, entry by entry, with the deterministic
  explanation always populated and the model's narrative beside it when there is
  one. A screen can answer "why did #2 go from 65% to 32%" without asking the
  server again.
- **`status` and `version`** on every node. `current`, `superseded`, `retracted`,
  and a version number that opens the history. Nothing is deleted, so a client
  that only ever showed the current view would be hiding the interesting half.
- **`verdict`** on a copilot answer: `grounded`, `insufficient_evidence`,
  `ungrounded` or `degraded`. Four states an operator learns once, rather than a
  paragraph they have to read to find out whether the assistant actually knew.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field

from signalforge.application.brain.governor import GovernorResult
from signalforge.application.brain.service import BrainView, GuidanceView, HypothesisCard
from signalforge.domain.brain.confidence import ConfidenceEntry
from signalforge.domain.brain.grounding import GroundedAnswer
from signalforge.domain.brain.nodes import GraphEdge, GraphNode, SituationSnapshot
from signalforge.domain.brain.topology import TopologyView


# --- requests -----------------------------------------------------------------


class AskIn(BaseModel):
    question: str = Field(
        min_length=1,
        max_length=500,
        description=(
            "A question about this incident. The answer is drawn only from this "
            "incident's own graph; anything the graph does not support comes "
            "back as insufficient evidence rather than as a guess."
        ),
    )


# --- nodes --------------------------------------------------------------------


class ProvenanceOut(BaseModel):
    source: str
    actor_type: str
    authority: str = Field(
        description=(
            "authoritative, observed or inferred. Anything a model concluded is "
            "inferred; the brain correlates, it does not measure."
        )
    )
    model: str = ""
    model_class: str = ""
    degraded: bool = False
    result_ref: str = Field(
        default="",
        description="Where the underlying data can be read in full - the "
        "drill-down handle behind an excerpt.",
    )


class NodeOut(BaseModel):
    id: str
    node_type: str
    derived_key: str
    label: str
    content: str = ""
    version: int
    status: str
    seq: int
    created_at: datetime
    confidence: Optional[float] = None
    confirmation: Optional[str] = Field(
        default=None,
        description="confirmed (measured) or predicted (inferred from "
        "dependencies). Never conflated.",
    )
    untrusted: bool = Field(
        default=False,
        description="True when the content is text this platform did not write.",
    )
    supersedes: Optional[str] = None
    superseded_by: Optional[str] = None
    retired_reason: str = ""
    tags: list[str] = []
    provenance: ProvenanceOut
    detail: dict[str, Any] = {}


class EdgeOut(BaseModel):
    source_id: str
    target_id: str
    relation: str
    weight: float = 0.0
    note: str = ""


class ConfidenceEntryOut(BaseModel):
    at: datetime
    previous: Optional[float]
    current: float
    delta: float
    direction: str
    net_weight: float
    supporting: list[str] = []
    refuting: list[str] = []
    added: list[str] = []
    trigger: str = ""
    explain: str = Field(
        description="The deterministic explanation, always populated - written "
        "from the stored numbers, so it says the same thing with or without a "
        "model."
    )
    narrative: str = Field(
        default="",
        description="One sentence a model may write about why this moved. Never "
        "the source of a number.",
    )
    narrative_degraded: bool = False


class HypothesisOut(BaseModel):
    node: NodeOut
    confidence: float
    discriminating_test: str = Field(
        description="What would most move this next. The thing that turns a "
        "percentage into an action."
    )
    explain: str
    evidence_for: list[NodeOut] = []
    evidence_against: list[NodeOut] = []
    history: list[ConfidenceEntryOut] = []
    sparkline: list[float] = Field(
        default=[],
        description="Confidence over time, oldest first, for the card's chart.",
    )


class SnapshotOut(BaseModel):
    id: str
    taken_at: datetime
    reason: str
    fingerprint: str
    topology_version: str = ""
    node_count: int = 0
    hypotheses: dict[str, float] = {}
    degraded: bool = False


class AgentRunOut(BaseModel):
    id: str
    agent: str
    status: str
    trigger_topic: str = ""
    started_at: datetime
    finished_at: Optional[datetime] = None
    model: str = ""
    tokens: int = 0
    error: str = ""


class BrainOut(BaseModel):
    incident_id: str
    reference: str
    app_id: str
    degraded: bool = Field(
        description="The AI path is not working. Every deterministic path still "
        "is, and the ledger below was still built."
    )
    degraded_reason: str = ""
    model_configured: bool
    counts: dict[str, int] = {}
    hypotheses: list[HypothesisOut] = []
    nodes: list[NodeOut] = []
    edges: list[EdgeOut] = []
    snapshots: list[SnapshotOut] = []
    runs: list[AgentRunOut] = []


class NodeHistoryOut(BaseModel):
    derived_key: str
    versions: list[NodeOut] = []


# --- topology -----------------------------------------------------------------


class ImpactOut(BaseModel):
    service: str
    confirmation: str
    reason: str
    hops: int = 0
    likelihood: float = 1.0
    path: list[str] = []
    region: str = ""


class TopologyDeltaOut(BaseModel):
    from_version: str
    to_version: str
    summary: str
    added_nodes: list[str] = []
    removed_nodes: list[str] = []
    added_edges: list[list[str]] = []
    removed_edges: list[list[str]] = []
    changed_nodes: list[str] = []


class TopologyOut(BaseModel):
    app_id: str
    version: str
    versions: list[str] = []
    imported_at: str = ""
    node_count: int = 0
    edge_count: int = 0
    nodes: list[dict[str, str]] = []
    edges: list[dict[str, str]] = []
    delta: Optional[TopologyDeltaOut] = None
    confirmed: list[str] = Field(
        default=[], description="Measured. Something was observed on these."
    )
    predicted: list[str] = Field(
        default=[],
        description="Inferred from dependencies. Nothing has been measured on "
        "these at all.",
    )
    impacts: list[ImpactOut] = []
    detail: dict[str, str] = {}


# --- guidance and the copilot ---------------------------------------------------


class GuidanceOut(BaseModel):
    caveat: str
    similar: list[NodeOut] = []
    warnings: list[NodeOut] = []
    contradictions: list[NodeOut] = []
    injections: list[NodeOut] = Field(
        default=[],
        description="Content in this incident that tried to instruct the "
        "assistant. Recorded, never followed.",
    )
    degraded: bool = False


class AnswerOut(BaseModel):
    question: str
    answer: str
    verdict: str = Field(
        description="grounded, insufficient_evidence, ungrounded or degraded."
    )
    grounded: bool
    citations: list[str] = []
    caveats: list[str] = []
    considered: int = Field(
        default=0, description="How many graph nodes were offered to the model."
    )
    injection_observed: bool = False
    degraded: bool = False
    degraded_reason: str = ""
    model: str = ""


class RefreshOut(BaseModel):
    incident_id: str
    ran: list[str] = []
    nodes_written: int = 0
    versions_written: int = 0
    edges_written: int = 0
    moved: list[str] = []
    hypotheses: dict[str, float] = {}
    degraded: bool = False
    degraded_reasons: list[str] = []
    queries: int = 0
    tokens: int = 0
    snapshot_id: str = ""
    skipped: str = ""


# --- mapping ------------------------------------------------------------------


def node_out(node: GraphNode) -> NodeOut:
    return NodeOut(
        id=node.id,
        node_type=node.node_type.value,
        derived_key=node.derived_key,
        label=node.label,
        content=node.content,
        version=node.version,
        status=node.status.value,
        seq=node.seq,
        created_at=node.created_at,
        confidence=node.confidence,
        confirmation=node.confirmation.value if node.confirmation else None,
        untrusted=node.untrusted,
        supersedes=node.supersedes,
        superseded_by=node.superseded_by,
        retired_reason=node.retired_reason,
        tags=list(node.tags),
        provenance=ProvenanceOut(
            source=node.provenance.source,
            actor_type=node.provenance.actor_type.value,
            authority=node.provenance.authority.value,
            model=node.provenance.model,
            model_class=node.provenance.model_class,
            degraded=node.provenance.degraded,
            result_ref=node.provenance.result_ref,
        ),
        detail=dict(node.detail),
    )


def entry_out(entry: ConfidenceEntry) -> ConfidenceEntryOut:
    return ConfidenceEntryOut(
        at=entry.at,
        previous=entry.previous,
        current=entry.current,
        delta=round(entry.delta, 4),
        direction=entry.direction,
        net_weight=round(entry.net_weight, 4),
        supporting=list(entry.supporting),
        refuting=list(entry.refuting),
        added=list(entry.added),
        trigger=entry.trigger,
        explain=entry.explain(),
        narrative=entry.narrative,
        narrative_degraded=entry.narrative_degraded,
    )


def hypothesis_out(card: HypothesisCard) -> HypothesisOut:
    return HypothesisOut(
        node=node_out(card.node),
        confidence=card.confidence,
        discriminating_test=card.discriminating_test,
        explain=card.explain,
        evidence_for=[node_out(node) for node in card.evidence_for],
        evidence_against=[node_out(node) for node in card.evidence_against],
        history=[entry_out(entry) for entry in card.history],
        sparkline=[round(entry.current, 4) for entry in card.history],
    )


def snapshot_out(snapshot: SituationSnapshot) -> SnapshotOut:
    return SnapshotOut(
        id=snapshot.id,
        taken_at=snapshot.taken_at,
        reason=snapshot.reason,
        fingerprint=snapshot.fingerprint,
        topology_version=snapshot.topology_version,
        node_count=len(snapshot.node_ids),
        hypotheses=dict(snapshot.hypotheses),
        degraded=snapshot.degraded,
    )


def run_out(run: dict) -> AgentRunOut:
    return AgentRunOut(
        id=str(run.get("id")),
        agent=str(run.get("agent")),
        status=str(run.get("status")),
        trigger_topic=str(run.get("trigger_topic") or ""),
        started_at=run["started_at"],
        finished_at=run.get("finished_at"),
        model=str(run.get("model") or ""),
        tokens=int(run.get("prompt_tokens") or 0)
        + int(run.get("completion_tokens") or 0),
        error=str(run.get("error") or ""),
    )


def edge_out(edge: GraphEdge) -> EdgeOut:
    return EdgeOut(
        source_id=edge.source_id,
        target_id=edge.target_id,
        relation=edge.relation.value,
        weight=edge.weight,
        note=edge.note,
    )


def brain_out(view: BrainView) -> BrainOut:
    return BrainOut(
        incident_id=view.incident.id,
        reference=view.incident.reference,
        app_id=view.incident.app_id,
        degraded=view.degraded,
        degraded_reason=view.degraded_reason,
        model_configured=view.model_configured,
        counts=dict(view.counts),
        hypotheses=[hypothesis_out(card) for card in view.hypotheses],
        nodes=[node_out(node) for node in view.nodes],
        edges=[edge_out(edge) for edge in view.edges],
        snapshots=[snapshot_out(snapshot) for snapshot in view.snapshots],
        runs=[run_out(dict(run)) for run in view.runs],
    )


def guidance_out(view: GuidanceView) -> GuidanceOut:
    return GuidanceOut(
        caveat=view.caveat,
        similar=[node_out(node) for node in view.similar],
        warnings=[node_out(node) for node in view.warnings],
        contradictions=[node_out(node) for node in view.contradictions],
        injections=[node_out(node) for node in view.injections],
        degraded=view.degraded,
    )


def topology_out(view: TopologyView) -> TopologyOut:
    return TopologyOut(
        app_id=view.app_id,
        version=view.version,
        versions=list(view.versions),
        imported_at=view.imported_at,
        node_count=view.node_count,
        edge_count=view.edge_count,
        nodes=[dict(node) for node in view.nodes],
        edges=[dict(edge) for edge in view.edges],
        delta=(
            TopologyDeltaOut(
                from_version=view.delta.from_version,
                to_version=view.delta.to_version,
                summary=view.delta.summary(),
                added_nodes=list(view.delta.added_nodes),
                removed_nodes=list(view.delta.removed_nodes),
                added_edges=[list(pair) for pair in view.delta.added_edges],
                removed_edges=[list(pair) for pair in view.delta.removed_edges],
                changed_nodes=list(view.delta.changed_nodes),
            )
            if view.delta is not None
            else None
        ),
        confirmed=list(view.confirmed),
        predicted=list(view.predicted),
        impacts=[
            ImpactOut(
                service=impact.service,
                confirmation=impact.state.value,
                reason=impact.reason,
                hops=impact.hops,
                likelihood=impact.likelihood,
                path=list(impact.path),
                region=impact.region,
            )
            for impact in view.impacts
        ],
        detail=dict(view.detail),
    )


def answer_out(answer: GroundedAnswer) -> AnswerOut:
    return AnswerOut(
        question=answer.question,
        answer=answer.answer,
        verdict=answer.verdict.value,
        grounded=answer.grounded,
        citations=list(answer.citations),
        caveats=list(answer.caveats),
        considered=len(answer.considered),
        injection_observed=answer.injection_observed,
        degraded=answer.degraded,
        degraded_reason=answer.degraded_reason,
        model=answer.model,
    )


def refresh_out(result: GovernorResult) -> RefreshOut:
    return RefreshOut(
        incident_id=result.incident_id,
        ran=list(result.ran),
        nodes_written=result.nodes_written,
        versions_written=result.versions_written,
        edges_written=result.edges_written,
        moved=list(result.moved),
        hypotheses=dict(result.hypotheses),
        degraded=result.degraded,
        degraded_reasons=list(result.degraded_reasons),
        queries=result.queries,
        tokens=result.tokens,
        snapshot_id=result.snapshot_id,
        skipped=result.skipped,
    )


def history_out(derived_key: str, versions) -> NodeHistoryOut:
    return NodeHistoryOut(
        derived_key=derived_key,
        versions=[node_out(node) for node in versions],
    )
