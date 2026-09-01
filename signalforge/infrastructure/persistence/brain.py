"""Incident-brain repositories (REQ-050, REQ-053, REQ-013).

Tenant isolation lives here, not in the handlers: every read and write carries
the `tenant_id` predicate inside the class, and a cross-tenant read answers the
way a read of nothing does. Unlike the event dispatcher and the escalation
sweep, **nothing in this file spans tenants** - incident reasoning is never a
platform-wide work list, and a cross-tenant method here would be the shortest
path from one customer's incident into another's brain.

`upsert` carries the whole versioning rule and does it in one flush, because
"supersede the old row, insert the new one" split across two transactions leaves
an incident with two current versions of the same belief - which is exactly the
state the version history exists to make impossible.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Optional, Sequence

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from signalforge.contracts.envelope import ActorType, Authority
from signalforge.domain.brain.confidence import ConfidenceEntry
from signalforge.domain.brain.nodes import (
    ConfirmationState,
    GraphEdge,
    GraphNode,
    NodeStatus,
    NodeType,
    Provenance,
    Relation,
    SituationSnapshot,
)
from signalforge.domain.brain.ports import (
    AgentRunRepository,
    ConfidenceHistoryRepository,
    IncidentGraphRepository,
    SituationSnapshotRepository,
)
from signalforge.infrastructure.persistence.models import (
    AgentRunRow,
    AgentRunStepRow,
    ConfidenceHistoryRow,
    IncidentGraphEdgeRow,
    IncidentGraphNodeRow,
    SituationSnapshotRow,
)


class SqlIncidentGraphRepository(IncidentGraphRepository):
    def __init__(self, session: AsyncSession):
        self._s = session

    # -- writes --------------------------------------------------------------

    async def add(self, node: GraphNode) -> GraphNode:
        seq = await self._next_seq(node.tenant_id, node.incident_id)
        stored = _with_seq(node, seq)
        self._s.add(IncidentGraphNodeRow(**_node_values(stored)))
        await self._s.flush()
        return stored

    async def upsert(self, node: GraphNode, *, at: datetime) -> tuple[GraphNode, bool]:
        existing = await self.find_by_key(
            node.tenant_id, node.incident_id, node.derived_key
        )
        if existing is None:
            return await self.add(node), True
        if not existing.differs_from(
            label=node.label,
            content=node.content,
            confidence=node.confidence,
            confirmation=node.confirmation,
        ):
            # The world has not changed. An agent running every thirty seconds
            # against an unchanged incident writes nothing, which is what keeps
            # the version history a history rather than a heartbeat.
            return existing, False
        successor = existing.next_version(
            node_id=node.id or str(uuid.uuid4()),
            at=at,
            label=node.label,
            content=node.content,
            confidence=node.confidence,
            confirmation=node.confirmation,
            provenance=node.provenance,
            detail=node.detail,
            tags=node.tags,
        )
        stored = await self.add(successor)
        await self._s.execute(
            update(IncidentGraphNodeRow)
            .where(
                IncidentGraphNodeRow.tenant_id == existing.tenant_id,
                IncidentGraphNodeRow.id == existing.id,
            )
            .values(
                status=NodeStatus.SUPERSEDED.value,
                superseded_by=stored.id,
                retired_at=at,
            )
        )
        await self._s.flush()
        return stored, True

    async def retract(
        self, tenant_id: str, node_id: str, *, at: datetime, reason: str
    ) -> Optional[GraphNode]:
        node = await self.get(tenant_id, node_id)
        if node is None or node.status is not NodeStatus.CURRENT:
            return node
        await self._s.execute(
            update(IncidentGraphNodeRow)
            .where(
                IncidentGraphNodeRow.tenant_id == tenant_id,
                IncidentGraphNodeRow.id == node_id,
            )
            .values(
                status=NodeStatus.RETRACTED.value,
                retired_at=at,
                retired_reason=str(reason or "")[:400],
            )
        )
        await self._s.flush()
        return node.as_retracted(at=at, reason=reason)

    async def add_edge(self, edge: GraphEdge) -> None:
        held = (
            await self._s.execute(
                select(IncidentGraphEdgeRow.id).where(
                    IncidentGraphEdgeRow.tenant_id == edge.tenant_id,
                    IncidentGraphEdgeRow.incident_id == edge.incident_id,
                    IncidentGraphEdgeRow.source_id == edge.source_id,
                    IncidentGraphEdgeRow.target_id == edge.target_id,
                    IncidentGraphEdgeRow.relation == edge.relation.value,
                )
            )
        ).scalars().first()
        if held is not None:
            return
        self._s.add(
            IncidentGraphEdgeRow(
                id=edge.id or str(uuid.uuid4()),
                tenant_id=edge.tenant_id,
                incident_id=edge.incident_id,
                source_id=edge.source_id,
                target_id=edge.target_id,
                relation=edge.relation.value,
                weight=float(edge.weight),
                note=edge.note,
                created_at=edge.created_at,
                detail=dict(edge.detail),
            )
        )
        await self._s.flush()

    # -- reads ---------------------------------------------------------------

    async def get(self, tenant_id: str, node_id: str) -> Optional[GraphNode]:
        row = (
            await self._s.execute(
                select(IncidentGraphNodeRow).where(
                    IncidentGraphNodeRow.tenant_id == tenant_id,
                    IncidentGraphNodeRow.id == node_id,
                )
            )
        ).scalars().first()
        return _to_node(row)

    async def list(
        self,
        tenant_id: str,
        incident_id: str,
        *,
        types: Optional[Sequence[NodeType]] = None,
        current_only: bool = True,
        limit: int = 500,
    ) -> Sequence[GraphNode]:
        stmt = select(IncidentGraphNodeRow).where(
            IncidentGraphNodeRow.tenant_id == tenant_id,
            IncidentGraphNodeRow.incident_id == incident_id,
        )
        if current_only:
            stmt = stmt.where(IncidentGraphNodeRow.status == NodeStatus.CURRENT.value)
        if types:
            stmt = stmt.where(
                IncidentGraphNodeRow.node_type.in_([t.value for t in types])
            )
        stmt = stmt.order_by(IncidentGraphNodeRow.seq).limit(max(1, min(limit, 2_000)))
        rows = (await self._s.execute(stmt)).scalars().all()
        return [_to_node(row) for row in rows]

    async def history(
        self, tenant_id: str, incident_id: str, derived_key: str
    ) -> Sequence[GraphNode]:
        rows = (
            await self._s.execute(
                select(IncidentGraphNodeRow)
                .where(
                    IncidentGraphNodeRow.tenant_id == tenant_id,
                    IncidentGraphNodeRow.incident_id == incident_id,
                    IncidentGraphNodeRow.derived_key == derived_key,
                )
                .order_by(IncidentGraphNodeRow.version)
            )
        ).scalars().all()
        return [_to_node(row) for row in rows]

    async def find_by_key(
        self, tenant_id: str, incident_id: str, derived_key: str
    ) -> Optional[GraphNode]:
        row = (
            await self._s.execute(
                select(IncidentGraphNodeRow)
                .where(
                    IncidentGraphNodeRow.tenant_id == tenant_id,
                    IncidentGraphNodeRow.incident_id == incident_id,
                    IncidentGraphNodeRow.derived_key == derived_key,
                    IncidentGraphNodeRow.status == NodeStatus.CURRENT.value,
                )
                .order_by(IncidentGraphNodeRow.version.desc())
                .limit(1)
            )
        ).scalars().first()
        return _to_node(row)

    async def edges(
        self,
        tenant_id: str,
        incident_id: str,
        *,
        target_id: Optional[str] = None,
        source_id: Optional[str] = None,
        limit: int = 1_000,
    ) -> Sequence[GraphEdge]:
        stmt = select(IncidentGraphEdgeRow).where(
            IncidentGraphEdgeRow.tenant_id == tenant_id,
            IncidentGraphEdgeRow.incident_id == incident_id,
        )
        if target_id:
            stmt = stmt.where(IncidentGraphEdgeRow.target_id == target_id)
        if source_id:
            stmt = stmt.where(IncidentGraphEdgeRow.source_id == source_id)
        rows = (
            await self._s.execute(stmt.limit(max(1, min(limit, 5_000))))
        ).scalars().all()
        return [_to_edge(row) for row in rows]

    async def count_by_type(self, tenant_id: str, incident_id: str) -> dict[str, int]:
        rows = (
            await self._s.execute(
                select(
                    IncidentGraphNodeRow.node_type,
                    func.count(IncidentGraphNodeRow.id),
                )
                .where(
                    IncidentGraphNodeRow.tenant_id == tenant_id,
                    IncidentGraphNodeRow.incident_id == incident_id,
                    IncidentGraphNodeRow.status == NodeStatus.CURRENT.value,
                )
                .group_by(IncidentGraphNodeRow.node_type)
            )
        ).all()
        return {str(node_type): int(count) for node_type, count in rows}

    async def _next_seq(self, tenant_id: str, incident_id: str) -> int:
        highest = (
            await self._s.execute(
                select(func.max(IncidentGraphNodeRow.seq)).where(
                    IncidentGraphNodeRow.tenant_id == tenant_id,
                    IncidentGraphNodeRow.incident_id == incident_id,
                )
            )
        ).scalar()
        return int(highest or 0) + 1


class SqlConfidenceHistoryRepository(ConfidenceHistoryRepository):
    def __init__(self, session: AsyncSession):
        self._s = session

    async def append(
        self, tenant_id: str, incident_id: str, entry: ConfidenceEntry
    ) -> None:
        self._s.add(
            ConfidenceHistoryRow(
                tenant_id=tenant_id,
                incident_id=incident_id,
                hypothesis_key=entry.hypothesis_key,
                at=entry.at,
                previous=entry.previous,
                current=entry.current,
                net_weight=entry.net_weight,
                supporting=list(entry.supporting),
                refuting=list(entry.refuting),
                added=list(entry.added),
                removed=list(entry.removed),
                trigger=entry.trigger,
                narrative=entry.narrative,
                narrative_degraded=entry.narrative_degraded,
                detail=dict(entry.detail),
            )
        )
        await self._s.flush()

    async def list(
        self,
        tenant_id: str,
        incident_id: str,
        *,
        hypothesis_key: Optional[str] = None,
        limit: int = 200,
    ) -> Sequence[ConfidenceEntry]:
        stmt = select(ConfidenceHistoryRow).where(
            ConfidenceHistoryRow.tenant_id == tenant_id,
            ConfidenceHistoryRow.incident_id == incident_id,
        )
        if hypothesis_key:
            stmt = stmt.where(ConfidenceHistoryRow.hypothesis_key == hypothesis_key)
        rows = (
            await self._s.execute(
                stmt.order_by(ConfidenceHistoryRow.id).limit(max(1, min(limit, 1_000)))
            )
        ).scalars().all()
        return [_to_entry(row) for row in rows]

    async def latest(
        self, tenant_id: str, incident_id: str, hypothesis_key: str
    ) -> Optional[ConfidenceEntry]:
        row = (
            await self._s.execute(
                select(ConfidenceHistoryRow)
                .where(
                    ConfidenceHistoryRow.tenant_id == tenant_id,
                    ConfidenceHistoryRow.incident_id == incident_id,
                    ConfidenceHistoryRow.hypothesis_key == hypothesis_key,
                )
                .order_by(ConfidenceHistoryRow.id.desc())
                .limit(1)
            )
        ).scalars().first()
        return _to_entry(row) if row is not None else None


class SqlSituationSnapshotRepository(SituationSnapshotRepository):
    def __init__(self, session: AsyncSession):
        self._s = session

    async def add(self, snapshot: SituationSnapshot) -> None:
        self._s.add(
            SituationSnapshotRow(
                id=snapshot.id,
                tenant_id=snapshot.tenant_id,
                incident_id=snapshot.incident_id,
                taken_at=snapshot.taken_at,
                reason=snapshot.reason,
                node_ids=list(snapshot.node_ids),
                hypotheses=dict(snapshot.hypotheses),
                topology_version=snapshot.topology_version,
                max_seq=snapshot.max_seq,
                degraded=snapshot.degraded,
                fingerprint=snapshot.fingerprint,
                detail=dict(snapshot.detail),
            )
        )
        await self._s.flush()

    async def list(
        self, tenant_id: str, incident_id: str, *, limit: int = 50
    ) -> Sequence[SituationSnapshot]:
        rows = (
            await self._s.execute(
                select(SituationSnapshotRow)
                .where(
                    SituationSnapshotRow.tenant_id == tenant_id,
                    SituationSnapshotRow.incident_id == incident_id,
                )
                .order_by(SituationSnapshotRow.taken_at.desc())
                .limit(max(1, min(limit, 200)))
            )
        ).scalars().all()
        return [_to_snapshot(row) for row in rows]

    async def latest(
        self, tenant_id: str, incident_id: str
    ) -> Optional[SituationSnapshot]:
        found = await self.list(tenant_id, incident_id, limit=1)
        return found[0] if found else None


class SqlAgentRunRepository(AgentRunRepository):
    """The execution trace, on the tables S2 created empty for exactly this
    (CTL-AUD-04). Operational telemetry: which agent ran, on what, with which
    model, and whether it degraded."""

    def __init__(self, session: AsyncSession):
        self._s = session

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
    ) -> None:
        self._s.add(
            AgentRunRow(
                id=run_id,
                tenant_id=tenant_id,
                agent=agent[:100],
                status="running",
                incident_id=incident_id,
                trigger_topic=(trigger_topic or "")[:200] or None,
                trigger_event_id=trigger_event_id,
                started_at=started_at,
            )
        )
        await self._s.flush()

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
    ) -> None:
        await self._s.execute(
            update(AgentRunRow)
            .where(AgentRunRow.id == run_id)
            .values(
                status=status[:20],
                finished_at=finished_at,
                model=(model or None),
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                # Truncated, and never a prompt or an answer: this column is
                # read by an operator, not a place to spill incident content.
                error=(error or "")[:500] or None,
            )
        )
        await self._s.flush()

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
    ) -> None:
        self._s.add(
            AgentRunStepRow(
                id=str(uuid.uuid4()),
                run_id=run_id,
                seq=seq,
                step=step[:100],
                status=status[:20],
                started_at=started_at,
                finished_at=finished_at,
                tokens=tokens,
                detail=dict(detail or {}),
            )
        )
        await self._s.flush()

    async def list(
        self,
        tenant_id: str,
        *,
        incident_id: Optional[str] = None,
        limit: int = 50,
    ) -> Sequence[dict]:
        stmt = select(AgentRunRow).where(AgentRunRow.tenant_id == tenant_id)
        if incident_id:
            stmt = stmt.where(AgentRunRow.incident_id == incident_id)
        rows = (
            await self._s.execute(
                stmt.order_by(AgentRunRow.started_at.desc()).limit(
                    max(1, min(limit, 200))
                )
            )
        ).scalars().all()
        return [
            {
                "id": row.id,
                "agent": row.agent,
                "status": row.status,
                "incident_id": row.incident_id,
                "trigger_topic": row.trigger_topic or "",
                "started_at": row.started_at,
                "finished_at": row.finished_at,
                "model": row.model or "",
                "prompt_tokens": row.prompt_tokens,
                "completion_tokens": row.completion_tokens,
                "error": row.error or "",
            }
            for row in rows
        ]


# --- mapping ------------------------------------------------------------------


def _with_seq(node: GraphNode, seq: int) -> GraphNode:
    from dataclasses import replace

    return replace(node, seq=seq, id=node.id or str(uuid.uuid4()))


def _node_values(node: GraphNode) -> dict[str, Any]:
    return {
        "id": node.id,
        "tenant_id": node.tenant_id,
        "incident_id": node.incident_id,
        "node_type": node.node_type.value,
        "derived_key": node.derived_key,
        "version": node.version,
        "seq": node.seq,
        "status": node.status.value,
        "label": node.label,
        "content": node.content,
        "untrusted": node.untrusted,
        "confidence": node.confidence,
        "confirmation": node.confirmation.value if node.confirmation else None,
        "supersedes": node.supersedes,
        "superseded_by": node.superseded_by,
        "retired_at": node.retired_at,
        "retired_reason": node.retired_reason,
        "created_at": node.created_at,
        "provenance": node.provenance.as_dict(),
        "tags": list(node.tags),
        "detail": dict(node.detail),
    }


def _to_node(row: Optional[IncidentGraphNodeRow]) -> Optional[GraphNode]:
    if row is None:
        return None
    raw = dict(row.provenance or {})
    return GraphNode(
        id=row.id,
        tenant_id=row.tenant_id,
        incident_id=row.incident_id,
        node_type=NodeType(row.node_type),
        derived_key=row.derived_key,
        label=row.label,
        created_at=row.created_at,
        provenance=Provenance(
            source=raw.get("source") or "unknown",
            actor_type=ActorType(raw.get("actor_type", ActorType.AGENT.value)),
            actor_id=raw.get("actor_id", ""),
            authority=Authority(raw.get("authority", Authority.INFERRED.value)),
            model=raw.get("model", ""),
            model_class=raw.get("model_class", ""),
            prompt_purpose=raw.get("prompt_purpose", ""),
            degraded=bool(raw.get("degraded", False)),
            result_ref=raw.get("result_ref", ""),
            detail=dict(raw.get("detail") or {}),
        ),
        seq=row.seq,
        version=row.version,
        status=NodeStatus(row.status),
        content=row.content or "",
        untrusted=bool(row.untrusted),
        confidence=row.confidence,
        confirmation=(
            ConfirmationState(row.confirmation) if row.confirmation else None
        ),
        supersedes=row.supersedes,
        superseded_by=row.superseded_by,
        retired_at=row.retired_at,
        retired_reason=row.retired_reason or "",
        tags=tuple(row.tags or ()),
        detail=dict(row.detail or {}),
    )


def _to_edge(row: IncidentGraphEdgeRow) -> GraphEdge:
    return GraphEdge(
        id=row.id,
        tenant_id=row.tenant_id,
        incident_id=row.incident_id,
        source_id=row.source_id,
        target_id=row.target_id,
        relation=Relation(row.relation),
        created_at=row.created_at,
        weight=float(row.weight or 0.0),
        note=row.note or "",
        detail=dict(row.detail or {}),
    )


def _to_entry(row: ConfidenceHistoryRow) -> ConfidenceEntry:
    return ConfidenceEntry(
        hypothesis_key=row.hypothesis_key,
        at=row.at,
        previous=row.previous,
        current=float(row.current),
        net_weight=float(row.net_weight or 0.0),
        supporting=tuple(row.supporting or ()),
        refuting=tuple(row.refuting or ()),
        added=tuple(row.added or ()),
        removed=tuple(row.removed or ()),
        trigger=row.trigger or "",
        narrative=row.narrative or "",
        narrative_degraded=bool(row.narrative_degraded),
        detail=dict(row.detail or {}),
    )


def _to_snapshot(row: SituationSnapshotRow) -> SituationSnapshot:
    return SituationSnapshot(
        id=row.id,
        tenant_id=row.tenant_id,
        incident_id=row.incident_id,
        taken_at=row.taken_at,
        reason=row.reason or "",
        node_ids=tuple(row.node_ids or ()),
        hypotheses={str(k): float(v) for k, v in dict(row.hypotheses or {}).items()},
        topology_version=row.topology_version or "",
        max_seq=row.max_seq or 0,
        degraded=bool(row.degraded),
        detail=dict(row.detail or {}),
    )
