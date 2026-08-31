"""/invites: manage_org creates and revokes; the raw token is returned exactly
once at creation (REQ-010, REQ-012). Acceptance is on /auth/accept-invite."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from signalforge.contracts.roles import Permission
from signalforge.api import deps
from signalforge.api.schemas.identity import (
    InviteCreateRequest,
    InviteCreatedOut,
    InviteOut,
    invite_out,
)
from signalforge.application.identity.principal import Principal

router = APIRouter(
    prefix="/invites", tags=["invites"], dependencies=[Depends(deps.get_principal)]
)


@router.post("", response_model=InviteCreatedOut, status_code=201)
async def create_invite(
    body: InviteCreateRequest,
    principal: Principal = Depends(deps.require(Permission.MANAGE_ORG)),
    container: deps.Container = Depends(deps.get_container),
):
    created = await container.org.create_invite(
        principal, email=body.email, roles=body.roles, app_id=body.app_id
    )
    base = invite_out(created.invite, container.clock.now())
    return InviteCreatedOut(**base.model_dump(), token=created.token)


@router.get("", response_model=list[InviteOut])
async def list_invites(
    principal: Principal = Depends(deps.require(Permission.MANAGE_ORG)),
    container: deps.Container = Depends(deps.get_container),
):
    now = container.clock.now()
    return [invite_out(i, now) for i in await container.org.list_invites(principal)]


@router.delete("/{invite_id}", response_model=InviteOut)
async def revoke_invite(
    invite_id: str,
    principal: Principal = Depends(deps.require(Permission.MANAGE_ORG)),
    container: deps.Container = Depends(deps.get_container),
):
    invite = await container.org.revoke_invite(principal, invite_id)
    return invite_out(invite, container.clock.now())
