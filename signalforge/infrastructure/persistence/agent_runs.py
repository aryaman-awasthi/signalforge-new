"""SQL agent-run trace store (REQ-025). Tenant-scoped like every other
repository: a run is read back only through the tenant that produced it, and a
step is only reachable through its run, so the join carries the predicate."""

from __future__ import annotations

from typing import Optional, Sequence

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from signalforge.domain.observability.agent_runs import (
    AgentRun,
    AgentRunStatus,
    AgentRunStep,
    AgentRunStore,
)
from signalforge.infrastructure.persistence.models import AgentRunRow, AgentRunStepRow


class SqlAgentRunStore(AgentRunStore):
    def __init__(self, session: AsyncSession):
        self._s = session

    async def start(self, run: AgentRun) -> None:
        self._s.add(
            AgentRunRow(
                id=run.id,
                tenant_id=run.tenant_id,
                agent=run.agent,
                status=run.status.value,
                incident_id=run.incident_id,
                trigger_topic=run.trigger_topic,
                trigger_event_id=run.trigger_event_id,
                started_at=run.started_at,
                finished_at=run.finished_at,
                model=run.model,
                prompt_tokens=run.prompt_tokens,
                completion_tokens=run.completion_tokens,
                error=run.error,
            )
        )
        await self._s.flush()

    async def finish(self, run: AgentRun) -> None:
        await self._s.execute(
            update(AgentRunRow)
            .where(AgentRunRow.id == run.id, AgentRunRow.tenant_id == run.tenant_id)
            .values(
                status=run.status.value,
                finished_at=run.finished_at,
                prompt_tokens=run.prompt_tokens,
                completion_tokens=run.completion_tokens,
                error=run.error,
            )
        )
        await self._s.flush()

    async def add_step(self, step: AgentRunStep) -> None:
        self._s.add(
            AgentRunStepRow(
                id=step.id,
                run_id=step.run_id,
                seq=step.seq,
                step=step.step,
                status=step.status,
                started_at=step.started_at,
                finished_at=step.finished_at,
                tokens=step.tokens,
                detail=dict(step.detail),
            )
        )
        await self._s.flush()

    async def get(self, tenant_id: str, run_id: str) -> Optional[AgentRun]:
        row = (
            await self._s.execute(
                select(AgentRunRow).where(
                    AgentRunRow.tenant_id == tenant_id, AgentRunRow.id == run_id
                )
            )
        ).scalars().first()
        return self._to_entity(row) if row is not None else None

    async def list(
        self, tenant_id: str, incident_id: Optional[str] = None, limit: int = 50
    ) -> Sequence[AgentRun]:
        stmt = select(AgentRunRow).where(AgentRunRow.tenant_id == tenant_id)
        if incident_id is not None:
            stmt = stmt.where(AgentRunRow.incident_id == incident_id)
        rows = (
            await self._s.execute(stmt.order_by(AgentRunRow.started_at.desc()).limit(limit))
        ).scalars().all()
        return [self._to_entity(r) for r in rows]

    async def steps(self, tenant_id: str, run_id: str) -> Sequence[AgentRunStep]:
        rows = (
            await self._s.execute(
                select(AgentRunStepRow)
                .join(AgentRunRow, AgentRunRow.id == AgentRunStepRow.run_id)
                .where(AgentRunRow.tenant_id == tenant_id, AgentRunStepRow.run_id == run_id)
                .order_by(AgentRunStepRow.seq)
            )
        ).scalars().all()
        return [
            AgentRunStep(
                id=r.id,
                run_id=r.run_id,
                seq=r.seq,
                step=r.step,
                status=r.status,
                started_at=r.started_at,
                finished_at=r.finished_at,
                tokens=r.tokens,
                detail=dict(r.detail or {}),
            )
            for r in rows
        ]

    @staticmethod
    def _to_entity(row: AgentRunRow) -> AgentRun:
        return AgentRun(
            id=row.id,
            tenant_id=row.tenant_id,
            agent=row.agent,
            status=AgentRunStatus(row.status),
            started_at=row.started_at,
            incident_id=row.incident_id,
            trigger_topic=row.trigger_topic,
            trigger_event_id=row.trigger_event_id,
            finished_at=row.finished_at,
            model=row.model,
            prompt_tokens=row.prompt_tokens,
            completion_tokens=row.completion_tokens,
            error=row.error,
        )
