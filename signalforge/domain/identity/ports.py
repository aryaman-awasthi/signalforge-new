"""Identity ports: what the domain needs from the outside world (CLAUDE.md rule 1).

Infrastructure implements these; the application layer depends only on the
abstractions. Every repository method that touches tenant data takes tenant_id
and must filter by it inside the repository (REQ-013) - handlers are never
trusted to scope queries.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any, Optional, Sequence

from signalforge.domain.identity.entities import (
    Invite,
    RoleBinding,
    Session,
    Tenant,
    User,
)
from signalforge.domain.identity.oncall import EscalationPolicy, OnCallRotation


class TenantRepository(ABC):
    @abstractmethod
    async def add(self, tenant: Tenant) -> None: ...

    @abstractmethod
    async def get(self, tenant_id: str) -> Optional[Tenant]: ...

    @abstractmethod
    async def get_by_slug(self, slug: str) -> Optional[Tenant]: ...

    @abstractmethod
    async def update(self, tenant: Tenant) -> None: ...


class UserRepository(ABC):
    @abstractmethod
    async def add(self, user: User) -> None: ...

    @abstractmethod
    async def get(self, tenant_id: str, user_id: str) -> Optional[User]: ...

    @abstractmethod
    async def get_by_email(self, tenant_id: str, email: str) -> Optional[User]: ...

    @abstractmethod
    async def list(self, tenant_id: str) -> Sequence[User]: ...

    @abstractmethod
    async def update(self, user: User) -> None: ...


class RoleBindingRepository(ABC):
    @abstractmethod
    async def list_for_user(self, tenant_id: str, user_id: str) -> Sequence[RoleBinding]: ...

    @abstractmethod
    async def list_for_tenant(self, tenant_id: str) -> Sequence[RoleBinding]: ...

    @abstractmethod
    async def replace_for_user(
        self, tenant_id: str, user_id: str, bindings: Sequence[RoleBinding]
    ) -> None: ...


class InviteRepository(ABC):
    @abstractmethod
    async def add(self, invite: Invite) -> None: ...

    @abstractmethod
    async def get(self, tenant_id: str, invite_id: str) -> Optional[Invite]: ...

    @abstractmethod
    async def get_by_token_hash(self, token_hash: str) -> Optional[Invite]: ...

    @abstractmethod
    async def list(self, tenant_id: str) -> Sequence[Invite]: ...

    @abstractmethod
    async def update(self, invite: Invite) -> None: ...


class SessionRepository(ABC):
    @abstractmethod
    async def add(self, session: Session) -> None: ...

    @abstractmethod
    async def get(self, tenant_id: str, session_id: str) -> Optional[Session]: ...

    @abstractmethod
    async def get_by_token_hash(self, token_hash: str) -> Optional[Session]: ...

    @abstractmethod
    async def update(self, session: Session) -> None: ...

    @abstractmethod
    async def revoke_family(
        self, tenant_id: str, family_id: str, reason: str, at: datetime
    ) -> int: ...


class OnCallRepository(ABC):
    @abstractmethod
    async def upsert(self, rotation: OnCallRotation) -> None: ...

    @abstractmethod
    async def get(self, tenant_id: str, rotation_id: str) -> Optional[OnCallRotation]: ...

    @abstractmethod
    async def list(self, tenant_id: str) -> Sequence[OnCallRotation]: ...

    @abstractmethod
    async def delete(self, tenant_id: str, rotation_id: str) -> bool: ...


class EscalationRepository(ABC):
    @abstractmethod
    async def upsert(self, policy: EscalationPolicy) -> None: ...

    @abstractmethod
    async def get(self, tenant_id: str, policy_id: str) -> Optional[EscalationPolicy]: ...

    @abstractmethod
    async def list(self, tenant_id: str) -> Sequence[EscalationPolicy]: ...

    @abstractmethod
    async def delete(self, tenant_id: str, policy_id: str) -> bool: ...


class PasswordHasher(ABC):
    @abstractmethod
    def hash(self, password: str) -> str: ...

    @abstractmethod
    def verify(self, password: str, password_hash: str) -> bool: ...


class AccessTokenService(ABC):
    """Issues and verifies the short-lived access token referencing a session."""

    @abstractmethod
    def issue(self, *, user_id: str, tenant_id: str, session_id: str) -> str: ...

    @abstractmethod
    def verify(self, token: str) -> dict[str, Any]:
        """Returns the claims or raises AuthenticationError."""


class SecretStore(ABC):
    """Tenant-scoped encrypted secret storage (REQ-015). Values are encrypted
    at rest and never logged; list operations return names only."""

    @abstractmethod
    async def put(self, tenant_id: str, name: str, value: str) -> None: ...

    @abstractmethod
    async def get(self, tenant_id: str, name: str) -> Optional[str]: ...

    @abstractmethod
    async def list_names(self, tenant_id: str) -> Sequence[str]: ...

    @abstractmethod
    async def delete(self, tenant_id: str, name: str) -> bool: ...


class AuditWriter(ABC):
    """Append-only hash-chained audit log (REQ-016). There is deliberately no
    way to disable it: the policy floor makes audit non-optional."""

    @abstractmethod
    async def append(
        self,
        *,
        tenant_id: str,
        topic: str,
        actor_type: str,
        actor_id: str,
        payload: dict[str, Any],
        app_id: Optional[str] = None,
    ) -> None: ...


class RateLimiter(ABC):
    @abstractmethod
    def hit(self, key: str) -> None:
        """Record an attempt; raises RateLimitedError when the window is full."""

    @abstractmethod
    def reset(self, key: str) -> None: ...


class Clock(ABC):
    @abstractmethod
    def now(self) -> datetime: ...
