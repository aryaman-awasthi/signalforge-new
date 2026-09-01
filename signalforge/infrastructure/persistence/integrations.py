"""Stores for what the shipped adapters remember (REQ-031, REQ-033, REQ-035).

Tenant isolation (REQ-013) is inside every method here, exactly as it is for the
identity repositories: an adapter passes the tenant id it was given on its
binding and cannot widen it, because there is no method that would let it.

These four stores share one session and one `IntegrationUnitOfWork` rather than
joining the identity unit of work. An adapter call is not a use case: it must be
able to persist a ticket or a delivery receipt without holding - or being able
to commit - a transaction that also contains somebody's role bindings.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional, Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from signalforge.contracts.canonical import (
    DeliveryStatus,
    KnowledgeDocType,
    NotificationChannel,
    TicketStatus,
)
from signalforge.contracts.envelope import ActorType
from signalforge.domain.integrations.entities import (
    IndexedDocument,
    NotificationDelivery,
    StoredMessage,
    StoredTicket,
)
from signalforge.domain.integrations.ports import (
    IntegrationUnitOfWork,
    KnowledgeStore,
    MessageStore,
    NotificationStore,
    TicketStore,
)
from signalforge.infrastructure.persistence.models import (
    KnowledgeDocumentRow,
    NotificationDeliveryRow,
    SimMessageRow,
    SimTicketRow,
)


class SqlTicketStore(TicketStore):
    def __init__(self, session: AsyncSession):
        self._s = session

    async def get(
        self, tenant_id: str, app_id: str, ticket_id: str
    ) -> Optional[StoredTicket]:
        row = (
            await self._s.execute(
                select(SimTicketRow).where(
                    SimTicketRow.tenant_id == tenant_id,
                    SimTicketRow.app_id == app_id,
                    SimTicketRow.ticket_id == ticket_id,
                )
            )
        ).scalars().first()
        return _ticket(row)

    async def upsert(self, ticket: StoredTicket) -> None:
        row = (
            await self._s.execute(
                select(SimTicketRow).where(
                    SimTicketRow.tenant_id == ticket.tenant_id,
                    SimTicketRow.app_id == ticket.app_id,
                    SimTicketRow.ticket_id == ticket.ticket_id,
                )
            )
        ).scalars().first()
        if row is None:
            row = SimTicketRow(
                tenant_id=ticket.tenant_id,
                app_id=ticket.app_id,
                ticket_id=ticket.ticket_id,
                created_at=ticket.created_at,
            )
            self._s.add(row)
        row.reference = ticket.reference
        row.title = ticket.title
        row.status = ticket.status.value
        row.severity = ticket.severity
        row.assignee = ticket.assignee
        row.description = ticket.description
        row.links = list(ticket.links)
        row.updated_at = ticket.updated_at
        await self._s.flush()

    async def count(self, tenant_id: str) -> int:
        rows = (
            await self._s.execute(
                select(SimTicketRow.id).where(SimTicketRow.tenant_id == tenant_id)
            )
        ).scalars().all()
        return len(rows)

    async def list(
        self, tenant_id: str, *, app_id: Optional[str] = None, limit: int = 100
    ) -> Sequence[StoredTicket]:
        statement = select(SimTicketRow).where(SimTicketRow.tenant_id == tenant_id)
        if app_id is not None:
            statement = statement.where(SimTicketRow.app_id == app_id)
        rows = (
            await self._s.execute(
                statement.order_by(SimTicketRow.created_at.desc()).limit(limit)
            )
        ).scalars().all()
        return [_ticket(row) for row in rows]


class SqlMessageStore(MessageStore):
    def __init__(self, session: AsyncSession):
        self._s = session

    async def add(self, message: StoredMessage) -> bool:
        existing = (
            await self._s.execute(
                select(SimMessageRow.id).where(
                    SimMessageRow.tenant_id == message.tenant_id,
                    SimMessageRow.message_id == message.message_id,
                )
            )
        ).scalars().first()
        if existing is not None:
            return False
        self._s.add(
            SimMessageRow(
                tenant_id=message.tenant_id,
                app_id=message.app_id,
                channel=message.channel,
                message_id=message.message_id,
                thread_id=message.thread_id,
                author_type=message.author_type.value,
                author_id=message.author_id,
                author_display=message.author_display,
                text=message.text,
                sent_at=message.sent_at,
                scripted=message.scripted,
            )
        )
        await self._s.flush()
        return True

    async def read(
        self,
        tenant_id: str,
        channel: str,
        *,
        since: Optional[datetime],
        until: Optional[datetime],
        limit: int,
    ) -> Sequence[StoredMessage]:
        statement = select(SimMessageRow).where(
            SimMessageRow.tenant_id == tenant_id,
            SimMessageRow.channel == channel,
        )
        if since is not None:
            statement = statement.where(SimMessageRow.sent_at > since)
        if until is not None:
            # The window's upper bound is what makes a scheduled script arrive
            # in order rather than all at once: lines dated after "now" exist as
            # rows and are simply not visible yet.
            statement = statement.where(SimMessageRow.sent_at <= until)
        rows = (
            await self._s.execute(
                statement.order_by(SimMessageRow.sent_at, SimMessageRow.id).limit(limit)
            )
        ).scalars().all()
        return [_message(row) for row in rows]

    async def channel_epoch(self, tenant_id: str, channel: str) -> Optional[datetime]:
        return (
            await self._s.execute(
                select(SimMessageRow.sent_at)
                .where(
                    SimMessageRow.tenant_id == tenant_id,
                    SimMessageRow.channel == channel,
                )
                .order_by(SimMessageRow.sent_at)
                .limit(1)
            )
        ).scalars().first()


class SqlNotificationStore(NotificationStore):
    def __init__(self, session: AsyncSession):
        self._s = session

    async def get(
        self, tenant_id: str, notification_id: str
    ) -> Optional[NotificationDelivery]:
        row = (
            await self._s.execute(
                select(NotificationDeliveryRow).where(
                    NotificationDeliveryRow.tenant_id == tenant_id,
                    NotificationDeliveryRow.notification_id == notification_id,
                )
            )
        ).scalars().first()
        return _delivery(row)

    async def upsert(self, delivery: NotificationDelivery) -> None:
        row = (
            await self._s.execute(
                select(NotificationDeliveryRow).where(
                    NotificationDeliveryRow.tenant_id == delivery.tenant_id,
                    NotificationDeliveryRow.notification_id == delivery.notification_id,
                )
            )
        ).scalars().first()
        if row is None:
            row = NotificationDeliveryRow(
                tenant_id=delivery.tenant_id,
                notification_id=delivery.notification_id,
            )
            self._s.add(row)
        row.adapter_id = delivery.adapter_id
        row.channel = delivery.channel.value
        row.recipient = delivery.recipient
        row.title = delivery.title
        row.body = delivery.body
        row.status = delivery.status.value
        row.at = delivery.at
        row.app_id = delivery.app_id
        row.incident_id = delivery.incident_id
        row.requires_ack = delivery.requires_ack
        row.simulated = delivery.simulated
        row.detail = delivery.detail
        row.acked_by = delivery.acked_by
        row.acked_at = delivery.acked_at
        await self._s.flush()

    async def list_for_recipient(
        self,
        tenant_id: str,
        recipient: str,
        *,
        channels: Sequence[NotificationChannel] = (),
        open_only: bool = False,
        limit: int = 50,
    ) -> Sequence[NotificationDelivery]:
        statement = select(NotificationDeliveryRow).where(
            NotificationDeliveryRow.tenant_id == tenant_id,
            NotificationDeliveryRow.recipient == recipient,
        )
        if channels:
            statement = statement.where(
                NotificationDeliveryRow.channel.in_([c.value for c in channels])
            )
        if open_only:
            statement = statement.where(
                NotificationDeliveryRow.requires_ack.is_(True),
                NotificationDeliveryRow.status.notin_(
                    [DeliveryStatus.ACKED.value, DeliveryStatus.FAILED.value]
                ),
            )
        rows = (
            await self._s.execute(
                statement.order_by(NotificationDeliveryRow.at.desc()).limit(limit)
            )
        ).scalars().all()
        return [_delivery(row) for row in rows]


class SqlKnowledgeStore(KnowledgeStore):
    def __init__(self, session: AsyncSession):
        self._s = session

    async def upsert(self, document: IndexedDocument) -> None:
        row = (
            await self._s.execute(
                select(KnowledgeDocumentRow).where(
                    KnowledgeDocumentRow.tenant_id == document.tenant_id,
                    KnowledgeDocumentRow.doc_id == document.doc_id,
                )
            )
        ).scalars().first()
        if row is None:
            row = KnowledgeDocumentRow(
                tenant_id=document.tenant_id, doc_id=document.doc_id
            )
            self._s.add(row)
        row.app_id = document.app_id
        row.title = document.title
        row.body = document.body
        row.doc_type = document.doc_type.value
        row.tags = list(document.tags)
        row.tokens = list(document.tokens)
        row.embedding = list(document.embedding)
        row.embedding_model = document.embedding_model
        row.redaction_policy_id = document.redaction_policy_id
        row.updated_at = document.updated_at
        row.indexed_at = document.indexed_at
        await self._s.flush()

    async def get(self, tenant_id: str, doc_id: str) -> Optional[IndexedDocument]:
        row = (
            await self._s.execute(
                select(KnowledgeDocumentRow).where(
                    KnowledgeDocumentRow.tenant_id == tenant_id,
                    KnowledgeDocumentRow.doc_id == doc_id,
                )
            )
        ).scalars().first()
        return _document(row)

    async def all(
        self, tenant_id: str, *, app_id: Optional[str] = None
    ) -> Sequence[IndexedDocument]:
        statement = select(KnowledgeDocumentRow).where(
            KnowledgeDocumentRow.tenant_id == tenant_id
        )
        if app_id is not None:
            statement = statement.where(KnowledgeDocumentRow.app_id == app_id)
        rows = (
            await self._s.execute(statement.order_by(KnowledgeDocumentRow.doc_id))
        ).scalars().all()
        return [_document(row) for row in rows]


class SqlIntegrationUnitOfWork(IntegrationUnitOfWork):
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]):
        self._session_factory = session_factory

    async def __aenter__(self) -> "SqlIntegrationUnitOfWork":
        self._session = self._session_factory()
        self.tickets = SqlTicketStore(self._session)
        self.messages = SqlMessageStore(self._session)
        self.notifications = SqlNotificationStore(self._session)
        self.knowledge = SqlKnowledgeStore(self._session)
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if exc_type is not None:
            await self._session.rollback()
        await self._session.close()

    async def commit(self) -> None:
        await self._session.commit()


# --- row <-> entity -----------------------------------------------------------


def _ticket(row: Optional[SimTicketRow]) -> Optional[StoredTicket]:
    if row is None:
        return None
    return StoredTicket(
        tenant_id=row.tenant_id,
        app_id=row.app_id,
        ticket_id=row.ticket_id,
        reference=row.reference,
        title=row.title,
        status=TicketStatus(row.status),
        created_at=row.created_at,
        updated_at=row.updated_at,
        description=row.description,
        severity=row.severity,
        assignee=row.assignee,
        links=tuple(row.links or ()),
    )


def _message(row: Optional[SimMessageRow]) -> Optional[StoredMessage]:
    if row is None:
        return None
    return StoredMessage(
        tenant_id=row.tenant_id,
        app_id=row.app_id,
        channel=row.channel,
        message_id=row.message_id,
        author_type=ActorType(row.author_type),
        author_id=row.author_id,
        author_display=row.author_display,
        text=row.text,
        sent_at=row.sent_at,
        thread_id=row.thread_id,
        scripted=row.scripted,
    )


def _delivery(row: Optional[NotificationDeliveryRow]) -> Optional[NotificationDelivery]:
    if row is None:
        return None
    return NotificationDelivery(
        tenant_id=row.tenant_id,
        notification_id=row.notification_id,
        adapter_id=row.adapter_id,
        channel=NotificationChannel(row.channel),
        recipient=row.recipient,
        title=row.title,
        body=row.body,
        status=DeliveryStatus(row.status),
        at=row.at,
        app_id=row.app_id,
        incident_id=row.incident_id,
        requires_ack=row.requires_ack,
        simulated=row.simulated,
        detail=row.detail,
        acked_by=row.acked_by,
        acked_at=row.acked_at,
    )


def _document(row: Optional[KnowledgeDocumentRow]) -> Optional[IndexedDocument]:
    if row is None:
        return None
    return IndexedDocument(
        tenant_id=row.tenant_id,
        doc_id=row.doc_id,
        title=row.title,
        body=row.body,
        doc_type=KnowledgeDocType(row.doc_type),
        updated_at=row.updated_at,
        indexed_at=row.indexed_at,
        tokens=tuple(row.tokens or ()),
        tags=tuple(row.tags or ()),
        app_id=row.app_id,
        embedding=tuple(row.embedding or ()),
        embedding_model=row.embedding_model,
        redaction_policy_id=row.redaction_policy_id,
    )
