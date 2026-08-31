"""Unit of work over the identity ports. The application layer sees only the
domain abstractions; infrastructure supplies an implementation whose repos all
share one transaction."""

from __future__ import annotations

from abc import ABC, abstractmethod

from signalforge.domain.events.ports import EventStore
from signalforge.domain.identity.ports import (
    AuditWriter,
    EscalationRepository,
    InviteRepository,
    OnCallRepository,
    RoleBindingRepository,
    SecretStore,
    SessionRepository,
    TenantRepository,
    UserRepository,
)


class IdentityUnitOfWork(ABC):
    tenants: TenantRepository
    users: UserRepository
    bindings: RoleBindingRepository
    invites: InviteRepository
    sessions: SessionRepository
    oncall: OnCallRepository
    escalations: EscalationRepository
    secrets: SecretStore
    audit: AuditWriter
    events: EventStore
    """The outbox: an event published here shares the use case's transaction."""

    @abstractmethod
    async def __aenter__(self) -> "IdentityUnitOfWork": ...

    @abstractmethod
    async def __aexit__(self, exc_type, exc, tb) -> None: ...

    @abstractmethod
    async def commit(self) -> None: ...
