"""/events: replay, causal chains and seals (REQ-023, REQ-012).

Replay is the audit surface, so it hangs off read_audit; sealing writes a
permanent record at the end of an incident and hangs off close_incident, which
keeps the frozen roles matrix honest - the Auditor stays strictly read-only.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query

from signalforge.api import deps
from signalforge.api.schemas.events import (
    CausalChainOut,
    EventPageOut,
    SealIn,
    SealOut,
    SealVerificationOut,
    page_out,
    seal_out,
    verification_out,
)
from signalforge.application.events.replay import DEFAULT_REPLAY_LIMIT, MAX_REPLAY_LIMIT
from signalforge.application.identity.principal import Principal
from signalforge.contracts.envelope import EventEnvelope, Plane
from signalforge.contracts.roles import Permission

router = APIRouter(prefix="/events", tags=["events"])


@router.get("/seals", response_model=list[SealOut])
async def list_seals(
    incident_id: Optional[str] = Query(default=None),
    principal: Principal = Depends(deps.require(Permission.READ_AUDIT)),
    container: deps.Container = Depends(deps.get_container),
):
    return [seal_out(s) for s in await container.seals.list_seals(principal, incident_id)]


@router.post("/seals", response_model=SealOut, status_code=201)
async def create_seal(
    body: SealIn,
    principal: Principal = Depends(deps.require(Permission.CLOSE_INCIDENT)),
    container: deps.Container = Depends(deps.get_container),
):
    # Seals cover the incident plane only: the signal plane ages out under
    # retention, so a seal over it would stop verifying by design.
    seal = await container.seals.seal(
        principal,
        incident_id=body.incident_id,
        plane=Plane.INCIDENT,
        until=body.until,
    )
    return seal_out(seal)


@router.post("/seals/{seal_id}/verify", response_model=SealVerificationOut)
async def verify_seal(
    seal_id: str,
    principal: Principal = Depends(deps.require(Permission.READ_AUDIT)),
    container: deps.Container = Depends(deps.get_container),
):
    return verification_out(await container.seals.verify(principal, seal_id))


@router.get("", response_model=EventPageOut)
async def replay(
    after_seq: int = Query(default=0, ge=0),
    until: Optional[int] = Query(default=None, ge=1),
    topics: Optional[list[str]] = Query(default=None),
    incident_id: Optional[str] = Query(default=None),
    plane: Optional[Plane] = Query(default=None),
    limit: int = Query(default=DEFAULT_REPLAY_LIMIT, ge=1, le=MAX_REPLAY_LIMIT),
    principal: Principal = Depends(deps.require(Permission.READ_AUDIT)),
    container: deps.Container = Depends(deps.get_container),
):
    page = await container.replay.read(
        principal,
        after_seq=after_seq,
        until=until,
        topics=topics,
        incident_id=incident_id,
        plane=plane,
        limit=limit,
    )
    return page_out(page)


@router.get("/{event_id}", response_model=EventEnvelope)
async def get_event(
    event_id: str,
    principal: Principal = Depends(deps.require(Permission.READ_AUDIT)),
    container: deps.Container = Depends(deps.get_container),
):
    return await container.replay.get(principal, event_id)


@router.get("/{event_id}/chain", response_model=CausalChainOut)
async def causal_chain(
    event_id: str,
    principal: Principal = Depends(deps.require(Permission.READ_AUDIT)),
    container: deps.Container = Depends(deps.get_container),
):
    chain = await container.replay.causal_chain(principal, event_id)
    return CausalChainOut(event_id=event_id, events=list(chain), count=len(chain))
