"""/orgs: the caller's organisation (REQ-010, REQ-012)."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from signalforge.contracts.roles import Permission
from signalforge.api import deps
from signalforge.api.schemas.identity import OrgOut, OrgRenameRequest, org_out
from signalforge.application.identity.principal import Principal

router = APIRouter(prefix="/orgs", tags=["orgs"], dependencies=[Depends(deps.get_principal)])


@router.get("/current", response_model=OrgOut)
async def current_org(
    principal: Principal = Depends(deps.get_principal),
    container: deps.Container = Depends(deps.get_container),
):
    return org_out(await container.org.get_org(principal))


@router.patch("/current", response_model=OrgOut)
async def rename_org(
    body: OrgRenameRequest,
    principal: Principal = Depends(deps.require(Permission.MANAGE_ORG)),
    container: deps.Container = Depends(deps.get_container),
):
    return org_out(await container.org.rename_org(principal, body.name))
