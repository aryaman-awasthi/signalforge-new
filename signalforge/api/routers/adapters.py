"""Adapter registry and connector bindings (REQ-030, REQ-012, REQ-015).

Everything here is manage_apps work: choosing an integration, wiring an
application to it and holding its credentials are all the same operator job, and
the roles matrix gives manage_apps to the org owner only. The RBAC dependency
takes app_id from the query string, so a per-app manage_apps binding applies to
`?app_id=` calls exactly as it does everywhere else.

The registry itself holds no tenant data - the same manifests for everyone - but
it still sits behind the guard: what integrations an installation has is
information about that installation.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query, Response

from signalforge.api import deps
from signalforge.api.schemas.adapters import (
    AdapterOut,
    CapabilityMatrixOut,
    ConnectorHealthOut,
    ConnectorIn,
    ConnectorOut,
    ConnectorPatchIn,
    RegistryOut,
    adapter_out,
    connector_health_out,
    connector_out,
    manifest_error_out,
    matrix_out,
)
from signalforge.application.identity.principal import Principal
from signalforge.contracts.envelope import AdapterKind
from signalforge.contracts.roles import Permission
from signalforge.domain.adapters.manifest import AdapterMode, CredentialScope

router = APIRouter(tags=["adapters"])


@router.get("/adapters", response_model=RegistryOut)
async def list_adapters(
    kind: Optional[AdapterKind] = Query(default=None),
    mode: Optional[AdapterMode] = Query(default=None),
    scope: Optional[CredentialScope] = Query(default=None),
    simulated: Optional[bool] = Query(default=None),
    principal: Principal = Depends(deps.require(Permission.MANAGE_APPS)),
    container: deps.Container = Depends(deps.get_container),
):
    entries = container.adapters.entries(
        kind=kind, mode=mode, scope=scope, simulated=simulated
    )
    return RegistryOut(
        adapters=[adapter_out(entry) for entry in entries],
        # Rejected manifests are part of the answer. Silently short lists are
        # how an installation ends up wondering where its integration went.
        errors=[manifest_error_out(error) for error in container.adapters.errors],
    )


@router.get("/adapters/capability-matrix", response_model=CapabilityMatrixOut)
async def capability_matrix(
    principal: Principal = Depends(deps.require(Permission.MANAGE_APPS)),
    container: deps.Container = Depends(deps.get_container),
):
    return matrix_out(container.adapters.matrix())


@router.get("/adapters/{adapter_id}", response_model=AdapterOut)
async def get_adapter(
    adapter_id: str,
    principal: Principal = Depends(deps.require(Permission.MANAGE_APPS)),
    container: deps.Container = Depends(deps.get_container),
):
    return adapter_out(container.adapters.entry(adapter_id))


@router.get("/connectors", response_model=list[ConnectorOut])
async def list_connectors(
    app_id: Optional[str] = Query(default=None),
    adapter_id: Optional[str] = Query(default=None),
    enabled_only: bool = Query(default=False),
    principal: Principal = Depends(
        deps.require(Permission.MANAGE_APPS, app_id_from_query=True)
    ),
    container: deps.Container = Depends(deps.get_container),
):
    views = await container.connectors.list(
        principal, app_id=app_id, adapter_id=adapter_id, enabled_only=enabled_only
    )
    return [connector_out(view) for view in views]


@router.get("/connectors/health", response_model=list[ConnectorHealthOut])
async def connector_health(
    app_id: Optional[str] = Query(default=None),
    principal: Principal = Depends(
        deps.require(Permission.MANAGE_APPS, app_id_from_query=True)
    ),
    container: deps.Container = Depends(deps.get_container),
):
    """What the S23 integration health screen reads: direction, last pull, last
    push, freshness, errors and the SIMULATED flag - the bindings the health
    loop writes back to, joined with the live ingest keys that point at them so
    a push row measures what arrived rather than that this server is up."""
    now = container.clock.now()
    views = await container.connectors.health(principal, app_id=app_id)
    return [connector_health_out(view, now) for view in views]


@router.post("/connectors", response_model=ConnectorOut, status_code=201)
async def create_connector(
    body: ConnectorIn,
    principal: Principal = Depends(deps.require(Permission.MANAGE_APPS)),
    container: deps.Container = Depends(deps.get_container),
):
    view = await container.connectors.create(
        principal,
        app_id=body.app_id,
        adapter_id=body.adapter_id,
        name=body.name,
        credential_scope=body.credential_scope,
        config=body.config,
        credentials=body.credentials,
        enabled=body.enabled,
    )
    return connector_out(view)


@router.get("/connectors/{binding_id}", response_model=ConnectorOut)
async def get_connector(
    binding_id: str,
    principal: Principal = Depends(deps.require(Permission.MANAGE_APPS)),
    container: deps.Container = Depends(deps.get_container),
):
    return connector_out(await container.connectors.get(principal, binding_id))


@router.patch("/connectors/{binding_id}", response_model=ConnectorOut)
async def update_connector(
    binding_id: str,
    body: ConnectorPatchIn,
    principal: Principal = Depends(deps.require(Permission.MANAGE_APPS)),
    container: deps.Container = Depends(deps.get_container),
):
    view = await container.connectors.update(
        principal,
        binding_id,
        config=body.config,
        credentials=body.credentials,
        enabled=body.enabled,
    )
    return connector_out(view)


@router.delete("/connectors/{binding_id}", status_code=204, response_class=Response)
async def delete_connector(
    binding_id: str,
    principal: Principal = Depends(deps.require(Permission.MANAGE_APPS)),
    container: deps.Container = Depends(deps.get_container),
):
    await container.connectors.delete(principal, binding_id)
    return Response(status_code=204)
