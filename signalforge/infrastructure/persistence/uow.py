"""SQL unit of work: one AsyncSession shared by every repository, the secret
store, the audit writer and the event outbox, so a use case commits atomically
or not at all. The outbox lives on this unit of work on purpose (REQ-020): a use
case that changes state publishes its event through the same transaction, so the
two can never disagree."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from signalforge.application.identity.uow import IdentityUnitOfWork
from signalforge.domain.identity.ports import Clock
from signalforge.infrastructure.persistence.repositories import (
    SqlEscalationRepository,
    SqlInviteRepository,
    SqlOnCallRepository,
    SqlRoleBindingRepository,
    SqlSessionRepository,
    SqlTenantRepository,
    SqlUserRepository,
)
from signalforge.infrastructure.persistence.connectors import (
    SqlConnectorBindingRepository,
)
from signalforge.infrastructure.persistence.event_store import SqlEventStore
from signalforge.infrastructure.persistence.incidents import (
    SqlChangeWindowStore,
    SqlIncidentRepository,
    SqlIncidentTransitionRepository,
)
from signalforge.infrastructure.persistence.ingest import (
    SqlIngestKeyRepository,
    SqlIngestReceiptStore,
)
from signalforge.infrastructure.persistence.integrations import SqlNotificationStore
from signalforge.infrastructure.persistence.onboarding import (
    SqlApplicationRepository,
    SqlOnboardingProfileRepository,
    SqlTopologyVersionRepository,
)
from signalforge.infrastructure.security.audit import AuditChainWriter
from signalforge.infrastructure.security.secrets import FernetSecretStore


class SqlIdentityUnitOfWork(IdentityUnitOfWork):
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        fernet_key: str,
        clock: Clock,
    ):
        self._session_factory = session_factory
        self._fernet_key = fernet_key
        self._clock = clock

    async def __aenter__(self) -> "SqlIdentityUnitOfWork":
        self._session = self._session_factory()
        self.tenants = SqlTenantRepository(self._session)
        self.users = SqlUserRepository(self._session)
        self.bindings = SqlRoleBindingRepository(self._session)
        self.invites = SqlInviteRepository(self._session)
        self.sessions = SqlSessionRepository(self._session)
        self.oncall = SqlOnCallRepository(self._session)
        self.escalations = SqlEscalationRepository(self._session)
        self.secrets = FernetSecretStore(self._session, key=self._fernet_key)
        self.connectors = SqlConnectorBindingRepository(self._session)
        self.notifications = SqlNotificationStore(self._session)
        self.ingest_keys = SqlIngestKeyRepository(self._session)
        self.ingest_receipts = SqlIngestReceiptStore(self._session)
        self.applications = SqlApplicationRepository(self._session)
        self.onboarding = SqlOnboardingProfileRepository(self._session)
        self.topologies = SqlTopologyVersionRepository(self._session)
        self.incidents = SqlIncidentRepository(self._session)
        self.incident_transitions = SqlIncidentTransitionRepository(self._session)
        self.changes = SqlChangeWindowStore(self._session)
        self.audit = AuditChainWriter(self._session, clock=self._clock)
        self.events = SqlEventStore(self._session, clock=self._clock)
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if exc_type is not None:
            await self._session.rollback()
        await self._session.close()

    async def commit(self) -> None:
        await self._session.commit()
