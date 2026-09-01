"""Pydantic request/response shapes for the identity routers (thin API layer).
Mapping helpers keep routers to parse -> use case -> respond."""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from signalforge.contracts.roles import Permission, Role
from signalforge.domain.identity.entities import Invite, RoleBinding, Tenant, User
from signalforge.domain.identity.oncall import EscalationPolicy, OnCallRotation
from signalforge.application.identity.auth_service import AuthResult, TokenPair
from signalforge.application.identity.oncall_service import PagePlanEntry
from signalforge.application.identity.org_service import UserWithRoles


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid")


# -- auth -------------------------------------------------------------------

class SignupRequest(_In):
    org_name: str = Field(min_length=1, max_length=200)
    email: str
    password: str
    display_name: str = Field(min_length=1, max_length=200)


class LoginRequest(_In):
    org_slug: str = Field(min_length=1)
    email: str
    password: str


class RefreshRequest(_In):
    refresh_token: str = Field(min_length=1)


class AcceptInviteRequest(_In):
    token: str = Field(min_length=1)
    password: str
    display_name: str = Field(min_length=1, max_length=200)


class TokenPairOut(BaseModel):
    access_token: str
    refresh_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int


class WsTicketOut(BaseModel):
    """A shown-once credential, like a token pair or an ingest key.

    It is deliberately *not* a token, and the field says so: `ws_ticket` buys one
    WebSocket handshake within its lifetime and nothing else. The access-token
    verifier refuses it, so it cannot be presented to any HTTP route, and
    redeeming it consumes it. The name is distinct from `ticket` on purpose -
    an incident carries an ITSM `ticket`, which is a reference, not a secret,
    and the credential census has to be able to tell the two apart.
    """

    ws_ticket: str
    expires_in: int
    url_path: str = "/ws"
    subprotocol: Literal["ticket"] = "ticket"


class RoleBindingOut(BaseModel):
    role: Role
    app_id: Optional[str] = None


class UserOut(BaseModel):
    id: str
    email: str
    display_name: str
    is_active: bool
    bindings: list[RoleBindingOut]


class OrgOut(BaseModel):
    id: str
    name: str
    slug: str
    created_at: datetime


class AuthResponse(BaseModel):
    user: UserOut
    org: OrgOut
    tokens: TokenPairOut


class MeOut(BaseModel):
    user_id: str
    tenant_id: str
    email: str
    display_name: str
    bindings: list[RoleBindingOut]
    permissions: list[Permission]


# -- org / users / invites --------------------------------------------------

class OrgRenameRequest(_In):
    name: str = Field(min_length=1, max_length=200)


class RoleBindingIn(_In):
    role: Role
    app_id: Optional[str] = Field(default=None, max_length=100)


class SetRolesRequest(_In):
    bindings: list[RoleBindingIn]


class InviteCreateRequest(_In):
    email: str
    roles: list[Role] = Field(min_length=1)
    app_id: Optional[str] = Field(default=None, max_length=100)


class InviteOut(BaseModel):
    id: str
    email: str
    roles: list[Role]
    app_id: Optional[str]
    expires_at: datetime
    status: Literal["open", "accepted", "revoked", "expired"]


class InviteCreatedOut(InviteOut):
    token: str  # shown exactly once


# -- roles matrix -----------------------------------------------------------

class RolesMatrixOut(BaseModel):
    roles: dict[Role, list[Permission]]


# -- on-call / escalation ---------------------------------------------------

class RotationIn(_In):
    name: str = Field(min_length=1, max_length=200)
    user_ids: list[str] = Field(min_length=1)
    app_id: Optional[str] = Field(default=None, max_length=100)
    handoff_interval_hours: int = 168


class RotationOut(BaseModel):
    id: str
    name: str
    user_ids: list[str]
    app_id: Optional[str]
    handoff_interval_hours: int


class EscalationStepIn(_In):
    target_type: Literal["user", "rotation"]
    target_id: str = Field(min_length=1)
    timeout_s: int


class PolicyIn(_In):
    name: str = Field(min_length=1, max_length=200)
    steps: list[EscalationStepIn] = Field(min_length=1)
    app_id: Optional[str] = Field(default=None, max_length=100)


class EscalationStepOut(BaseModel):
    target_type: str
    target_id: str
    timeout_s: int


class PolicyOut(BaseModel):
    id: str
    name: str
    steps: list[EscalationStepOut]
    app_id: Optional[str]
    page_offsets_s: list[int]


class PagePlanEntryOut(BaseModel):
    order: int
    target_type: str
    target_id: str
    resolved_user_id: str
    page_at_offset_s: int


class TestFireOut(BaseModel):
    policy_id: str
    simulated: Literal[True] = True
    plan: list[PagePlanEntryOut]


# -- mapping helpers --------------------------------------------------------

def binding_out(binding: RoleBinding) -> RoleBindingOut:
    return RoleBindingOut(role=binding.role, app_id=binding.app_id)


def user_out(user: User, bindings: tuple[RoleBinding, ...]) -> UserOut:
    return UserOut(
        id=user.id, email=user.email, display_name=user.display_name,
        is_active=user.is_active, bindings=[binding_out(b) for b in bindings],
    )


def org_out(tenant: Tenant) -> OrgOut:
    return OrgOut(
        id=tenant.id, name=tenant.name, slug=tenant.slug, created_at=tenant.created_at
    )


def auth_response(result: AuthResult) -> AuthResponse:
    return AuthResponse(
        user=user_out(result.user, result.bindings),
        org=org_out(result.tenant),
        tokens=token_pair_out(result.tokens),
    )


def token_pair_out(tokens: TokenPair) -> TokenPairOut:
    return TokenPairOut(
        access_token=tokens.access_token,
        refresh_token=tokens.refresh_token,
        expires_in=tokens.expires_in_s,
    )


def user_with_roles_out(uwr: UserWithRoles) -> UserOut:
    return user_out(uwr.user, uwr.bindings)


def invite_out(invite: Invite, now: datetime) -> InviteOut:
    if invite.accepted_at is not None:
        status = "accepted"
    elif invite.revoked_at is not None:
        status = "revoked"
    elif now >= invite.expires_at:
        status = "expired"
    else:
        status = "open"
    return InviteOut(
        id=invite.id, email=invite.email, roles=list(invite.roles),
        app_id=invite.app_id, expires_at=invite.expires_at, status=status,
    )


def rotation_out(rotation: OnCallRotation) -> RotationOut:
    return RotationOut(
        id=rotation.id, name=rotation.name, user_ids=list(rotation.user_ids),
        app_id=rotation.app_id,
        handoff_interval_hours=rotation.handoff_interval_hours,
    )


def policy_out(policy: EscalationPolicy) -> PolicyOut:
    return PolicyOut(
        id=policy.id, name=policy.name, app_id=policy.app_id,
        steps=[
            EscalationStepOut(
                target_type=s.target_type.value, target_id=s.target_id,
                timeout_s=s.timeout_s,
            )
            for s in policy.steps
        ],
        page_offsets_s=list(policy.page_offsets_s()),
    )


def page_plan_out(policy_id: str, plan: list[PagePlanEntry]) -> TestFireOut:
    return TestFireOut(
        policy_id=policy_id,
        plan=[
            PagePlanEntryOut(
                order=p.order, target_type=p.target_type, target_id=p.target_id,
                resolved_user_id=p.resolved_user_id,
                page_at_offset_s=p.page_at_offset_s,
            )
            for p in plan
        ],
    )
