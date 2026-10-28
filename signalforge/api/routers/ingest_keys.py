"""Ingest key management (REQ-032, REQ-012, REQ-015).

All of it is `manage_apps` work: deciding which application may post telemetry
into this tenant is the same operator job as choosing its integrations, and the
roles matrix gives manage_apps to the org owner only. The RBAC dependency takes
app_id from the query string, so a per-app manage_apps binding applies here as
it does everywhere else.

`DELETE` revokes rather than deletes. The receipts a key wrote are the record
that a sender's events were already accepted, and deleting the key that names
them would leave that record unattributable.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query

from signalforge.api import deps
from signalforge.api.schemas.ingest import (
    IngestKeyIn,
    IngestKeyOut,
    IssuedKeyOut,
    ingest_key_out,
    issued_key_out,
)
from signalforge.application.identity.principal import Principal
from signalforge.contracts.roles import Permission
from signalforge.domain.ingest.streams import IngestStream

router = APIRouter(prefix="/ingest-keys", tags=["ingest"])


@router.get("", response_model=list[IngestKeyOut])
async def list_ingest_keys(
    app_id: Optional[str] = Query(default=None),
    stream: Optional[IngestStream] = Query(default=None),
    include_revoked: bool = Query(default=False),
    principal: Principal = Depends(
        deps.require(Permission.MANAGE_APPS, app_id_from_query=True)
    ),
    container: deps.Container = Depends(deps.get_container),
):
    now = container.clock.now()
    keys = await container.ingest_keys.list(
        principal, app_id=app_id, stream=stream, include_revoked=include_revoked
    )
    return [ingest_key_out(key, now) for key in keys]


@router.post("", response_model=IssuedKeyOut, status_code=201)
async def issue_ingest_key(
    body: IngestKeyIn,
    principal: Principal = Depends(deps.require(Permission.MANAGE_APPS)),
    container: deps.Container = Depends(deps.get_container),
):
    issued = await container.ingest_keys.issue(
        principal,
        app_id=body.app_id,
        stream=body.stream,
        name=body.name,
        binding_id=body.binding_id,
    )
    return issued_key_out(issued, container.clock.now())


@router.get("/{key_id}", response_model=IngestKeyOut)
async def get_ingest_key(
    key_id: str,
    principal: Principal = Depends(deps.require(Permission.MANAGE_APPS)),
    container: deps.Container = Depends(deps.get_container),
):
    key = await container.ingest_keys.get(principal, key_id)
    return ingest_key_out(key, container.clock.now())


@router.post("/{key_id}/rotate", response_model=IssuedKeyOut, status_code=201)
async def rotate_ingest_key(
    key_id: str,
    principal: Principal = Depends(deps.require(Permission.MANAGE_APPS)),
    container: deps.Container = Depends(deps.get_container),
):
    """Issue a successor. Both keys stay live, each with its own secret, until
    the old one is revoked - a sender cannot be switched atomically, because it
    is a configuration change in somebody else's system."""
    issued = await container.ingest_keys.rotate(principal, key_id)
    return issued_key_out(issued, container.clock.now())


@router.delete("/{key_id}", response_model=IngestKeyOut)
async def revoke_ingest_key(
    key_id: str,
    principal: Principal = Depends(deps.require(Permission.MANAGE_APPS)),
    container: deps.Container = Depends(deps.get_container),
):
    key = await container.ingest_keys.revoke(principal, key_id)
    return ingest_key_out(key, container.clock.now())
