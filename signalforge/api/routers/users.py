"""/users: members and their per-app role bindings (REQ-010, REQ-012).
Reads are open to any authenticated member (the on-call editor needs the
roster); mutations require manage_org."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from signalforge.contracts.roles import Permission
from signalforge.api import deps
from signalforge.api.schemas.identity import (
    SetRolesRequest,
    UserOut,
    user_with_roles_out,
)
from signalforge.application.identity.principal import Principal

router = APIRouter(prefix="/users", tags=["users"], dependencies=[Depends(deps.get_principal)])


@router.get("", response_model=list[UserOut])
async def list_users(
    principal: Principal = Depends(deps.get_principal),
    container: deps.Container = Depends(deps.get_container),
):
    return [user_with_roles_out(u) for u in await container.org.list_users(principal)]


@router.put("/{user_id}/roles", response_model=UserOut)
async def set_user_roles(
    user_id: str,
    body: SetRolesRequest,
    principal: Principal = Depends(deps.require(Permission.MANAGE_ORG)),
    container: deps.Container = Depends(deps.get_container),
):
    result = await container.org.set_user_roles(
        principal, user_id, [(b.role, b.app_id) for b in body.bindings]
    )
    return user_with_roles_out(result)
