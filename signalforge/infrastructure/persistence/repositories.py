"""Repository implementations. Tenant isolation lives HERE (REQ-013): every
query on tenant-owned data carries a tenant_id predicate inside the repository,
so no handler or use case can forget it. The only exceptions are bootstrap
lookups that happen before a tenant is known (slug login, invite-token and
refresh-token lookup), which resolve to exactly one row by an unguessable
unique key and return the tenant with the entity."""

from __future__ import annotations

from datetime import datetime
from typing import Optional, Sequence

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from signalforge.contracts.roles import Role
from signalforge.domain.identity.entities import (
    Invite,
    RoleBinding,
    Session,
    Tenant,
    User,
)
from signalforge.domain.identity.oncall import (
    EscalationPolicy,
    EscalationStep,
    EscalationTargetType,
    OnCallRotation,
)
from signalforge.domain.identity.ports import (
    EscalationRepository,
    InviteRepository,
    OnCallRepository,
    RoleBindingRepository,
    SessionRepository,
    TenantRepository,
    UserRepository,
)
from signalforge.infrastructure.persistence.models import (
    EscalationPolicyRow,
    InviteRow,
    OnCallRotationRow,
    RoleBindingRow,
    SessionRow,
    TenantRow,
    UserRow,
)


class SqlTenantRepository(TenantRepository):
    def __init__(self, session: AsyncSession):
        self._s = session

    async def add(self, tenant: Tenant) -> None:
        self._s.add(
            TenantRow(
                id=tenant.id, name=tenant.name, slug=tenant.slug,
                created_at=tenant.created_at, is_active=tenant.is_active,
            )
        )
        await self._s.flush()

    async def get(self, tenant_id: str) -> Optional[Tenant]:
        row = await self._s.get(TenantRow, tenant_id)
        return self._to_entity(row)

    async def get_by_slug(self, slug: str) -> Optional[Tenant]:
        row = (
            await self._s.execute(select(TenantRow).where(TenantRow.slug == slug))
        ).scalars().first()
        return self._to_entity(row)

    async def update(self, tenant: Tenant) -> None:
        await self._s.execute(
            update(TenantRow)
            .where(TenantRow.id == tenant.id)
            .values(name=tenant.name, slug=tenant.slug, is_active=tenant.is_active)
        )

    @staticmethod
    def _to_entity(row: Optional[TenantRow]) -> Optional[Tenant]:
        if row is None:
            return None
        return Tenant(
            id=row.id, name=row.name, slug=row.slug,
            created_at=row.created_at, is_active=row.is_active,
        )


class SqlUserRepository(UserRepository):
    def __init__(self, session: AsyncSession):
        self._s = session

    async def add(self, user: User) -> None:
        self._s.add(
            UserRow(
                id=user.id, tenant_id=user.tenant_id, email=user.email,
                display_name=user.display_name, password_hash=user.password_hash,
                created_at=user.created_at, is_active=user.is_active,
            )
        )
        await self._s.flush()

    async def get(self, tenant_id: str, user_id: str) -> Optional[User]:
        row = (
            await self._s.execute(
                select(UserRow).where(
                    UserRow.tenant_id == tenant_id, UserRow.id == user_id
                )
            )
        ).scalars().first()
        return self._to_entity(row)

    async def get_by_email(self, tenant_id: str, email: str) -> Optional[User]:
        row = (
            await self._s.execute(
                select(UserRow).where(
                    UserRow.tenant_id == tenant_id, UserRow.email == email.lower()
                )
            )
        ).scalars().first()
        return self._to_entity(row)

    async def list(self, tenant_id: str) -> Sequence[User]:
        rows = (
            await self._s.execute(
                select(UserRow)
                .where(UserRow.tenant_id == tenant_id)
                .order_by(UserRow.created_at)
            )
        ).scalars().all()
        return [self._to_entity(r) for r in rows]

    async def update(self, user: User) -> None:
        await self._s.execute(
            update(UserRow)
            .where(UserRow.tenant_id == user.tenant_id, UserRow.id == user.id)
            .values(
                display_name=user.display_name,
                password_hash=user.password_hash,
                is_active=user.is_active,
            )
        )

    @staticmethod
    def _to_entity(row: Optional[UserRow]) -> Optional[User]:
        if row is None:
            return None
        return User(
            id=row.id, tenant_id=row.tenant_id, email=row.email,
            display_name=row.display_name, password_hash=row.password_hash,
            created_at=row.created_at, is_active=row.is_active,
        )


class SqlRoleBindingRepository(RoleBindingRepository):
    def __init__(self, session: AsyncSession):
        self._s = session

    async def list_for_user(self, tenant_id: str, user_id: str) -> Sequence[RoleBinding]:
        rows = (
            await self._s.execute(
                select(RoleBindingRow).where(
                    RoleBindingRow.tenant_id == tenant_id,
                    RoleBindingRow.user_id == user_id,
                )
            )
        ).scalars().all()
        return [
            RoleBinding(
                tenant_id=r.tenant_id, user_id=r.user_id,
                role=Role(r.role), app_id=r.app_id,
            )
            for r in rows
        ]

    async def list_for_tenant(self, tenant_id: str) -> Sequence[RoleBinding]:
        rows = (
            await self._s.execute(
                select(RoleBindingRow).where(RoleBindingRow.tenant_id == tenant_id)
            )
        ).scalars().all()
        return [
            RoleBinding(
                tenant_id=r.tenant_id, user_id=r.user_id,
                role=Role(r.role), app_id=r.app_id,
            )
            for r in rows
        ]

    async def replace_for_user(
        self, tenant_id: str, user_id: str, bindings: Sequence[RoleBinding]
    ) -> None:
        for b in bindings:
            if b.tenant_id != tenant_id or b.user_id != user_id:
                raise ValueError("binding tenant/user mismatch")
        await self._s.execute(
            delete(RoleBindingRow).where(
                RoleBindingRow.tenant_id == tenant_id,
                RoleBindingRow.user_id == user_id,
            )
        )
        for b in bindings:
            self._s.add(
                RoleBindingRow(
                    tenant_id=b.tenant_id, user_id=b.user_id,
                    role=b.role.value, app_id=b.app_id,
                )
            )
        await self._s.flush()


class SqlInviteRepository(InviteRepository):
    def __init__(self, session: AsyncSession):
        self._s = session

    async def add(self, invite: Invite) -> None:
        self._s.add(
            InviteRow(
                id=invite.id, tenant_id=invite.tenant_id, email=invite.email,
                roles=[r.value for r in invite.roles], app_id=invite.app_id,
                token_hash=invite.token_hash, created_by=invite.created_by,
                created_at=invite.created_at, expires_at=invite.expires_at,
                accepted_at=invite.accepted_at, revoked_at=invite.revoked_at,
            )
        )
        await self._s.flush()

    async def get(self, tenant_id: str, invite_id: str) -> Optional[Invite]:
        row = (
            await self._s.execute(
                select(InviteRow).where(
                    InviteRow.tenant_id == tenant_id, InviteRow.id == invite_id
                )
            )
        ).scalars().first()
        return self._to_entity(row)

    async def get_by_token_hash(self, token_hash: str) -> Optional[Invite]:
        # Bootstrap lookup: the acceptor has no tenant yet; the token hash is
        # unique and unguessable, and the entity carries its tenant home.
        row = (
            await self._s.execute(
                select(InviteRow).where(InviteRow.token_hash == token_hash)
            )
        ).scalars().first()
        return self._to_entity(row)

    async def list(self, tenant_id: str) -> Sequence[Invite]:
        rows = (
            await self._s.execute(
                select(InviteRow)
                .where(InviteRow.tenant_id == tenant_id)
                .order_by(InviteRow.created_at)
            )
        ).scalars().all()
        return [self._to_entity(r) for r in rows]

    async def update(self, invite: Invite) -> None:
        await self._s.execute(
            update(InviteRow)
            .where(InviteRow.tenant_id == invite.tenant_id, InviteRow.id == invite.id)
            .values(accepted_at=invite.accepted_at, revoked_at=invite.revoked_at)
        )

    @staticmethod
    def _to_entity(row: Optional[InviteRow]) -> Optional[Invite]:
        if row is None:
            return None
        return Invite(
            id=row.id, tenant_id=row.tenant_id, email=row.email,
            roles=tuple(Role(r) for r in row.roles), app_id=row.app_id,
            token_hash=row.token_hash, created_by=row.created_by,
            created_at=row.created_at, expires_at=row.expires_at,
            accepted_at=row.accepted_at, revoked_at=row.revoked_at,
        )


class SqlSessionRepository(SessionRepository):
    def __init__(self, session: AsyncSession):
        self._s = session

    async def add(self, session: Session) -> None:
        self._s.add(
            SessionRow(
                id=session.id, tenant_id=session.tenant_id, user_id=session.user_id,
                family_id=session.family_id,
                refresh_token_hash=session.refresh_token_hash,
                prev_token_hash=session.prev_token_hash,
                issued_at=session.issued_at, expires_at=session.expires_at,
                rotated_at=session.rotated_at, revoked_at=session.revoked_at,
                revoked_reason=session.revoked_reason,
            )
        )
        await self._s.flush()

    async def get(self, tenant_id: str, session_id: str) -> Optional[Session]:
        row = (
            await self._s.execute(
                select(SessionRow).where(
                    SessionRow.tenant_id == tenant_id, SessionRow.id == session_id
                )
            )
        ).scalars().first()
        return self._to_entity(row)

    async def get_by_token_hash(self, token_hash: str) -> Optional[Session]:
        # Bootstrap lookup by unguessable hash; matches the current OR the
        # previous token so the use case can detect refresh-token reuse.
        row = (
            await self._s.execute(
                select(SessionRow).where(
                    (SessionRow.refresh_token_hash == token_hash)
                    | (SessionRow.prev_token_hash == token_hash)
                )
            )
        ).scalars().first()
        return self._to_entity(row)

    async def update(self, session: Session) -> None:
        await self._s.execute(
            update(SessionRow)
            .where(SessionRow.tenant_id == session.tenant_id, SessionRow.id == session.id)
            .values(
                refresh_token_hash=session.refresh_token_hash,
                prev_token_hash=session.prev_token_hash,
                rotated_at=session.rotated_at,
                revoked_at=session.revoked_at,
                revoked_reason=session.revoked_reason,
            )
        )

    async def revoke_family(
        self, tenant_id: str, family_id: str, reason: str, at: datetime
    ) -> int:
        result = await self._s.execute(
            update(SessionRow)
            .where(
                SessionRow.tenant_id == tenant_id,
                SessionRow.family_id == family_id,
                SessionRow.revoked_at.is_(None),
            )
            .values(revoked_at=at, revoked_reason=reason)
        )
        return result.rowcount or 0

    @staticmethod
    def _to_entity(row: Optional[SessionRow]) -> Optional[Session]:
        if row is None:
            return None
        return Session(
            id=row.id, tenant_id=row.tenant_id, user_id=row.user_id,
            family_id=row.family_id, refresh_token_hash=row.refresh_token_hash,
            prev_token_hash=row.prev_token_hash, issued_at=row.issued_at,
            expires_at=row.expires_at, rotated_at=row.rotated_at,
            revoked_at=row.revoked_at, revoked_reason=row.revoked_reason,
        )


class SqlOnCallRepository(OnCallRepository):
    def __init__(self, session: AsyncSession):
        self._s = session

    async def upsert(self, rotation: OnCallRotation) -> None:
        row = (
            await self._s.execute(
                select(OnCallRotationRow).where(
                    OnCallRotationRow.tenant_id == rotation.tenant_id,
                    OnCallRotationRow.id == rotation.id,
                )
            )
        ).scalars().first()
        if row is None:
            self._s.add(
                OnCallRotationRow(
                    id=rotation.id, tenant_id=rotation.tenant_id, name=rotation.name,
                    app_id=rotation.app_id, user_ids=list(rotation.user_ids),
                    handoff_interval_hours=rotation.handoff_interval_hours,
                )
            )
        else:
            row.name = rotation.name
            row.app_id = rotation.app_id
            row.user_ids = list(rotation.user_ids)
            row.handoff_interval_hours = rotation.handoff_interval_hours
        await self._s.flush()

    async def get(self, tenant_id: str, rotation_id: str) -> Optional[OnCallRotation]:
        row = (
            await self._s.execute(
                select(OnCallRotationRow).where(
                    OnCallRotationRow.tenant_id == tenant_id,
                    OnCallRotationRow.id == rotation_id,
                )
            )
        ).scalars().first()
        return self._to_entity(row)

    async def list(self, tenant_id: str) -> Sequence[OnCallRotation]:
        rows = (
            await self._s.execute(
                select(OnCallRotationRow)
                .where(OnCallRotationRow.tenant_id == tenant_id)
                .order_by(OnCallRotationRow.name)
            )
        ).scalars().all()
        return [self._to_entity(r) for r in rows]

    async def delete(self, tenant_id: str, rotation_id: str) -> bool:
        result = await self._s.execute(
            delete(OnCallRotationRow).where(
                OnCallRotationRow.tenant_id == tenant_id,
                OnCallRotationRow.id == rotation_id,
            )
        )
        return bool(result.rowcount)

    @staticmethod
    def _to_entity(row: Optional[OnCallRotationRow]) -> Optional[OnCallRotation]:
        if row is None:
            return None
        return OnCallRotation(
            id=row.id, tenant_id=row.tenant_id, name=row.name,
            user_ids=tuple(row.user_ids), app_id=row.app_id,
            handoff_interval_hours=row.handoff_interval_hours,
        )


class SqlEscalationRepository(EscalationRepository):
    def __init__(self, session: AsyncSession):
        self._s = session

    async def upsert(self, policy: EscalationPolicy) -> None:
        steps = [
            {"target_type": s.target_type.value, "target_id": s.target_id, "timeout_s": s.timeout_s}
            for s in policy.steps
        ]
        row = (
            await self._s.execute(
                select(EscalationPolicyRow).where(
                    EscalationPolicyRow.tenant_id == policy.tenant_id,
                    EscalationPolicyRow.id == policy.id,
                )
            )
        ).scalars().first()
        if row is None:
            self._s.add(
                EscalationPolicyRow(
                    id=policy.id, tenant_id=policy.tenant_id, name=policy.name,
                    app_id=policy.app_id, steps=steps,
                )
            )
        else:
            row.name = policy.name
            row.app_id = policy.app_id
            row.steps = steps
        await self._s.flush()

    async def get(self, tenant_id: str, policy_id: str) -> Optional[EscalationPolicy]:
        row = (
            await self._s.execute(
                select(EscalationPolicyRow).where(
                    EscalationPolicyRow.tenant_id == tenant_id,
                    EscalationPolicyRow.id == policy_id,
                )
            )
        ).scalars().first()
        return self._to_entity(row)

    async def list(self, tenant_id: str) -> Sequence[EscalationPolicy]:
        rows = (
            await self._s.execute(
                select(EscalationPolicyRow)
                .where(EscalationPolicyRow.tenant_id == tenant_id)
                .order_by(EscalationPolicyRow.name)
            )
        ).scalars().all()
        return [self._to_entity(r) for r in rows]

    async def delete(self, tenant_id: str, policy_id: str) -> bool:
        result = await self._s.execute(
            delete(EscalationPolicyRow).where(
                EscalationPolicyRow.tenant_id == tenant_id,
                EscalationPolicyRow.id == policy_id,
            )
        )
        return bool(result.rowcount)

    @staticmethod
    def _to_entity(row: Optional[EscalationPolicyRow]) -> Optional[EscalationPolicy]:
        if row is None:
            return None
        return EscalationPolicy(
            id=row.id, tenant_id=row.tenant_id, name=row.name, app_id=row.app_id,
            steps=tuple(
                EscalationStep(
                    target_type=EscalationTargetType(s["target_type"]),
                    target_id=s["target_id"],
                    timeout_s=s["timeout_s"],
                )
                for s in row.steps
            ),
        )
