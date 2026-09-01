"""The Incident Brain surface (S5-T05, REQ-050, REQ-053, REQ-055).

Every route here is scoped to one incident in the caller's own tenant. The reads
carry no permission, on exactly the reasoning `GET /incidents/{id}/war-room`
carries none (DR-071): `ROLE_PERMISSIONS[Role.SME]` is empty, and the SME is the
person most likely to be paged and most likely to need the reasoning. A brain
the paged expert cannot open has failed at its only job.

    route                                        guard
    ---------------------------------------------------------------------
    GET  /incidents/{id}/brain                   tenant-scoped read
    GET  /incidents/{id}/brain/nodes/{key}       tenant-scoped read
    GET  /incidents/{id}/brain/topology          tenant-scoped read
    GET  /incidents/{id}/brain/guidance          tenant-scoped read
    POST /incidents/{id}/brain/ask               tenant-scoped, no tools
    POST /incidents/{id}/brain/refresh           declare_incident, budgeted

The two POSTs deserve their own sentences, because they are guarded differently
and the difference is the whole authorization decision on this surface.

`ask` writes no state at all. It reads one incident's graph, asks a model a
question about it, and returns an answer whose citations must resolve to nodes
that retrieval produced. It holds no tool, no invoker and no write path, so
there is nothing for an injected instruction to reach - a structural control
rather than a filter. It does write an audit entry, because "the assistant told
me the pool was fine" is a sentence somebody will say at a review. So it stays
open to every member of the tenant, like the reads.

`refresh` is command work and carries `declare_incident` (DR-101). It writes
persistent brain state - graph nodes and new versions, edges, evidence,
hypotheses, confidence history, situation snapshots, agent-run records - and
spends the incident's token budget, so calling it "state-free" was wrong: it is
the act of the platform forming a belief about an incident, on the record. The
permission is the frozen matrix's existing name for incident command authority,
already carried by pages, escalations and task assignment; no new permission was
minted and the role matrix is untouched.

The guard is `require_incident`, so the incident is resolved inside the caller's
own tenant *before* the permission is evaluated: another tenant's incident is a
404 rather than a 403, and a denial happens before any pass runs, so a refused
call writes nothing and spends nothing.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query

from signalforge.api import deps
from signalforge.contracts.roles import Permission
from signalforge.api.schemas.brain import (
    AnswerOut,
    AskIn,
    BrainOut,
    GuidanceOut,
    NodeHistoryOut,
    RefreshOut,
    TopologyOut,
    answer_out,
    brain_out,
    guidance_out,
    history_out,
    refresh_out,
    topology_out,
)
from signalforge.application.identity.principal import Principal

router = APIRouter(tags=["incident-brain"])


@router.get("/incidents/{incident_id}/brain", response_model=BrainOut)
async def get_brain(
    incident_id: str,
    principal: Principal = Depends(deps.get_principal),
    container: deps.Container = Depends(deps.get_container),
):
    """The ledger, the hypotheses and their confidence history.

    Returns superseded and retracted nodes as well as current ones: nothing is
    deleted, and a client that only ever showed the current view would hide the
    half a post-incident review reads.
    """
    return brain_out(await container.brain.view(principal, incident_id))


@router.get(
    "/incidents/{incident_id}/brain/nodes/{derived_key:path}",
    response_model=NodeHistoryOut,
)
async def get_node_history(
    incident_id: str,
    derived_key: str,
    principal: Principal = Depends(deps.get_principal),
    container: deps.Container = Depends(deps.get_container),
):
    """Every version of one belief, oldest first. What the version-history icon
    on the ledger opens, and what makes an incident replayable."""
    return history_out(
        derived_key,
        await container.brain.history(principal, incident_id, derived_key),
    )


@router.get("/incidents/{incident_id}/brain/topology", response_model=TopologyOut)
async def get_brain_topology(
    incident_id: str,
    version: Optional[str] = Query(
        default=None,
        description="Which imported map to read. Defaults to the latest; older "
        "versions are kept forever and the delta is computed against the one "
        "before the selection.",
    ),
    principal: Principal = Depends(deps.get_principal),
    container: deps.Container = Depends(deps.get_container),
):
    """The dependency map, what changed, and the impact split.

    `confirmed` and `predicted` are two lists and are never merged: a predicted
    downstream outage rendered like an observed one is how a war room pages four
    teams for an outage that never happened.
    """
    return topology_out(
        await container.brain.topology(principal, incident_id, version=version)
    )


@router.get("/incidents/{incident_id}/brain/guidance", response_model=GuidanceOut)
async def get_guidance(
    incident_id: str,
    principal: Principal = Depends(deps.get_principal),
    container: deps.Container = Depends(deps.get_container),
):
    """Similar prior incidents, mistake warnings, contradictions and any content
    that tried to instruct the assistant. Every row is advisory and the caveat
    travels with the payload rather than being a thing the client remembers."""
    return guidance_out(await container.brain.guidance(principal, incident_id))


@router.post("/incidents/{incident_id}/brain/ask", response_model=AnswerOut)
async def ask_copilot(
    incident_id: str,
    body: AskIn,
    principal: Principal = Depends(deps.get_principal),
    container: deps.Container = Depends(deps.get_container),
):
    """Ask the copilot about this incident.

    The answer is drawn only from this incident's graph and every citation must
    resolve to a node retrieval actually returned. An answer that cited anything
    else is discarded, not annotated - showing an ungrounded answer with a
    warning is showing it.
    """
    return answer_out(await container.brain.ask(principal, incident_id, body.question))


@router.post("/incidents/{incident_id}/brain/refresh", response_model=RefreshOut)
async def refresh_brain(
    incident_id: str,
    principal: Principal = Depends(
        deps.require_incident(Permission.DECLARE_INCIDENT)
    ),
    container: deps.Container = Depends(deps.get_container),
):
    """Run a reasoning pass now. Incident command only.

    Idempotent and budgeted: a pass against an unchanged world writes no new
    version, and every model call inside it spends against this incident's token
    budget. Both still matter - they bound what a *permitted* caller can spend -
    but neither is why the route is guarded. It is guarded because the pass
    writes the platform's beliefs about a live incident, and a belief the
    platform records is command work.
    """
    return refresh_out(await container.brain.refresh(principal, incident_id))
