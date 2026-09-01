"""Unit of work over the identity ports. The application layer sees only the
domain abstractions; infrastructure supplies an implementation whose repos all
share one transaction."""

from __future__ import annotations

from abc import ABC, abstractmethod

from signalforge.domain.adapters.ports import ConnectorBindingRepository
from signalforge.domain.events.ports import EventStore
from signalforge.domain.incidents.ports import (
    ChangeWindowStore,
    IncidentRepository,
    IncidentTransitionRepository,
)
from signalforge.domain.ingest.ports import IngestKeyRepository, IngestReceiptStore
from signalforge.domain.integrations.ports import NotificationStore
from signalforge.domain.onboarding.ports import (
    ApplicationRepository,
    OnboardingProfileRepository,
    TopologyVersionRepository,
)
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
    applications: ApplicationRepository
    onboarding: OnboardingProfileRepository
    topologies: TopologyVersionRepository
    """Onboarding (S3-T04). Here because every wizard step is one commit of the
    change, its audit entry and its event: a profile that saved while its audit
    entry did not would make the timeline disagree with what the platform is
    actually watching, and a topology version stored without its event would be
    invisible to replay."""
    incidents: IncidentRepository
    incident_transitions: IncidentTransitionRepository
    """The incident lifecycle (S4-T02). Here because declaring, re-grading and
    closing are each one commit of four things: the incident row, the transition
    that records who moved it, the audit entry and the incident-plane event. An
    incident whose transition did not commit would be a state change nobody
    performed."""
    changes: ChangeWindowStore
    """The change-window projection (S4-T02). Reachable from this unit of work
    for reads and from the event one for writes, exactly as delivery receipts
    are: the bus consumer folds a deployment event inside the dispatcher's
    transaction, and a screen reads it without joining that transaction."""
    audit: AuditWriter
    events: EventStore
    """The outbox: an event published here shares the use case's transaction."""

    @abstractmethod
    async def __aenter__(self) -> "IdentityUnitOfWork": ...

    @abstractmethod
    async def __aexit__(self, exc_type, exc, tb) -> None: ...

    @abstractmethod
    async def commit(self) -> None: ...
