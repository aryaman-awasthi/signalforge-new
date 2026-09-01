"""Incident-brain ports (CLAUDE.md rule 1).

Tenant isolation (REQ-013) is enforced inside the implementations exactly as it
is everywhere else in this codebase: every method takes `tenant_id`, the
predicate lives in the repository, and a cross-tenant read answers the way a read
of nothing does. There is **no** cross-tenant method here at all - unlike the
event dispatcher or the escalation sweep, nothing about incident reasoning is a
platform-wide work list, and adding one would be the easiest way to leak one
tenant's incident into another's brain.

`IncidentGraphRepository.upsert` carries the whole versioning rule, and it is a
repository method rather than a service loop on purpose: "supersede the old row
and insert the new one" has to be one transaction, or a crash between the two
leaves an incident with two current versions of the same belief.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Optional, Sequence

from signalforge.domain.brain.confidence import ConfidenceEntry
from signalforge.domain.brain.nodes import (
    GraphEdge,
    GraphNode,
    NodeType,
    SituationSnapshot,
)


class IncidentGraphRepository(ABC):
    """Nodes and edges. Append-only in effect: rows are added and marked
    superseded or retracted, never updated in content and never deleted."""

    @abstractmethod
    async def add(self, node: GraphNode) -> GraphNode:
        """Insert a first version and assign its `seq`."""

    @abstractmethod
    async def upsert(
        self,
        node: GraphNode,
        *,
        at: datetime,
    ) -> tuple[GraphNode, bool]:
        """Write what is currently believed about `node.derived_key`.

        Returns (the current node, whether a new version was written).

        Three outcomes, one transaction:

        - nothing held for that key: insert at version 1;
        - held and materially unchanged (`GraphNode.differs_from`): return the
          existing node untouched, so an agent running every thirty seconds
          against an unchanged world writes nothing;
        - held and changed: mark the old row superseded, insert the next version
          naming it, return the new one.
        """

    @abstractmethod
    async def get(self, tenant_id: str, node_id: str) -> Optional[GraphNode]: ...

    @abstractmethod
    async def list(
        self,
        tenant_id: str,
        incident_id: str,
        *,
        types: Optional[Sequence[NodeType]] = None,
        current_only: bool = True,
        limit: int = 500,
    ) -> Sequence[GraphNode]: ...

    @abstractmethod
    async def history(
        self, tenant_id: str, incident_id: str, derived_key: str
    ) -> Sequence[GraphNode]:
        """Every version of one belief, oldest first. What the version-history
        icon on the ledger opens, and what makes an incident replayable."""

    @abstractmethod
    async def find_by_key(
        self, tenant_id: str, incident_id: str, derived_key: str
    ) -> Optional[GraphNode]:
        """The current version, if there is one. The idempotency lookup."""

    @abstractmethod
    async def retract(
        self, tenant_id: str, node_id: str, *, at: datetime, reason: str
    ) -> Optional[GraphNode]:
        """Withdraw a belief without replacing it. The row stays readable."""

    @abstractmethod
    async def add_edge(self, edge: GraphEdge) -> None:
        """Insert or ignore by (incident, source, target, relation). Idempotent
        because an agent re-linking the same evidence to the same hypothesis
        must not double its weight."""

    @abstractmethod
    async def edges(
        self,
        tenant_id: str,
        incident_id: str,
        *,
        target_id: Optional[str] = None,
        source_id: Optional[str] = None,
        limit: int = 1_000,
    ) -> Sequence[GraphEdge]: ...

    @abstractmethod
    async def count_by_type(self, tenant_id: str, incident_id: str) -> dict[str, int]: ...


class ConfidenceHistoryRepository(ABC):
    """Append-only. This is the answer to "why did hypothesis #2 move from 65%
    to 32%", and a store that could rewrite it would make the answer worthless."""

    @abstractmethod
    async def append(
        self, tenant_id: str, incident_id: str, entry: ConfidenceEntry
    ) -> None: ...

    @abstractmethod
    async def list(
        self,
        tenant_id: str,
        incident_id: str,
        *,
        hypothesis_key: Optional[str] = None,
        limit: int = 200,
    ) -> Sequence[ConfidenceEntry]: ...

    @abstractmethod
    async def latest(
        self, tenant_id: str, incident_id: str, hypothesis_key: str
    ) -> Optional[ConfidenceEntry]: ...


class SituationSnapshotRepository(ABC):
    @abstractmethod
    async def add(self, snapshot: SituationSnapshot) -> None: ...

    @abstractmethod
    async def list(
        self, tenant_id: str, incident_id: str, *, limit: int = 50
    ) -> Sequence[SituationSnapshot]: ...

    @abstractmethod
    async def latest(
        self, tenant_id: str, incident_id: str
    ) -> Optional[SituationSnapshot]: ...


class AgentRunRepository(ABC):
    """The execution trace: which agent ran, on what, using which model, what it
    produced, and whether it degraded (CTL-AUD-04).

    Deliberately not the audit chain. An agent run is operational telemetry with
    a retention of days; the *decisions* it produced are graph nodes and audited
    events with the retention of an incident.
    """

    @abstractmethod
    async def start(
        self,
        *,
        run_id: str,
        tenant_id: str,
        agent: str,
        incident_id: Optional[str],
        trigger_topic: str,
        trigger_event_id: Optional[str],
        started_at: datetime,
    ) -> None: ...

    @abstractmethod
    async def finish(
        self,
        *,
        run_id: str,
        status: str,
        finished_at: datetime,
        model: str = "",
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        error: str = "",
    ) -> None: ...

    @abstractmethod
    async def step(
        self,
        *,
        run_id: str,
        seq: int,
        step: str,
        status: str,
        started_at: datetime,
        finished_at: Optional[datetime] = None,
        tokens: int = 0,
        detail: Optional[dict] = None,
    ) -> None: ...

    @abstractmethod
    async def list(
        self,
        tenant_id: str,
        *,
        incident_id: Optional[str] = None,
        limit: int = 50,
    ) -> Sequence[dict]:
        """Runs newest first, as plain dictionaries.

        Dictionaries rather than an entity because nothing in the domain reasons
        over an agent run - it is read by the cockpit and by an auditor, and
        inventing a value object for it would be structure without a consumer.
        """
