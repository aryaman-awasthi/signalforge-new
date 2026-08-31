"""/oncall: rotations with explicit on-call order (REQ-014, REQ-012)."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from signalforge.contracts.roles import Permission
from signalforge.api import deps
from signalforge.api.schemas.identity import RotationIn, RotationOut, rotation_out
from signalforge.application.identity.principal import Principal

router = APIRouter(prefix="/oncall", tags=["oncall"], dependencies=[Depends(deps.get_principal)])


@router.get("/rotations", response_model=list[RotationOut])
async def list_rotations(
    principal: Principal = Depends(deps.get_principal),
    container: deps.Container = Depends(deps.get_container),
):
    return [rotation_out(r) for r in await container.oncall.list_rotations(principal)]


@router.post("/rotations", response_model=RotationOut, status_code=201)
async def create_rotation(
    body: RotationIn,
    principal: Principal = Depends(deps.require(Permission.MANAGE_ORG)),
    container: deps.Container = Depends(deps.get_container),
):
    rotation = await container.oncall.save_rotation(
        principal, name=body.name, user_ids=body.user_ids, app_id=body.app_id,
        handoff_interval_hours=body.handoff_interval_hours,
    )
    return rotation_out(rotation)


@router.put("/rotations/{rotation_id}", response_model=RotationOut)
async def update_rotation(
    rotation_id: str,
    body: RotationIn,
    principal: Principal = Depends(deps.require(Permission.MANAGE_ORG)),
    container: deps.Container = Depends(deps.get_container),
):
    rotation = await container.oncall.save_rotation(
        principal, name=body.name, user_ids=body.user_ids, app_id=body.app_id,
        handoff_interval_hours=body.handoff_interval_hours, rotation_id=rotation_id,
    )
    return rotation_out(rotation)


@router.delete("/rotations/{rotation_id}", status_code=204)
async def delete_rotation(
    rotation_id: str,
    principal: Principal = Depends(deps.require(Permission.MANAGE_ORG)),
    container: deps.Container = Depends(deps.get_container),
):
    await container.oncall.delete_rotation(principal, rotation_id)
