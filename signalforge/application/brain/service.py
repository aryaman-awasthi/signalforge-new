"""Reading the Incident Brain, and asking it questions (REQ-050, REQ-055).

The read side of the brain. Everything here is tenant-scoped through the
repositories exactly as every other service is, and none of it carries a
permission - on the same reasoning `GET /incidents/{id}/war-room` carries none
(DR-071): `ROLE_PERMISSIONS[Role.SME]` is empty, and the SME is precisely the
person a war room exists to reach. A brain the paged expert cannot open is a
brain that has failed at its only job.

`ask` is the copilot, and it is a *read* that happens to involve a model. It
writes no graph node, changes no incident state, and holds no tool. The one
thing it writes is an audit entry and a timeline event recording that a question
was asked and how it was answered - because "the assistant told me the pool was
fine" is a sentence somebody will say at a post-incident review, and the record
has to be able to settle it.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Callable, Mapping, Optional, Sequence

from signalforge.application.brain.agents.copilot import CopilotAgent
from signalforge.application.brain.agents.telemetry import confirmed_services
from signalforge.application.brain.common import COPILOT_ANSWERED_TOPIC, publish_agent
from signalforge.application.identity.principal import Principal
from signalforge.application.identity.uow import IdentityUnitOfWork
from signalforge.domain.brain.confidence import ConfidenceEntry
from signalforge.domain.brain.errors import BrainNotFoundError
from signalforge.domain.brain.grounding import GroundedAnswer
from signalforge.domain.brain.nodes import (
    GraphEdge,
    GraphNode,
    NodeType,
    Relation,
    SituationSnapshot,
)
from signalforge.domain.brain.similarity import IncidentFingerprint
from signalforge.domain.brain.topology import (
    ServiceImpact,
    TopologyDelta,
    TopologyView,
    diff,
    propagate,
)
from signalforge.domain.identity.ports import Clock
from signalforge.domain.incidents.entities import Incident
from signalforge.domain.llm.ports import LlmProvider

logger = logging.getLogger("signalforge.brain")


@dataclass(frozen=True)
class HypothesisCard:
    """One hypothesis, with everything S10 renders on its card.

    `evidence_for` and `evidence_against` are separate tuples rather than one
    signed list, because "what argues against this" is the half an incident
    commander reads second and acts on first.
    """

    node: GraphNode
    evidence_for: tuple[GraphNode, ...] = ()
    evidence_against: tuple[GraphNode, ...] = ()
    history: tuple[ConfidenceEntry, ...] = ()

    @property
    def confidence(self) -> float:
        return self.node.confidence or 0.0

    @property
    def discriminating_test(self) -> str:
        return str(self.node.detail.get("discriminating_test") or "")

    @property
    def latest(self) -> Optional[ConfidenceEntry]:
        return self.history[-1] if self.history else None

    @property
    def explain(self) -> str:
        """Why this number is what it is. Always populated: the deterministic
        sentence, with the model's narrative beside it when there is one."""
        entry = self.latest
        if entry is None:
            return str(self.node.detail.get("explain") or "")
        return entry.explain()


@dataclass(frozen=True)
class BrainView:
    """Everything S10 needs, read in one pass."""

    incident: Incident
    nodes: tuple[GraphNode, ...] = ()
    edges: tuple[GraphEdge, ...] = ()
    hypotheses: tuple[HypothesisCard, ...] = ()
    counts: Mapping[str, int] = field(default_factory=dict)
    snapshots: tuple[SituationSnapshot, ...] = ()
    runs: tuple[Mapping[str, object], ...] = ()
    degraded: bool = False
    degraded_reason: str = ""
    model_configured: bool = True

    def of_type(self, node_type: NodeType) -> tuple[GraphNode, ...]:
        return tuple(node for node in self.nodes if node.node_type is node_type)


@dataclass(frozen=True)
class GuidanceView:
    """S14. Historical similarity and pinned mistake warnings, both of which are
    advisory and both of which say so on every row."""

    similar: tuple[GraphNode, ...] = ()
    warnings: tuple[GraphNode, ...] = ()
    contradictions: tuple[GraphNode, ...] = ()
    injections: tuple[GraphNode, ...] = ()
    degraded: bool = False

    @property
    def caveat(self) -> str:
        return (
            "Everything on this panel is guidance drawn from prior incidents and "
            "from what people said. None of it has been measured on the system "
            "that is broken now."
        )


class IncidentBrainService:
    def __init__(
        self,
        uow_factory: Callable[[], IdentityUnitOfWork],
        bus,
        clock: Clock,
        llm: LlmProvider,
        *,
        governor=None,
        copilot: Optional[CopilotAgent] = None,
        metrics=None,
    ):
        self._uow_factory = uow_factory
        self._bus = bus
        self._clock = clock
        self._llm = llm
        self._governor = governor
        self._copilot = copilot or CopilotAgent(llm)
        self._metrics = metrics

    # -- reads ----------------------------------------------------------------

    async def view(self, principal: Principal, incident_id: str) -> BrainView:
        tenant_id = principal.tenant_id
        async with self._uow_factory() as uow:
            incident = await uow.incidents.get(tenant_id, incident_id)
            if incident is None:
                raise BrainNotFoundError(f"incident {incident_id} not found")
            nodes = tuple(
                await uow.incident_graph.list(tenant_id, incident_id, limit=1_000)
            )
            edges = tuple(await uow.incident_graph.edges(tenant_id, incident_id))
            counts = await uow.incident_graph.count_by_type(tenant_id, incident_id)
            snapshots = tuple(
                await uow.situation_snapshots.list(tenant_id, incident_id, limit=20)
            )
            runs = tuple(
                await uow.agent_runs.list(tenant_id, incident_id=incident_id, limit=25)
            )
            cards = []
            by_id = {node.id: node for node in nodes}
            for node in nodes:
                if node.node_type is not NodeType.HYPOTHESIS:
                    continue
                history = tuple(
                    await uow.confidence_history.list(
                        tenant_id, incident_id, hypothesis_key=node.derived_key
                    )
                )
                supports = tuple(
                    by_id[edge.source_id]
                    for edge in edges
                    if edge.target_id == node.id
                    and edge.relation is Relation.SUPPORTS
                    and edge.source_id in by_id
                )
                refutes = tuple(
                    by_id[edge.source_id]
                    for edge in edges
                    if edge.target_id == node.id
                    and edge.relation is Relation.REFUTES
                    and edge.source_id in by_id
                )
                cards.append(
                    HypothesisCard(
                        node=node,
                        evidence_for=supports,
                        evidence_against=refutes,
                        history=history,
                    )
                )

        cards.sort(key=lambda card: (-card.confidence, card.node.label))
        degraded, reason = self._degraded_state(runs)
        return BrainView(
            incident=incident,
            nodes=nodes,
            edges=edges,
            hypotheses=tuple(cards),
            counts=counts,
            snapshots=snapshots,
            runs=runs,
            degraded=degraded,
            degraded_reason=reason,
            model_configured=self._llm.configured,
        )

    async def history(
        self, principal: Principal, incident_id: str, derived_key: str
    ) -> tuple[GraphNode, ...]:
        """Every version of one belief. What the version-history icon opens."""
        async with self._uow_factory() as uow:
            return tuple(
                await uow.incident_graph.history(
                    principal.tenant_id, incident_id, derived_key
                )
            )

    async def guidance(self, principal: Principal, incident_id: str) -> GuidanceView:
        tenant_id = principal.tenant_id
        async with self._uow_factory() as uow:
            nodes = list(
                await uow.incident_graph.list(tenant_id, incident_id, limit=1_000)
            )
            runs = list(
                await uow.agent_runs.list(tenant_id, incident_id=incident_id, limit=25)
            )
        return GuidanceView(
            similar=tuple(
                node
                for node in nodes
                if node.node_type is NodeType.EVIDENCE and "history" in node.tags
            ),
            warnings=tuple(
                node for node in nodes if "mistake-warning" in node.tags
            ),
            contradictions=tuple(
                node for node in nodes if "contradiction" in node.tags
            ),
            injections=tuple(
                node for node in nodes if "prompt-injection" in node.tags
            ),
            degraded=self._degraded_state(runs)[0],
        )

    async def topology(
        self, principal: Principal, incident_id: str, *, version: Optional[str] = None
    ) -> TopologyView:
        """S13: the map, its versions, what changed, and the two impact lists.

        The impacts are read from the graph rather than recomputed, so the screen
        shows what the topology agent actually concluded at the last pass -
        including its CONFIRMED/PREDICTED split - rather than a second opinion
        computed on the read path that could disagree with the ledger.
        """
        tenant_id = principal.tenant_id
        async with self._uow_factory() as uow:
            incident = await uow.incidents.get(tenant_id, incident_id)
            if incident is None:
                raise BrainNotFoundError(f"incident {incident_id} not found")
            versions = list(
                await uow.topologies.list(tenant_id, incident.app_id, limit=20)
            )
            nodes = list(
                await uow.incident_graph.list(
                    tenant_id, incident_id, types=[NodeType.IMPACT], limit=200
                )
            )

        if not versions:
            return TopologyView(
                app_id=incident.app_id,
                version="",
                detail={
                    "reason": (
                        "No dependency map is imported for this application, so "
                        "nothing here says what is downstream of the fault."
                    )
                },
            )

        chosen = next(
            (item for item in versions if item.version == version), versions[0]
        )
        previous = None
        for candidate in versions:
            if candidate.version != chosen.version:
                previous = candidate
                break
        delta: Optional[TopologyDelta] = None
        if previous is not None:
            delta = diff(
                previous.snapshot,
                chosen.snapshot,
                from_version=previous.version,
                to_version=chosen.version,
            )

        impacts = tuple(
            ServiceImpact(
                service=str(node.detail.get("subject") or node.label),
                state=node.confirmation,
                reason=node.content,
                hops=int(node.detail.get("hops") or 0),
                likelihood=float(node.detail.get("likelihood") or 1.0),
                path=tuple(node.detail.get("path") or ()),
                region=str(node.detail.get("region") or ""),
            )
            for node in nodes
            if node.confirmation is not None
        )

        return TopologyView(
            app_id=incident.app_id,
            version=chosen.version,
            imported_at=chosen.imported_at.isoformat(),
            node_count=chosen.node_count,
            edge_count=chosen.edge_count,
            versions=tuple(item.version for item in versions),
            delta=delta,
            impacts=impacts,
            nodes=tuple(
                {
                    "id": node.node_id,
                    "name": node.name or node.node_id,
                    "type": node.node_type.value,
                    "region": node.region or "",
                }
                for node in chosen.snapshot.nodes[:200]
            ),
            edges=tuple(
                {
                    "source": edge.source,
                    "target": edge.target,
                    "type": edge.edge_type.value,
                }
                for edge in chosen.snapshot.edges[:400]
            ),
        )

    # -- the copilot ----------------------------------------------------------

    async def ask(
        self, principal: Principal, incident_id: str, question: str
    ) -> GroundedAnswer:
        """One grounded question about one incident.

        Self-scoped in the way that matters: the incident is read through a
        tenant-scoped repository, and the only material the answer can draw on
        is that incident's own graph. There is no parameter by which a caller
        could point this at another tenant's evidence, which is a stronger
        control than a permission would be.
        """
        tenant_id = principal.tenant_id
        async with self._uow_factory() as uow:
            incident = await uow.incidents.get(tenant_id, incident_id)
            if incident is None:
                raise BrainNotFoundError(f"incident {incident_id} not found")
            nodes = list(
                await uow.incident_graph.list(tenant_id, incident_id, limit=1_000)
            )

        answer = await self._copilot.answer(
            question,
            tenant_id=tenant_id,
            incident_id=incident_id,
            nodes=nodes,
        )

        async with self._uow_factory() as uow:
            await publish_agent(
                uow,
                self._bus,
                self._clock,
                tenant_id=tenant_id,
                app_id=incident.app_id,
                incident_id=incident_id,
                topic=COPILOT_ANSWERED_TOPIC,
                payload={
                    "incident_id": incident_id,
                    "asked_by": principal.user_id,
                    # The question is recorded; the answer is not. The answer is
                    # reconstructible from the citations and the graph, and a
                    # timeline that carried every generated paragraph would be a
                    # second copy of the incident with a different retention.
                    "question": answer.question,
                    "verdict": answer.verdict.value,
                    "citations": list(answer.citations),
                    "considered": len(answer.considered),
                    "degraded": answer.degraded,
                    "model": answer.model,
                    "injection_observed": answer.injection_observed,
                },
                agent=self._copilot.name,
            )
            await uow.commit()

        if self._metrics is not None:
            self._metrics.increment(
                "signalforge_copilot_answers_total",
                {"verdict": answer.verdict.value},
            )
        return answer

    # -- refresh --------------------------------------------------------------

    async def refresh(self, principal: Principal, incident_id: str):
        """Run a governor pass now, on request.

        Budgeted like every other pass, and idempotent: a pass against an
        unchanged world writes nothing and produces no new versions, so a person
        pressing the button twice does not fill the ledger.
        """
        if self._governor is None:
            raise BrainNotFoundError("no incident governor is wired on this node")
        return await self._governor.run_for(
            principal.tenant_id, incident_id, trigger="manual"
        )

    # -- internals ------------------------------------------------------------

    def _degraded_state(
        self, runs: Sequence[Mapping[str, object]]
    ) -> tuple[bool, str]:
        """Whether the AI path is degraded, from the last pass's own runs.

        Read from the trace rather than from the provider's configuration,
        because the two answer different questions: the provider says whether a
        key exists, and the trace says whether the last thing this incident
        actually tried worked. An operator watching the badge cares about the
        second.
        """
        if not self._llm.configured:
            return True, "no approved, credentialled model gateway is configured"
        for run in runs:
            if str(run.get("status")) in ("degraded", "failed"):
                return True, f"{run.get('agent')} last ran {run.get('status')}"
        return False, ""


def fingerprint_summary(fingerprint: IncidentFingerprint) -> str:
    return f"{fingerprint.reference}: {fingerprint.title}"


def confirmed_from(context) -> dict[str, str]:
    return confirmed_services(context)


def recompute_impacts(snapshot, confirmed: Mapping[str, str]) -> tuple[ServiceImpact, ...]:
    return propagate(snapshot, confirmed=dict(confirmed))
