"""What an agent is, and what it is allowed to produce (REQ-052).

An agent here is not a microservice and not an autonomous actor. It is a
function from the incident's gathered evidence to a set of **proposals** - nodes
and edges it would like the graph to hold - which the governor writes in one
transaction. Three things follow from that shape and each is deliberate:

- **An agent cannot write.** It holds no repository, no unit of work and no bus.
  Everything it produces goes through the governor's merge, which is where
  idempotency, versioning, provenance stamping and the audit entry live. An
  agent that could write would be an agent that could write a fact.
- **An agent cannot act.** There is no tool, no invoker, no HTTP client on this
  interface. The strongest control against a prompt-injected instruction is not
  a filter; it is that there is nothing on the other side of the model to do.
- **An agent declares its own provenance.** Every proposal names the source and
  the authority, and anything a model produced is `INFERRED` by construction.

`AgentOutcome.degraded` is how the model path's state reaches the cockpit: an
agent whose model call fell back says so, the governor aggregates it, and the
DEGRADED badge is a fact about the run rather than a guess from a missing field.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional, Sequence

from signalforge.application.brain.evidence import IncidentContext
from signalforge.domain.brain.nodes import (
    ConfirmationState,
    GraphNode,
    NodeType,
    Provenance,
    Relation,
)
from signalforge.domain.llm.entities import TokenUsage
from signalforge.domain.llm.ports import LlmProvider


@dataclass(frozen=True)
class NodeProposal:
    """One thing an agent would like the graph to hold.

    Addressed by `derived_key` rather than by id, because the agent does not
    know whether this is the first time anybody has believed it. The governor
    resolves the key: insert, no-op, or a new version naming its predecessor.
    """

    node_type: NodeType
    derived_key: str
    label: str
    provenance: Provenance
    content: str = ""
    confidence: Optional[float] = None
    confirmation: Optional[ConfirmationState] = None
    untrusted: bool = False
    tags: tuple[str, ...] = ()
    detail: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class EdgeProposal:
    """A relation between two proposals, by key. Resolved to node ids by the
    governor after the nodes exist."""

    source_key: str
    target_key: str
    relation: Relation
    weight: float = 0.0
    note: str = ""
    detail: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AgentOutcome:
    nodes: tuple[NodeProposal, ...] = ()
    edges: tuple[EdgeProposal, ...] = ()
    events: tuple[tuple[str, dict], ...] = ()
    """(topic, payload) pairs the governor publishes. Agents do not hold a bus,
    for the same reason they do not hold a repository."""
    notes: tuple[str, ...] = ()
    degraded: bool = False
    degraded_reason: str = ""
    model: str = ""
    usage: TokenUsage = field(default_factory=TokenUsage)
    skipped: str = ""
    """Why this agent did nothing, when it did nothing. An agent that silently
    produced no output and one that had nothing to work with look identical on a
    screen unless one of them says so."""

    @property
    def empty(self) -> bool:
        return not (self.nodes or self.edges or self.events)


class Agent(ABC):
    """Every agent in the roster. Deliberately one method."""

    name: str
    triggers: tuple[str, ...] = ()
    """Topic prefixes that make this agent worth running. Empty means "every
    pass" - the deterministic agents, which are cheap. The model-backed ones
    narrow themselves, because a governor pass that ran every agent on every
    event would spend an incident's token budget on a heartbeat."""

    @abstractmethod
    async def run(self, context: IncidentContext) -> AgentOutcome: ...

    def wants(self, trigger: str) -> bool:
        if not self.triggers:
            return True
        return any(trigger.startswith(prefix) for prefix in self.triggers)


class ModelBackedAgent(Agent):
    """An agent that may call a model, and works without one.

    The base class exists to make the degraded path uniform: `self._ask` returns
    the provider's `CompletionResult` and never raises, and every subclass reads
    `result.degraded` to decide whether it is writing model-derived or
    deterministic output. No subclass catches an exception around a model call,
    because there is not one to catch (REQ-051).
    """

    def __init__(self, llm: LlmProvider):
        self._llm = llm

    @property
    def llm(self) -> LlmProvider:
        return self._llm

    def provenance(
        self,
        *,
        result: Optional[Any] = None,
        source: Optional[str] = None,
        result_ref: str = "",
        detail: Optional[Mapping[str, Any]] = None,
    ) -> Provenance:
        """Provenance for something this agent produced, model or not.

        `Authority.INFERRED` always. An agent correlates things other subsystems
        measured; recording its output as `OBSERVED` would put a correlation
        into the audit chain wearing the authority of a measurement.
        """
        from signalforge.contracts.envelope import ActorType, Authority

        return Provenance(
            source=source or self.name,
            actor_type=ActorType.AGENT,
            actor_id=self.name,
            authority=Authority.INFERRED,
            model=getattr(result, "model", "") if result is not None else "",
            model_class=(
                getattr(result, "model_class").value
                if result is not None and getattr(result, "model_class", None)
                else ""
            ),
            prompt_purpose=getattr(result, "purpose", "") if result is not None else "",
            degraded=bool(getattr(result, "degraded", False)) if result else False,
            result_ref=result_ref,
            detail=dict(detail or {}),
        )


def observed(
    source: str, *, result_ref: str = "", detail: Optional[Mapping[str, Any]] = None
) -> Provenance:
    """Provenance for something another subsystem measured and this agent only
    carried across. `OBSERVED`, because the measurement is real - the agent is
    the courier, not the author."""
    from signalforge.contracts.envelope import ActorType, Authority

    return Provenance(
        source=source,
        actor_type=ActorType.SYSTEM,
        actor_id=source,
        authority=Authority.OBSERVED,
        result_ref=result_ref,
        detail=dict(detail or {}),
    )


def inferred(
    source: str, *, result_ref: str = "", detail: Optional[Mapping[str, Any]] = None
) -> Provenance:
    """Provenance for something an agent derived rather than read.

    The default for anything the brain concludes, and the one that matters to
    the arithmetic: `AUTHORITY_FACTOR` scales inferred evidence to a quarter, so
    a correlation stamped `observed` would be weighted like a measurement.
    Available as a free function as well as on `ModelBackedAgent` because the
    deterministic agents need it too - a prediction is an inference whether or
    not a model was involved.
    """
    from signalforge.contracts.envelope import ActorType, Authority

    return Provenance(
        source=source,
        actor_type=ActorType.AGENT,
        actor_id=source,
        authority=Authority.INFERRED,
        result_ref=result_ref,
        detail=dict(detail or {}),
    )


def authoritative(
    source: str, *, result_ref: str = "", detail: Optional[Mapping[str, Any]] = None
) -> Provenance:
    """Provenance for a fact whose system of record said it - a deployment from
    the change system, a ticket from ITSM."""
    from signalforge.contracts.envelope import ActorType, Authority

    return Provenance(
        source=source,
        actor_type=ActorType.EXTERNAL,
        actor_id=source,
        authority=Authority.AUTHORITATIVE,
        result_ref=result_ref,
        detail=dict(detail or {}),
    )


def node_index(nodes: Sequence[GraphNode]) -> dict[str, GraphNode]:
    """derived_key -> current node. How an edge proposal is resolved to ids."""
    return {node.derived_key: node for node in nodes if node.current}
