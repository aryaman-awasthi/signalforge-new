"""Platform self-observability surface (REQ-025, REQ-012).

/metrics is unauthenticated by scrape convention and carries no label that
identifies a tenant, an incident or a person - the registry rejects such labels
at declaration time, so that property is enforced rather than reviewed.
Everything that does expose tenant state sits behind RBAC: dead letters and
platform status are manage_apps work, agent traces are read_audit.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query, Response

from signalforge.api import deps
from signalforge.api.schemas.events import DeadLetterOut, RequeueOut, dead_letter_out
from signalforge.api.schemas.platform import (
    AgentRunDetailOut,
    AgentRunOut,
    PlatformStatusOut,
    agent_run_out,
    agent_step_out,
    platform_status_out,
)
from signalforge.application.identity.principal import Principal
from signalforge.contracts.observe import OBSERVE_METRICS_CONTENT_TYPE
from signalforge.contracts.roles import Permission
from signalforge.domain.events.entities import DeadLetterState
from signalforge.domain.identity.errors import NotFoundError

router = APIRouter(tags=["platform"])


@router.get("/metrics", response_class=Response)
async def metrics(container: deps.Container = Depends(deps.get_container)):
    await container.refresh_platform_gauges()
    return Response(
        content=container.metrics.render(), media_type=OBSERVE_METRICS_CONTENT_TYPE
    )


@router.get("/platform/status", response_model=PlatformStatusOut)
async def platform_status(
    principal: Principal = Depends(deps.require(Permission.MANAGE_APPS)),
    container: deps.Container = Depends(deps.get_container),
):
    return platform_status_out(await container.platform_status.status(principal.tenant_id))


@router.get("/platform/dead-letters", response_model=list[DeadLetterOut])
async def list_dead_letters(
    state: Optional[DeadLetterState] = Query(default=None),
    group: Optional[str] = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    principal: Principal = Depends(deps.require(Permission.MANAGE_APPS)),
    container: deps.Container = Depends(deps.get_container),
):
    rows = await container.dead_letters.list(
        principal, state=state, group=group, limit=limit
    )
    return [dead_letter_out(row) for row in rows]


@router.post("/platform/dead-letters/{dead_letter_id}/requeue", response_model=RequeueOut)
async def requeue_dead_letter(
    dead_letter_id: str,
    principal: Principal = Depends(deps.require(Permission.MANAGE_APPS)),
    container: deps.Container = Depends(deps.get_container),
):
    result = await container.dead_letters.requeue(
        principal, dead_letter_id, local_groups=container.dispatcher.groups
    )
    return RequeueOut(
        **dead_letter_out(result.dead_letter).model_dump(),
        drained_here=result.drained_here,
    )


@router.get("/platform/agent-runs", response_model=list[AgentRunOut])
async def list_agent_runs(
    incident_id: Optional[str] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    principal: Principal = Depends(deps.require(Permission.READ_AUDIT)),
    container: deps.Container = Depends(deps.get_container),
):
    async with container.event_uow_factory() as uow:
        runs = await uow.agent_runs.list(principal.tenant_id, incident_id, limit)
    return [agent_run_out(run) for run in runs]


@router.get("/platform/agent-runs/{run_id}", response_model=AgentRunDetailOut)
async def get_agent_run(
    run_id: str,
    principal: Principal = Depends(deps.require(Permission.READ_AUDIT)),
    container: deps.Container = Depends(deps.get_container),
):
    async with container.event_uow_factory() as uow:
        run = await uow.agent_runs.get(principal.tenant_id, run_id)
        if run is None:
            raise NotFoundError(f"agent run {run_id} not found")
        steps = await uow.agent_runs.steps(principal.tenant_id, run_id)
    return AgentRunDetailOut(
        **agent_run_out(run).model_dump(), steps=[agent_step_out(s) for s in steps]
    )
