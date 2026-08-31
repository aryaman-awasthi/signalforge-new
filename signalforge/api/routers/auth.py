"""/auth: signup, login, refresh, logout, me (REQ-010).

signup, login, refresh and invite acceptance are the only unauthenticated
endpoints in the product; everything else hangs off the RBAC dependency.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from signalforge.contracts.roles import ROLE_PERMISSIONS
from signalforge.api import deps
from signalforge.api.schemas.identity import (
    AcceptInviteRequest,
    AuthResponse,
    LoginRequest,
    MeOut,
    RefreshRequest,
    SignupRequest,
    TokenPairOut,
    auth_response,
    binding_out,
    token_pair_out,
)
from signalforge.application.identity.principal import Principal

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/signup", response_model=AuthResponse, status_code=201)
async def signup(
    body: SignupRequest, container: deps.Container = Depends(deps.get_container)
):
    result = await container.auth.signup(
        org_name=body.org_name, email=body.email,
        password=body.password, display_name=body.display_name,
    )
    return auth_response(result)


@router.post("/login", response_model=AuthResponse)
async def login(
    body: LoginRequest, container: deps.Container = Depends(deps.get_container)
):
    result = await container.auth.login(
        org_slug=body.org_slug, email=body.email, password=body.password
    )
    return auth_response(result)


@router.post("/refresh", response_model=TokenPairOut)
async def refresh(
    body: RefreshRequest, container: deps.Container = Depends(deps.get_container)
):
    return token_pair_out(await container.auth.refresh(body.refresh_token))


@router.post("/accept-invite", response_model=AuthResponse, status_code=201)
async def accept_invite(
    body: AcceptInviteRequest, container: deps.Container = Depends(deps.get_container)
):
    result = await container.org.accept_invite(
        token=body.token, password=body.password, display_name=body.display_name
    )
    return auth_response(result)


@router.post("/logout", status_code=204)
async def logout(
    principal: Principal = Depends(deps.get_principal),
    container: deps.Container = Depends(deps.get_container),
):
    await container.auth.logout(principal)


@router.get("/me", response_model=MeOut)
async def me(principal: Principal = Depends(deps.get_principal)):
    permissions = sorted(
        {p for b in principal.bindings for p in ROLE_PERMISSIONS[b.role]},
        key=lambda p: p.value,
    )
    return MeOut(
        user_id=principal.user_id, tenant_id=principal.tenant_id,
        email=principal.email, display_name=principal.display_name,
        bindings=[binding_out(b) for b in principal.bindings],
        permissions=permissions,
    )
