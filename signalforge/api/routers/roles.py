"""/roles: read-only view of the frozen roles matrix (REQ-005, REQ-012).
The matrix itself is a contract; only bindings (on /users) are editable."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from signalforge.contracts.roles import ROLE_PERMISSIONS, Role
from signalforge.api import deps
from signalforge.api.schemas.identity import RolesMatrixOut

router = APIRouter(prefix="/roles", tags=["roles"], dependencies=[Depends(deps.get_principal)])


@router.get("/matrix", response_model=RolesMatrixOut)
async def roles_matrix():
    return RolesMatrixOut(
        roles={
            role: sorted(ROLE_PERMISSIONS[role], key=lambda p: p.value)
            for role in Role
        }
    )
