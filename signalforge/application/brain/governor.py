"""The Incident Governor: one orchestrator, one shared state, one budget
(REQ-052).

Eight capabilities, not eight services. Every agent shares this object's incident
state, evidence gathering, model provider, token budget, provenance stamping,
execution trace and degradation handling, because the alternative - each agent
solving those itself - is how a platform ends up with eight retry policies, three
budget notions and a provenance field that means something different per writer.

**The pass is two-phase and the ordering is load-bearing.** Phase one runs the
agents that read the world - telemetry, topology, change, conversation, history -
and merges their proposals into the graph, where each node gets a real id. Phase
two runs the hypothesis agent against the *written* graph, so a hypothesis is
linked to the exact node versions that argued for and against it. Running them
together would link hypotheses to nodes that do not have ids yet, and the answer
to "why did #2 move from 65% to 32%?" would be prose instead of rows.

**A pass never raises.** Every agent is run inside a guard: an agent that throws
is recorded as failed on its own run row and the pass continues. An incident
whose brain could take the incident down with it would be worse than no brain.

**Waking is decoupled from running.** The bus consumer marks an incident as
having something to think about; a scheduled job drains that set. Running agents
inside the dispatcher's transaction would put a second writer on the same SQLite
connection the dispatcher is holding, and a governor pass is seconds of model
latency inside a transaction that should be milliseconds.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Mapping, Optional, Sequence

from signalforge.application.brain.agents.base import (
    Agent,
    AgentOutcome,
    EdgeProposal,
    NodeProposal,
)
from signalforge.application.brain.agents.change import ChangeIntelligenceAgent
from signalforge.application.brain.agents.conversation import ConversationAgent
from signalforge.application.brain.agents.historical import HistoricalLearningAgent
from signalforge.application.brain.agents.hypothesis import HypothesisAgent
from signalforge.application.brain.agents.telemetry import TelemetryAgent
from signalforge.application.brain.agents.topology import TopologyAgent
from signalforge.application.brain.common import BRAIN_UPDATED_TOPIC, publish_agent
from signalforge.application.brain.evidence import (
    IncidentContext,
    ProgressiveEvidenceService,
)
from signalforge.application.events.bus import EventBus
from signalforge.application.identity.uow import IdentityUnitOfWork
from signalforge.domain.brain.confidence import ConfidenceEntry
from signalforge.domain.brain.evidence import QueryBudget
from signalforge.domain.brain.nodes import (
    GraphEdge,
    GraphNode,
    NodeType,
    SituationSnapshot,
)
from signalforge.domain.identity.ports import Clock
from signalforge.domain.incidents.entities import Incident
from signalforge.domain.llm.ports import LlmProvider

logger = logging.getLogger("signalforge.brain")

BRAIN_GROUP = "incident-brain"
BRAIN_TRIGGER_PATTERNS: tuple[str, ...] = (
    "incident.*",
    "warroom.*",
    "detection.*",
    "deployment.*",
)
"""What is worth thinking about. Deliberately not `brain.*`: an agent reacting to
its own output is a loop, and the one thing a budgeted reasoning system must not
be able to do is feed itself."""

MAX_PENDING = 200


@dataclass
class GovernorResult:
    """What one pass did, in the fields the cockpit and an auditor both read."""

    incident_id: str
    ran: tuple[str, ...] = ()
    nodes_written: int = 0
    versions_written: int = 0
    edges_written: int = 0
    hypotheses: Mapping[str, float] = field(default_factory=dict)
    moved: tuple[str, ...] = ()
    degraded: bool = False
    degraded_reasons: tuple[str, ...] = ()
    failures: tuple[str, ...] = ()
    queries: int = 0
    tokens: int = 0
    snapshot_id: str = ""
    notes: tuple[str, ...] = ()
    skipped: str = ""

    @property
    def changed(self) -> bool:
        return bool(self.nodes_written or self.versions_written or self.edges_written)


class IncidentGovernor:
    def __init__(
        self,
        uow_factory: Callable[[], IdentityUnitOfWork],
        bus: EventBus,
        clock: Clock,
        llm: LlmProvider,
        evidence: ProgressiveEvidenceService,
        *,
        agents: Optional[Sequence[Agent]] = None,
        hypothesis: Optional[HypothesisAgent] = None,
        budget: Optional[QueryBudget] = None,
        metrics=None,
    ):
        self._uow_factory = uow_factory
        self._bus = bus
        self._clock = clock
        self._llm = llm
        self._evidence = evidence
        self._budget = budget or QueryBudget()
        self._metrics = metrics
        self._agents: tuple[Agent, ...] = tuple(
            agents
            if agents is not None
            else (
                TelemetryAgent(),
                TopologyAgent(),
                ChangeIntelligenceAgent(),
                ConversationAgent(llm),
                HistoricalLearningAgent(llm),
            )
        )
        self._hypothesis = hypothesis or HypothesisAgent(llm)
        self._pending: dict[tuple[str, str], str] = {}

    # -- waking ---------------------------------------------------------------

    def wake(self, tenant_id: str, incident_id: str, trigger: str) -> None:
        """Something happened that this incident should think about.

        Records, does not run. Called from the bus consumer inside the
        dispatcher's transaction, so it must be cheap and must not touch the
        database: a governor pass opening a second connection while the
        dispatcher holds a write transaction is a SQLite deadlock waiting for a
        busy night.
        """
        if len(self._pending) >= MAX_PENDING:
            # A backlog this size means the drain job is not running. Dropping
            # the newest is right: every incident already in the set will be
            # re-evaluated against current state anyway, so nothing is lost
            # except a duplicate wake.
            return
        self._pending[(tenant_id, incident_id)] = trigger

    @property
    def pending(self) -> int:
        return len(self._pending)

    async def drain(self, limit: int = 10) -> list[GovernorResult]:
        """Run a pass for each woken incident. One job per install."""
        taken = list(self._pending.items())[:limit]
        for key, _trigger in taken:
            self._pending.pop(key, None)
        results: list[GovernorResult] = []
        for (tenant_id, incident_id), trigger in taken:
            try:
                results.append(
                    await self.run_for(tenant_id, incident_id, trigger=trigger)
                )
            except Exception as exc:  # pragma: no cover - defensive by design
                logger.warning(
                    "an incident brain pass failed",
                    extra={
                        "incident_id": incident_id,
                        "error": f"{type(exc).__name__}: {exc}",
                    },
                )
        return results

    # -- the pass -------------------------------------------------------------

    async def run_for(
        self, tenant_id: str, incident_id: str, *, trigger: str = "manual"
    ) -> GovernorResult:
        """One complete reasoning pass over one incident."""
        async with self._uow_factory() as uow:
            incident = await uow.incidents.get(tenant_id, incident_id)
        if incident is None:
            return GovernorResult(incident_id=incident_id, skipped="no such incident")
        if not incident.open:
            # A closed incident's brain is a record, not a work item. Continuing
            # to reason about it would spend budget rewriting history.
            return GovernorResult(
                incident_id=incident_id, skipped="the incident is closed"
            )

        context = await self._evidence.gather(tenant_id, incident, budget=self._budget)
        result = GovernorResult(incident_id=incident_id)

        # -- phase one: read the world ---------------------------------------
        outcomes: list[tuple[Agent, AgentOutcome]] = []
        for agent in self._agents:
            if not agent.wants(trigger):
                continue
            outcome = await self._guarded(agent, context, trigger)
            outcomes.append((agent, outcome))

        written, versions, edges = await self._merge(context, outcomes)
        result.nodes_written = written
        result.versions_written = versions
        result.edges_written = edges

        # -- phase two: reason over what was written --------------------------
        async with self._uow_factory() as uow:
            nodes = list(
                await uow.incident_graph.list(tenant_id, incident_id, limit=1_000)
            )
            previous: dict[str, Optional[float]] = {}
            previous_ids: dict[str, Sequence[str]] = {}
            for node in nodes:
                if node.node_type is not NodeType.HYPOTHESIS:
                    continue
                latest = await uow.confidence_history.latest(
                    tenant_id, incident_id, node.derived_key
                )
                previous[node.derived_key] = latest.current if latest else None
                previous_ids[node.derived_key] = (
                    tuple(latest.supporting) + tuple(latest.refuting) if latest else ()
                )

        run_id = str(uuid.uuid4())
        started = self._clock.now()
        try:
            outcome, entries = await self._hypothesis.reason(
                context,
                nodes,
                previous=previous,
                previous_ids=previous_ids,
                trigger=trigger,
            )
        except Exception as exc:  # pragma: no cover - defensive by design
            logger.warning(
                "hypothesis reasoning failed",
                extra={
                    "incident_id": incident_id,
                    "error": f"{type(exc).__name__}: {exc}",
                },
            )
            outcome, entries = AgentOutcome(skipped=f"{type(exc).__name__}"), []
            result.failures = result.failures + (self._hypothesis.name,)

        await self._record_run(
            run_id,
            context,
            self._hypothesis.name,
            outcome,
            trigger=trigger,
            started=started,
        )
        outcomes.append((self._hypothesis, outcome))
        hyp_written, hyp_versions, hyp_edges = await self._merge(
            context, [(self._hypothesis, outcome)]
        )
        result.nodes_written += hyp_written
        result.versions_written += hyp_versions
        result.edges_written += hyp_edges

        # -- history, snapshot, events ----------------------------------------
        moved: list[str] = []
        async with self._uow_factory() as uow:
            for entry in entries:
                latest = await uow.confidence_history.latest(
                    tenant_id, incident_id, entry.hypothesis_key
                )
                if latest is not None and _same(latest, entry):
                    # Nothing moved and nothing new is attached. A history row
                    # per pass would bury the movements in heartbeats.
                    continue
                await uow.confidence_history.append(tenant_id, incident_id, entry)
                moved.append(entry.hypothesis_key)
            await uow.commit()
        result.moved = tuple(moved)

        result.ran = tuple(agent.name for agent, _ in outcomes)
        result.hypotheses = {
            node.derived_key: node.confidence or 0.0
            for node in await self._current_hypotheses(tenant_id, incident_id)
        }
        result.degraded = any(outcome.degraded for _agent, outcome in outcomes)
        result.degraded_reasons = tuple(
            sorted(
                {
                    outcome.degraded_reason
                    for _agent, outcome in outcomes
                    if outcome.degraded and outcome.degraded_reason
                }
            )
        )
        result.notes = tuple(
            note for _agent, outcome in outcomes for note in outcome.notes
        )
        result.queries = context.ledger.spent
        result.tokens = sum(
            outcome.usage.total for _agent, outcome in outcomes if outcome.usage
        )

        result.snapshot_id = await self._snapshot(context, result, trigger=trigger)
        await self._publish(context, result, outcomes)
        if self._metrics is not None:
            self._metrics.increment(
                "signalforge_brain_passes_total",
                {"degraded": "true" if result.degraded else "false"},
            )
        return result

    # -- running one agent ----------------------------------------------------

    async def _guarded(
        self, agent: Agent, context: IncidentContext, trigger: str
    ) -> AgentOutcome:
        run_id = str(uuid.uuid4())
        started = self._clock.now()
        try:
            outcome = await agent.run(context)
        except Exception as exc:
            logger.warning(
                "an incident-brain agent failed",
                extra={
                    "agent": agent.name,
                    "incident_id": context.incident_id,
                    "error": f"{type(exc).__name__}: {exc}",
                },
            )
            outcome = AgentOutcome(skipped=f"{type(exc).__name__}: {exc}"[:200])
            await self._record_run(
                run_id,
                context,
                agent.name,
                outcome,
                trigger=trigger,
                started=started,
                status="failed",
                error=f"{type(exc).__name__}: {exc}",
            )
            return outcome
        await self._record_run(
            run_id, context, agent.name, outcome, trigger=trigger, started=started
        )
        return outcome

    async def _record_run(
        self,
        run_id: str,
        context: IncidentContext,
        agent: str,
        outcome: AgentOutcome,
        *,
        trigger: str,
        started: datetime,
        status: str = "",
        error: str = "",
    ) -> None:
        """The execution trace (CTL-AUD-04).

        Deliberately free of prompts and answers: this row says which agent ran,
        on what, with which model, how much it cost and whether it degraded.
        Everything it *concluded* is a graph node with its own provenance, which
        is where an auditor reads content.
        """
        finished = self._clock.now()
        resolved = status or ("degraded" if outcome.degraded else "ok")
        async with self._uow_factory() as uow:
            await uow.agent_runs.start(
                run_id=run_id,
                tenant_id=context.tenant_id,
                agent=agent,
                incident_id=context.incident_id,
                trigger_topic=trigger,
                trigger_event_id=None,
                started_at=started,
            )
            await uow.agent_runs.step(
                run_id=run_id,
                seq=1,
                step="run",
                status=resolved,
                started_at=started,
                finished_at=finished,
                tokens=outcome.usage.total if outcome.usage else 0,
                detail={
                    "nodes": len(outcome.nodes),
                    "edges": len(outcome.edges),
                    "events": len(outcome.events),
                    "skipped": outcome.skipped,
                    "degraded_reason": outcome.degraded_reason,
                },
            )
            await uow.agent_runs.finish(
                run_id=run_id,
                status=resolved,
                finished_at=finished,
                model=outcome.model,
                prompt_tokens=outcome.usage.prompt if outcome.usage else 0,
                completion_tokens=outcome.usage.completion if outcome.usage else 0,
                error=error,
            )
            await uow.commit()
        if self._metrics is not None:
            self._metrics.increment(
                "signalforge_agent_runs_total",
                {"agent": agent, "outcome": resolved},
            )

    # -- merging --------------------------------------------------------------

    async def _merge(
        self,
        context: IncidentContext,
        outcomes: Sequence[tuple[Agent, AgentOutcome]],
    ) -> tuple[int, int, int]:
        """Write every proposal in one transaction.

        The versioning rule lives in the repository (`upsert`), so this is only
        the loop and the edge resolution. An edge names its endpoints by
        `derived_key`; a key the pass did not write is looked up as a node id,
        which is how the hypothesis agent links to evidence written in phase
        one without having to know the keys it was written under.
        """
        proposals: list[NodeProposal] = []
        edges: list[EdgeProposal] = []
        for _agent, outcome in outcomes:
            proposals.extend(outcome.nodes)
            edges.extend(outcome.edges)
        if not proposals and not edges:
            return 0, 0, 0

        created = versioned = drawn = 0
        now = context.now
        async with self._uow_factory() as uow:
            index: dict[str, str] = {}
            for proposal in proposals:
                node = GraphNode(
                    id=str(uuid.uuid4()),
                    tenant_id=context.tenant_id,
                    incident_id=context.incident_id,
                    node_type=proposal.node_type,
                    derived_key=proposal.derived_key,
                    label=proposal.label,
                    created_at=now,
                    provenance=proposal.provenance,
                    content=proposal.content,
                    untrusted=proposal.untrusted,
                    confidence=proposal.confidence,
                    confirmation=proposal.confirmation,
                    tags=proposal.tags,
                    detail=proposal.detail,
                )
                stored, changed = await uow.incident_graph.upsert(node, at=now)
                index[proposal.derived_key] = stored.id
                if changed:
                    if stored.version == 1:
                        created += 1
                    else:
                        versioned += 1

            for edge in edges:
                source = index.get(edge.source_key) or edge.source_key
                target = index.get(edge.target_key) or edge.target_key
                if source == target:
                    continue
                await uow.incident_graph.add_edge(
                    GraphEdge(
                        id=str(uuid.uuid4()),
                        tenant_id=context.tenant_id,
                        incident_id=context.incident_id,
                        source_id=source,
                        target_id=target,
                        relation=edge.relation,
                        created_at=now,
                        weight=edge.weight,
                        note=edge.note,
                        detail=dict(edge.detail),
                    )
                )
                drawn += 1
            await uow.commit()
        return created, versioned, drawn

    # -- snapshot and events --------------------------------------------------

    async def _current_hypotheses(
        self, tenant_id: str, incident_id: str
    ) -> Sequence[GraphNode]:
        async with self._uow_factory() as uow:
            return list(
                await uow.incident_graph.list(
                    tenant_id, incident_id, types=[NodeType.HYPOTHESIS]
                )
            )

    async def _snapshot(
        self, context: IncidentContext, result: GovernorResult, *, trigger: str
    ) -> str:
        """A replayable point in the incident's reasoning.

        Taken when the pass changed something. A snapshot per unchanged pass
        would make the list a heartbeat, and the fingerprint would say the same
        thing every time.
        """
        if not result.changed and not result.moved:
            return ""
        async with self._uow_factory() as uow:
            nodes = list(
                await uow.incident_graph.list(
                    context.tenant_id, context.incident_id, limit=1_000
                )
            )
            snapshot = SituationSnapshot(
                id=str(uuid.uuid4()),
                tenant_id=context.tenant_id,
                incident_id=context.incident_id,
                taken_at=context.now,
                reason=f"governor pass after {trigger}"[:200],
                node_ids=tuple(node.id for node in nodes),
                hypotheses={
                    node.derived_key: round(node.confidence or 0.0, 4)
                    for node in nodes
                    if node.node_type is NodeType.HYPOTHESIS
                },
                topology_version=(
                    context.topology.version if context.topology else ""
                ),
                max_seq=max((node.seq for node in nodes), default=0),
                degraded=result.degraded,
                detail={"trigger": trigger, "agents": list(result.ran)},
            )
            await uow.situation_snapshots.add(snapshot)
            await uow.commit()
        return snapshot.id

    async def _publish(
        self,
        context: IncidentContext,
        result: GovernorResult,
        outcomes: Sequence[tuple[Agent, AgentOutcome]],
    ) -> None:
        if not result.changed and not result.moved:
            return
        async with self._uow_factory() as uow:
            for agent, outcome in outcomes:
                for topic, payload in outcome.events:
                    await publish_agent(
                        uow,
                        self._bus,
                        self._clock,
                        tenant_id=context.tenant_id,
                        app_id=context.app_id,
                        incident_id=context.incident_id,
                        topic=topic,
                        payload=payload,
                        agent=agent.name,
                        confidence=_confidence_of(payload),
                    )
            await publish_agent(
                uow,
                self._bus,
                self._clock,
                tenant_id=context.tenant_id,
                app_id=context.app_id,
                incident_id=context.incident_id,
                topic=BRAIN_UPDATED_TOPIC,
                payload={
                    "incident_id": context.incident_id,
                    "agents": list(result.ran),
                    "nodes_written": result.nodes_written,
                    "versions_written": result.versions_written,
                    "edges_written": result.edges_written,
                    "hypotheses": dict(result.hypotheses),
                    "moved": list(result.moved),
                    "degraded": result.degraded,
                    "degraded_reasons": list(result.degraded_reasons),
                    "queries": result.queries,
                    "tokens": result.tokens,
                    "snapshot_id": result.snapshot_id,
                    "failures": list(result.failures),
                },
            )
            await uow.commit()


def _same(previous: ConfidenceEntry, entry: ConfidenceEntry) -> bool:
    """Whether this recomputation is worth a history row.

    Both the number and the evidence set have to be unchanged. A pass that
    attached a new piece of evidence which happened not to move the number is
    still worth recording - "we found more and it did not change our mind" is a
    finding.
    """
    return (
        round(previous.current, 4) == round(entry.current, 4)
        and set(previous.supporting) == set(entry.supporting)
        and set(previous.refuting) == set(entry.refuting)
    )


def _confidence_of(payload: Mapping[str, Any]) -> Optional[float]:
    value = payload.get("current")
    return float(value) if isinstance(value, (int, float)) else None
