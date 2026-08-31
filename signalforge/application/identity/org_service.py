"""Org management use cases: org profile, users and role bindings, invites
(REQ-010, REQ-012). RBAC is enforced by the API dependency before these run;
every mutation lands in the audit chain.
"""

from __future__ import annotations

import secrets
import uuid
from dataclasses import dataclass
from datetime import timedelta
from typing import Optional, Sequence

from signalforge.contracts.roles import Role
from signalforge.domain.identity.entities import (
    Invite,
    RoleBinding,
    Tenant,
    User,
    normalize_email,
)
from signalforge.domain.identity.errors import ConflictError, NotFoundError
from signalforge.domain.identity.ports import Clock, PasswordHasher
from signalforge.domain.identity.tokens import hash_refresh_token as _sha256
from signalforge.application.identity.auth_service import (
    AuthResult,
    AuthService,
    UowFactory,
    _check_password,
)
from signalforge.application.identity.principal import Principal


@dataclass(frozen=True)
class UserWithRoles:
    user: User
    bindings: tuple[RoleBinding, ...]


@dataclass(frozen=True)
class CreatedInvite:
    invite: Invite
    token: str  # returned exactly once; only its hash is stored


class OrgService:
    def __init__(
        self,
        uow_factory: UowFactory,
        hasher: PasswordHasher,
        auth: AuthService,
        clock: Clock,
        *,
        invite_ttl_s: int,
    ):
        self._uow_factory = uow_factory
        self._hasher = hasher
        self._auth = auth
        self._clock = clock
        self._invite_ttl_s = invite_ttl_s

    # -- org ---------------------------------------------------------------

    async def get_org(self, principal: Principal) -> Tenant:
        async with self._uow_factory() as uow:
            tenant = await uow.tenants.get(principal.tenant_id)
        if tenant is None:
            raise NotFoundError("organisation not found")
        return tenant

    async def rename_org(self, principal: Principal, name: str) -> Tenant:
        async with self._uow_factory() as uow:
            tenant = await uow.tenants.get(principal.tenant_id)
            if tenant is None:
                raise NotFoundError("organisation not found")
            old_name = tenant.name
            tenant.name = name
            await uow.tenants.update(tenant)
            await uow.audit.append(
                tenant_id=tenant.id, topic="audit.org.renamed",
                actor_type="human", actor_id=principal.user_id,
                payload={"from": old_name, "to": name},
            )
            await uow.commit()
        return tenant

    # -- users and roles ---------------------------------------------------

    async def list_users(self, principal: Principal) -> list[UserWithRoles]:
        async with self._uow_factory() as uow:
            users = await uow.users.list(principal.tenant_id)
            bindings = await uow.bindings.list_for_tenant(principal.tenant_id)
        by_user: dict[str, list[RoleBinding]] = {}
        for b in bindings:
            by_user.setdefault(b.user_id, []).append(b)
        return [UserWithRoles(u, tuple(by_user.get(u.id, ()))) for u in users]

    async def set_user_roles(
        self,
        principal: Principal,
        user_id: str,
        bindings: Sequence[tuple[Role, Optional[str]]],
    ) -> UserWithRoles:
        tenant_id = principal.tenant_id
        async with self._uow_factory() as uow:
            user = await uow.users.get(tenant_id, user_id)
            if user is None:
                raise NotFoundError("user not found")
            new = tuple(
                RoleBinding(tenant_id=tenant_id, user_id=user_id, role=r, app_id=a)
                for r, a in dict.fromkeys(bindings)
            )
            keeps_owner = any(
                b.role is Role.ORG_OWNER and b.app_id is None for b in new
            )
            if not keeps_owner:
                others = [
                    b for b in await uow.bindings.list_for_tenant(tenant_id)
                    if b.role is Role.ORG_OWNER and b.app_id is None
                    and b.user_id != user_id
                ]
                if not others:
                    raise ConflictError("the organisation must keep at least one org owner")
            old = await uow.bindings.list_for_user(tenant_id, user_id)
            await uow.bindings.replace_for_user(tenant_id, user_id, new)
            await uow.audit.append(
                tenant_id=tenant_id, topic="audit.org.roles_changed",
                actor_type="human", actor_id=principal.user_id,
                payload={
                    "user_id": user_id,
                    "from": [[b.role.value, b.app_id] for b in old],
                    "to": [[b.role.value, b.app_id] for b in new],
                },
            )
            await uow.commit()
        return UserWithRoles(user, new)

    # -- invites -----------------------------------------------------------

    async def create_invite(
        self,
        principal: Principal,
        *,
        email: str,
        roles: Sequence[Role],
        app_id: Optional[str] = None,
    ) -> CreatedInvite:
        email = normalize_email(email)
        now = self._clock.now()
        async with self._uow_factory() as uow:
            if await uow.users.get_by_email(principal.tenant_id, email) is not None:
                raise ConflictError("a user with this email already exists")
            open_invites = [
                i for i in await uow.invites.list(principal.tenant_id)
                if i.email == email and i.is_open(now)
            ]
            if open_invites:
                raise ConflictError("an open invite for this email already exists")
            token = secrets.token_urlsafe(32)
            invite = Invite(
                id=str(uuid.uuid4()), tenant_id=principal.tenant_id, email=email,
                roles=tuple(roles), app_id=app_id, token_hash=_sha256(token),
                created_by=principal.user_id, created_at=now,
                expires_at=now + timedelta(seconds=self._invite_ttl_s),
            )
            await uow.invites.add(invite)
            await uow.audit.append(
                tenant_id=principal.tenant_id, topic="audit.org.invite_created",
                actor_type="human", actor_id=principal.user_id,
                payload={
                    "invite_id": invite.id, "email": email,
                    "roles": [r.value for r in roles], "app_id": app_id,
                },
            )
            await uow.commit()
        return CreatedInvite(invite=invite, token=token)

    async def list_invites(self, principal: Principal) -> Sequence[Invite]:
        async with self._uow_factory() as uow:
            return await uow.invites.list(principal.tenant_id)

    async def revoke_invite(self, principal: Principal, invite_id: str) -> Invite:
        now = self._clock.now()
        async with self._uow_factory() as uow:
            invite = await uow.invites.get(principal.tenant_id, invite_id)
            if invite is None:
                raise NotFoundError("invite not found")
            if not invite.is_open(now):
                raise ConflictError("invite is no longer open")
            invite.revoked_at = now
            await uow.invites.update(invite)
            await uow.audit.append(
                tenant_id=principal.tenant_id, topic="audit.org.invite_revoked",
                actor_type="human", actor_id=principal.user_id,
                payload={"invite_id": invite.id},
            )
            await uow.commit()
        return invite

    async def accept_invite(
        self, *, token: str, password: str, display_name: str
    ) -> AuthResult:
        _check_password(password)
        now = self._clock.now()
        async with self._uow_factory() as uow:
            invite = await uow.invites.get_by_token_hash(_sha256(token))
            if invite is None or not invite.is_open(now):
                raise NotFoundError("invite is invalid or no longer open")
            tenant = await uow.tenants.get(invite.tenant_id)
            if tenant is None or not tenant.is_active:
                raise NotFoundError("invite is invalid or no longer open")
            user = User(
                id=str(uuid.uuid4()), tenant_id=tenant.id, email=invite.email,
                display_name=display_name,
                password_hash=self._hasher.hash(password), created_at=now,
            )
            await uow.users.add(user)
            bindings = tuple(
                RoleBinding(
                    tenant_id=tenant.id, user_id=user.id, role=r, app_id=invite.app_id
                )
                for r in invite.roles
            )
            await uow.bindings.replace_for_user(tenant.id, user.id, bindings)
            invite.accepted_at = now
            await uow.invites.update(invite)
            await uow.audit.append(
                tenant_id=tenant.id, topic="audit.org.invite_accepted",
                actor_type="human", actor_id=user.id,
                payload={"invite_id": invite.id, "email": user.email},
            )
            tokens = await self._auth._start_session(uow, tenant.id, user.id)
            await uow.commit()
        return AuthResult(user=user, tenant=tenant, bindings=bindings, tokens=tokens)
