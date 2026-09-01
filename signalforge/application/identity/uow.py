"""Unit of work over the identity ports. The application layer sees only the
domain abstractions; infrastructure supplies an implementation whose repos all
share one transaction."""

from __future__ import annotations

from abc import ABC, abstractmethod

from signalforge.domain.adapters.ports import ConnectorBindingRepository
from signalforge.domain.events.ports import EventStore
from signalforge.domain.ingest.ports import IngestKeyRepository, IngestReceiptStore
from signalforge.domain.integrations.ports import NotificationStore
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
    connectors: ConnectorBindingRepository
    """Adapter bindings (S3). They live on this unit of work because a binding,
    its Fernet-encrypted credentials and its audit entry have to be one commit:
    a stored credential with no binding is unreachable, and a binding with no
    credential is a connector that silently cannot authenticate."""
    notifications: NotificationStore
    """Delivery receipts (S3). Here as well as on the adapters' own unit of work
    because acknowledging a page is a human act on the record: the receipt, its
    audit entry and its event have to be one commit, or the audit chain can
    disagree with what the escalation timer is reading."""
    ingest_keys: IngestKeyRepository
    ingest_receipts: IngestReceiptStore
    """Push ingestion (S3). On this unit of work because accepting a batch is
    one commit of four things: the idempotency receipts, the events they became,
    the counters on the key and the audit entry for anything refused. A receipt
    written without its event would silently swallow the sender's retry."""
    audit: AuditWriter
    events: EventStore
    """The outbox: an event published here shares the use case's transaction."""

    @abstractmethod
    async def __aenter__(self) -> "IdentityUnitOfWork": ...

    @abstractmethod
    async def __aexit__(self, exc_type, exc, tb) -> None: ...

    @abstractmethod
    async def commit(self) -> None: ...
