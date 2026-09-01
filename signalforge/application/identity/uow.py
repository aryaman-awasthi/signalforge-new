"""Unit of work over the identity ports. The application layer sees only the
domain abstractions; infrastructure supplies an implementation whose repos all
share one transaction."""

from __future__ import annotations

from abc import ABC, abstractmethod

from signalforge.domain.adapters.ports import ConnectorBindingRepository
from signalforge.domain.brain.ports import (
    AgentRunRepository,
    ConfidenceHistoryRepository,
    IncidentGraphRepository,
    SituationSnapshotRepository,
)
from signalforge.domain.events.ports import EventStore
from signalforge.domain.governance.ports import (
    ApprovalRepository,
    CommunicationRepository,
    ExecutionLedgerRepository,
    PolicyRepository,
    ResponsePlanRepository,
    ValidationRepository,
)
from signalforge.domain.incidents.ports import (
    ChangeWindowStore,
    IncidentRepository,
    IncidentTransitionRepository,
)
from signalforge.domain.ingest.ports import IngestKeyRepository, IngestReceiptStore
from signalforge.domain.warroom.ports import (
    IncidentTaskRepository,
    PageRepository,
    RoomMemberRepository,
    WarRoomRepository,
)
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
    war_rooms: WarRoomRepository
    room_members: RoomMemberRepository
    incident_pages: PageRepository
    incident_tasks: IncidentTaskRepository
    """The war room (S4-T03). On this unit of work because every act in a room is
    one commit of the row, its audit entry and its incident-plane event -
    acknowledging a page in particular, where the receipt, the audit chain and
    the escalation timer must never be able to disagree about whether somebody
    answered. `incident_pages` sits beside `notifications` for the same reason:
    a page links to the delivery receipts it produced, and a link written without
    its receipt would be a countdown nobody could stop."""
    incident_graph: IncidentGraphRepository
    confidence_history: ConfidenceHistoryRepository
    situation_snapshots: SituationSnapshotRepository
    agent_runs: AgentRunRepository
    """The incident brain (S5-T01). On this unit of work because one governor
    pass is one commit of four things that must never disagree: the graph node
    version, the confidence-history row that explains why it moved, the audit
    entry and the incident-plane event. A confidence that moved without its
    history row would be exactly the unexplained number this sprint exists to
    make impossible."""
    policies: PolicyRepository
    executions: ExecutionLedgerRepository
    approvals: ApprovalRepository
    response_plans: ResponsePlanRepository
    communications: CommunicationRepository
    validations: ValidationRepository
    """Governance and execution (S6). On this unit of work because one pass
    through the Tool Gateway is one commit of things that must never disagree:
    the ledger row, the approval it spent, the audit entry naming the policy
    version that authorised it and the incident-plane event. A ledger row that
    committed without its audit entry would be a production change nobody
    performed; an approval marked spent without the execution it was spent on
    would be an approval available twice."""
    audit: AuditWriter
    events: EventStore
    """The outbox: an event published here shares the use case's transaction."""

    @abstractmethod
    async def __aenter__(self) -> "IdentityUnitOfWork": ...

    @abstractmethod
    async def __aexit__(self, exc_type, exc, tb) -> None: ...

    @abstractmethod
    async def commit(self) -> None: ...
