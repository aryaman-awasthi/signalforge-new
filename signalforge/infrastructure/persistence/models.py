"""SQLAlchemy models for identity, secrets and the audit chain (S1) and for the
event backbone: outbox, consumer offsets, dead letters and seals (S2).

SQLite WAL today, Postgres-ready: only portable column types, JSON for small
value-object payloads, timestamps stored UTC via TZDateTime so tz-awareness
survives the SQLite roundtrip.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    TypeDecorator,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class TZDateTime(TypeDecorator):
    """Store UTC; re-attach tzinfo on load (SQLite drops it)."""

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value: Optional[datetime], dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("timestamps must be timezone-aware")
        return value.astimezone(timezone.utc).replace(tzinfo=None)

    def process_result_value(self, value: Optional[datetime], dialect):
        return value.replace(tzinfo=timezone.utc) if value is not None else None


class Base(DeclarativeBase):
    pass


class TenantRow(Base):
    __tablename__ = "tenants"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    slug: Mapped[str] = mapped_column(String(100), unique=True)
    created_at: Mapped[datetime] = mapped_column(TZDateTime)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class UserRow(Base):
    __tablename__ = "users"
    __table_args__ = (UniqueConstraint("tenant_id", "email"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    email: Mapped[str] = mapped_column(String(320))
    display_name: Mapped[str] = mapped_column(String(200))
    password_hash: Mapped[str] = mapped_column(String(300))
    created_at: Mapped[datetime] = mapped_column(TZDateTime)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class RoleBindingRow(Base):
    __tablename__ = "role_bindings"
    __table_args__ = (
        UniqueConstraint("tenant_id", "user_id", "role", "app_id"),
        Index("ix_role_bindings_tenant_user", "tenant_id", "user_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    role: Mapped[str] = mapped_column(String(50))
    app_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)


class InviteRow(Base):
    __tablename__ = "invites"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    email: Mapped[str] = mapped_column(String(320))
    roles: Mapped[list] = mapped_column(JSON)
    app_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_by: Mapped[str] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(TZDateTime)
    expires_at: Mapped[datetime] = mapped_column(TZDateTime)
    accepted_at: Mapped[Optional[datetime]] = mapped_column(TZDateTime, nullable=True)
    revoked_at: Mapped[Optional[datetime]] = mapped_column(TZDateTime, nullable=True)


class SessionRow(Base):
    __tablename__ = "sessions"
    __table_args__ = (Index("ix_sessions_tenant_family", "tenant_id", "family_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    family_id: Mapped[str] = mapped_column(String(36))
    refresh_token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    prev_token_hash: Mapped[Optional[str]] = mapped_column(
        String(64), nullable=True, index=True
    )
    issued_at: Mapped[datetime] = mapped_column(TZDateTime)
    expires_at: Mapped[datetime] = mapped_column(TZDateTime)
    rotated_at: Mapped[Optional[datetime]] = mapped_column(TZDateTime, nullable=True)
    revoked_at: Mapped[Optional[datetime]] = mapped_column(TZDateTime, nullable=True)
    revoked_reason: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)


class OnCallRotationRow(Base):
    __tablename__ = "oncall_rotations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    app_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    user_ids: Mapped[list] = mapped_column(JSON)
    handoff_interval_hours: Mapped[int] = mapped_column(Integer, default=168)


class EscalationPolicyRow(Base):
    __tablename__ = "escalation_policies"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    app_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    steps: Mapped[list] = mapped_column(JSON)


class SecretRow(Base):
    __tablename__ = "secrets"

    tenant_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(200), primary_key=True)
    ciphertext: Mapped[bytes] = mapped_column(LargeBinary)
    created_at: Mapped[datetime] = mapped_column(TZDateTime)
    updated_at: Mapped[datetime] = mapped_column(TZDateTime)


class AuditLogRow(Base):
    __tablename__ = "audit_log"
    __table_args__ = (Index("ix_audit_log_tenant_seq", "tenant_id", "seq"),)

    seq: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(36))
    event_id: Mapped[str] = mapped_column(String(36), unique=True)
    topic: Mapped[str] = mapped_column(String(200))
    actor_type: Mapped[str] = mapped_column(String(20))
    actor_id: Mapped[str] = mapped_column(String(200))
    app_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(TZDateTime)
    payload: Mapped[str] = mapped_column(Text)
    payload_hash: Mapped[str] = mapped_column(String(64))
    prev_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)


# --- event backbone (S2) -----------------------------------------------------


class EventRow(Base):
    """The outbox and the event log in one table: seq is the global recording
    order, envelope holds the full frozen contract shape, and the columns beside
    it exist only so replay and dispatch can filter without parsing JSON."""

    __tablename__ = "events"
    __table_args__ = (
        Index("ix_events_tenant_seq", "tenant_id", "seq"),
        Index("ix_events_chain", "tenant_id", "plane", "seq"),
        Index("ix_events_lane_seq", "lane", "seq"),
        Index("ix_events_incident", "tenant_id", "incident_id", "seq"),
        Index("ix_events_topic", "tenant_id", "topic", "seq"),
        Index("ix_events_causation", "causation_id"),
    )

    seq: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_id: Mapped[str] = mapped_column(String(36), unique=True)
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    topic: Mapped[str] = mapped_column(String(200))
    app_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    incident_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    plane: Mapped[str] = mapped_column(String(20))
    partition_key: Mapped[str] = mapped_column(String(200))
    lane: Mapped[int] = mapped_column(Integer)
    occurred_at: Mapped[datetime] = mapped_column(TZDateTime)
    recorded_at: Mapped[datetime] = mapped_column(TZDateTime)
    actor_type: Mapped[str] = mapped_column(String(20))
    actor_id: Mapped[str] = mapped_column(String(200))
    correlation_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    causation_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    idempotency_key: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    envelope: Mapped[str] = mapped_column(Text)
    payload_hash: Mapped[str] = mapped_column(String(64))
    prev_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)


class ConsumerOffsetRow(Base):
    """One row per (group, lane). "group" is reserved in SQL, hence group_name."""

    __tablename__ = "consumer_offsets"

    group_name: Mapped[str] = mapped_column(String(100), primary_key=True)
    lane: Mapped[int] = mapped_column(Integer, primary_key=True)
    last_seq: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(TZDateTime)


class ConsumerProcessedRow(Base):
    """The idempotency ledger that makes at-least-once delivery harmless."""

    __tablename__ = "consumer_processed"

    group_name: Mapped[str] = mapped_column(String(100), primary_key=True)
    event_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    processed_at: Mapped[datetime] = mapped_column(TZDateTime)


class DeadLetterRow(Base):
    __tablename__ = "dead_letters"
    __table_args__ = (
        Index("ix_dead_letters_tenant_state", "tenant_id", "state"),
        Index("ix_dead_letters_group_state", "group_name", "state"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    group_name: Mapped[str] = mapped_column(String(100))
    event_seq: Mapped[int] = mapped_column(Integer)
    event_id: Mapped[str] = mapped_column(String(36))
    tenant_id: Mapped[str] = mapped_column(String(36))
    topic: Mapped[str] = mapped_column(String(200))
    partition_key: Mapped[str] = mapped_column(String(200))
    lane: Mapped[int] = mapped_column(Integer)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str] = mapped_column(Text)
    state: Mapped[str] = mapped_column(String(20))
    first_failed_at: Mapped[datetime] = mapped_column(TZDateTime)
    last_failed_at: Mapped[datetime] = mapped_column(TZDateTime)
    requeued_at: Mapped[Optional[datetime]] = mapped_column(TZDateTime, nullable=True)
    requeued_by: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    resolved_at: Mapped[Optional[datetime]] = mapped_column(TZDateTime, nullable=True)


class AgentRunRow(Base):
    """One agent invocation. Empty until the agent suite lands in S5; the table
    exists now so agents are born observable rather than instrumented later."""

    __tablename__ = "agent_runs"
    __table_args__ = (
        Index("ix_agent_runs_tenant_started", "tenant_id", "started_at"),
        Index("ix_agent_runs_incident", "tenant_id", "incident_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    agent: Mapped[str] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(20))
    incident_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    trigger_topic: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    trigger_event_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    started_at: Mapped[datetime] = mapped_column(TZDateTime)
    finished_at: Mapped[Optional[datetime]] = mapped_column(TZDateTime, nullable=True)
    model: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


class AgentRunStepRow(Base):
    __tablename__ = "agent_run_steps"
    __table_args__ = (Index("ix_agent_run_steps_run", "run_id", "seq"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("agent_runs.id"), index=True)
    seq: Mapped[int] = mapped_column(Integer)
    step: Mapped[str] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(20))
    started_at: Mapped[datetime] = mapped_column(TZDateTime)
    finished_at: Mapped[Optional[datetime]] = mapped_column(TZDateTime, nullable=True)
    tokens: Mapped[int] = mapped_column(Integer, default=0)
    detail: Mapped[dict] = mapped_column(JSON, default=dict)


class RetentionWatermarkRow(Base):
    """What retention removed from a (tenant, plane) chain, and the hash the
    surviving events verify against."""

    __tablename__ = "retention_watermarks"

    tenant_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    plane: Mapped[str] = mapped_column(String(20), primary_key=True)
    pruned_through_seq: Mapped[int] = mapped_column(Integer)
    anchor_hash: Mapped[str] = mapped_column(String(64))
    pruned_count: Mapped[int] = mapped_column(Integer, default=0)
    pruned_at: Mapped[datetime] = mapped_column(TZDateTime)


# --- adapter registry (S3) ---------------------------------------------------


class ConnectorBindingRow(Base):
    """One application wired to one adapter at one credential scope.

    No credential is stored here: `credential_fields` names them and the values
    live Fernet-encrypted in `secrets` under connector/<id>/<field> (REQ-015),
    so this row can be listed and rendered without a decrypt. The health columns
    are written by the connector health loop and are what the integration health
    screen reads for freshness and last error.
    """

    __tablename__ = "connector_bindings"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "app_id", "adapter_id", "credential_scope",
            name="uq_connector_bindings_scope",
        ),
        Index("ix_connector_bindings_tenant_app", "tenant_id", "app_id"),
        Index("ix_connector_bindings_enabled", "enabled"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    app_id: Mapped[str] = mapped_column(String(100))
    adapter_id: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(200))
    credential_scope: Mapped[str] = mapped_column(String(20))
    config: Mapped[dict] = mapped_column(JSON, default=dict)
    credential_fields: Mapped[list] = mapped_column(JSON, default=list)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(TZDateTime)
    created_by: Mapped[str] = mapped_column(String(36))
    updated_at: Mapped[datetime] = mapped_column(TZDateTime)
    status: Mapped[str] = mapped_column(String(20), default="unknown")
    status_detail: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    last_checked_at: Mapped[Optional[datetime]] = mapped_column(TZDateTime, nullable=True)
    last_ok_at: Mapped[Optional[datetime]] = mapped_column(TZDateTime, nullable=True)
    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0)


class IngestKeyRow(Base):
    """One sending application's credential for one ingest stream (REQ-032).

    Neither half of the credential is readable here: `token_hash` is the sha256
    of the bearer token (the plaintext is shown once, at issue, and cannot be
    shown again), and the HMAC shared secret lives Fernet-encrypted in `secrets`
    under `ingest/<key_id>/secret`. `token_prefix` exists so an operator can
    tell two keys apart on screen without the platform being able to reveal
    either.

    The counters and `last_event_at` are the freshness a push binding cannot
    measure by pinging: SignalForge is the server, so it is always reachable
    even if the sender died a week ago.
    """

    __tablename__ = "ingest_keys"
    __table_args__ = (
        Index("ix_ingest_keys_tenant_app", "tenant_id", "app_id", "stream"),
        Index("ix_ingest_keys_binding", "tenant_id", "binding_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    app_id: Mapped[str] = mapped_column(String(100))
    stream: Mapped[str] = mapped_column(String(20))
    name: Mapped[str] = mapped_column(String(200))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    token_prefix: Mapped[str] = mapped_column(String(20))
    binding_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    rotated_from: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(TZDateTime)
    created_by: Mapped[str] = mapped_column(String(36))
    revoked_at: Mapped[Optional[datetime]] = mapped_column(TZDateTime, nullable=True)
    revoked_by: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    last_used_at: Mapped[Optional[datetime]] = mapped_column(TZDateTime, nullable=True)
    last_event_at: Mapped[Optional[datetime]] = mapped_column(TZDateTime, nullable=True)
    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    accepted_count: Mapped[int] = mapped_column(Integer, default=0)
    duplicate_count: Mapped[int] = mapped_column(Integer, default=0)
    rejected_count: Mapped[int] = mapped_column(Integer, default=0)


class IngestReceiptRow(Base):
    """Proof that this tenant has already recorded this producer `event_id`.

    The unique key is `(tenant_id, event_id)`, which is what makes a retry after
    a lost 202 harmless and what keeps two tenants that picked the same
    producer-assigned id from blocking or observing each other.
    """

    __tablename__ = "ingest_receipts"
    __table_args__ = (
        UniqueConstraint("tenant_id", "event_id", name="uq_ingest_receipts_event"),
        Index("ix_ingest_receipts_key", "tenant_id", "key_id", "received_at"),
        Index("ix_ingest_receipts_stream", "tenant_id", "stream", "received_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    event_id: Mapped[str] = mapped_column(String(36))
    key_id: Mapped[str] = mapped_column(String(36))
    app_id: Mapped[str] = mapped_column(String(100))
    stream: Mapped[str] = mapped_column(String(20))
    source_system: Mapped[str] = mapped_column(String(100))
    occurred_at: Mapped[datetime] = mapped_column(TZDateTime)
    received_at: Mapped[datetime] = mapped_column(TZDateTime)
    platform_event_id: Mapped[str] = mapped_column(String(36))
    event_seq: Mapped[int] = mapped_column(Integer)
    event_count: Mapped[int] = mapped_column(Integer, default=1)
    simulated: Mapped[bool] = mapped_column(Boolean, default=False)
    normalized_by: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)


class SimTicketRow(Base):
    """A ticket in the built-in lightweight ITSM (itsm-lite).

    This is the *integrated system's* data, not SignalForge's: it exists so an
    installation without ServiceNow can still rehearse ticket sync end to end.
    Durable on purpose - a simulated pager or ticket queue that forgets on
    restart cannot be used to rehearse anything.
    """

    __tablename__ = "sim_tickets"
    __table_args__ = (
        UniqueConstraint("tenant_id", "app_id", "ticket_id", name="uq_sim_tickets_id"),
        Index("ix_sim_tickets_tenant_app", "tenant_id", "app_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    app_id: Mapped[str] = mapped_column(String(100))
    ticket_id: Mapped[str] = mapped_column(String(100))
    reference: Mapped[str] = mapped_column(String(50))
    title: Mapped[str] = mapped_column(String(300))
    status: Mapped[str] = mapped_column(String(20))
    severity: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    assignee: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    links: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(TZDateTime)
    updated_at: Mapped[datetime] = mapped_column(TZDateTime)


class SimMessageRow(Base):
    """One message in a simulated collaboration channel (teams-sim).

    `scripted` tells a drill's rehearsed conversation apart from what a human
    typed into the bridge. `sent_at` may be in the future: the script player
    materialises a whole conversation at its scheduled times and reads window by
    time, which is how a script follows the drill clock without a timer.
    """

    __tablename__ = "sim_messages"
    __table_args__ = (
        UniqueConstraint("tenant_id", "message_id", name="uq_sim_messages_id"),
        Index("ix_sim_messages_channel_time", "tenant_id", "channel", "sent_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    app_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    channel: Mapped[str] = mapped_column(String(200))
    message_id: Mapped[str] = mapped_column(String(100))
    thread_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    author_type: Mapped[str] = mapped_column(String(20))
    author_id: Mapped[str] = mapped_column(String(200))
    author_display: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    text: Mapped[str] = mapped_column(Text)
    sent_at: Mapped[datetime] = mapped_column(TZDateTime)
    scripted: Mapped[bool] = mapped_column(Boolean, default=False)


class NotificationDeliveryRow(Base):
    """The receipt for one notification on one channel.

    Every notification adapter writes here - in-app, browser push, the simulated
    pager and the simulated DM - so "was this person told, and did they say they
    had it" is one query regardless of transport, and so a cockpit that
    reconnects can be caught up on what is still waiting for an acknowledgement.
    """

    __tablename__ = "notification_deliveries"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "notification_id", name="uq_notification_deliveries_id"
        ),
        Index("ix_notification_deliveries_recipient", "tenant_id", "recipient", "at"),
        Index("ix_notification_deliveries_incident", "tenant_id", "incident_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    notification_id: Mapped[str] = mapped_column(String(100))
    adapter_id: Mapped[str] = mapped_column(String(64))
    channel: Mapped[str] = mapped_column(String(20))
    recipient: Mapped[str] = mapped_column(String(200))
    title: Mapped[str] = mapped_column(String(300))
    body: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20))
    at: Mapped[datetime] = mapped_column(TZDateTime)
    app_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    incident_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    requires_ack: Mapped[bool] = mapped_column(Boolean, default=False)
    simulated: Mapped[bool] = mapped_column(Boolean, default=False)
    detail: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    acked_by: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    acked_at: Mapped[Optional[datetime]] = mapped_column(TZDateTime, nullable=True)


class KnowledgeDocumentRow(Base):
    """A runbook, PIR or note in the built-in knowledge index (kb-index).

    `tokens` is the tokenised body BM25 ranks against, stored rather than
    recomputed so a search costs one read. `embedding` is present only when an
    embedding provider was reachable at index time; its absence is exactly what
    makes the search fall back to lexical ranking rather than fail.
    """

    __tablename__ = "knowledge_documents"
    __table_args__ = (
        UniqueConstraint("tenant_id", "doc_id", name="uq_knowledge_documents_id"),
        Index("ix_knowledge_documents_tenant_app", "tenant_id", "app_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    doc_id: Mapped[str] = mapped_column(String(100))
    app_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    title: Mapped[str] = mapped_column(String(300))
    body: Mapped[str] = mapped_column(Text)
    doc_type: Mapped[str] = mapped_column(String(20))
    tags: Mapped[list] = mapped_column(JSON, default=list)
    tokens: Mapped[list] = mapped_column(JSON, default=list)
    embedding: Mapped[list] = mapped_column(JSON, default=list)
    embedding_model: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    redaction_policy_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(TZDateTime)
    indexed_at: Mapped[datetime] = mapped_column(TZDateTime)


class ApplicationRow(Base):
    """An onboarded application (S3-T04).

    `app_id` is not invented here: it is the tenant-scoped slug role bindings,
    connector bindings, ingest keys, events and audit entries already use. This
    table gives that string a name, a tier and - the part that does real work -
    a base URL, which is what auto-discovery walks and what pre-fills the
    `base_url` config field of every adapter whose schema asks for one.
    """

    __tablename__ = "applications"
    __table_args__ = (
        UniqueConstraint("tenant_id", "app_id", name="uq_applications_app_id"),
        Index("ix_applications_tenant", "tenant_id", "app_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    app_id: Mapped[str] = mapped_column(String(100))
    name: Mapped[str] = mapped_column(String(200))
    tier: Mapped[str] = mapped_column(String(20), default="tier_2")
    environment: Mapped[str] = mapped_column(String(20), default="production")
    base_url: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    description: Mapped[str] = mapped_column(Text, default="")
    owner_team: Mapped[str] = mapped_column(String(200), default="")
    created_at: Mapped[datetime] = mapped_column(TZDateTime)
    created_by: Mapped[str] = mapped_column(String(36))
    updated_at: Mapped[datetime] = mapped_column(TZDateTime)


class OnboardingProfileRow(Base):
    """Everything the wizard learned about one application, as one document.

    One row per application rather than a table per step, because the aggregate's
    invariants cross the steps - a probe names a journey, a baseline names a
    signal, an SLO names a journey - and enforcing those needs the whole profile
    in hand on every write. The sections are separate JSON columns so a reader
    that wants only the policy does not parse the topology.
    """

    __tablename__ = "onboarding_profiles"

    tenant_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    app_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    signals: Mapped[list] = mapped_column(JSON, default=list)
    region_label: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    regions: Mapped[list] = mapped_column(JSON, default=list)
    journeys: Mapped[list] = mapped_column(JSON, default=list)
    probes: Mapped[list] = mapped_column(JSON, default=list)
    actions: Mapped[list] = mapped_column(JSON, default=list)
    slos: Mapped[list] = mapped_column(JSON, default=list)
    severity_matrix: Mapped[dict] = mapped_column(JSON, default=dict)
    policy: Mapped[dict] = mapped_column(JSON, default=dict)
    baselines: Mapped[list] = mapped_column(JSON, default=list)
    topology: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    last_test: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(TZDateTime)


class TopologyVersionRow(Base):
    """One imported map, kept forever.

    Versions are never overwritten and never pruned: "has the topology changed
    since the incident started" is answered by comparing versions, and the
    blast-radius delta needs the older graph to still exist. The unique key on
    (tenant, app, version) is what makes re-uploading an unchanged document a
    no-op rather than a second version.
    """

    __tablename__ = "topology_versions"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "app_id", "version", name="uq_topology_versions_version"
        ),
        Index("ix_topology_versions_app", "tenant_id", "app_id", "imported_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    app_id: Mapped[str] = mapped_column(String(100))
    version: Mapped[str] = mapped_column(String(120))
    snapshot: Mapped[dict] = mapped_column(JSON)
    node_count: Mapped[int] = mapped_column(Integer, default=0)
    edge_count: Mapped[int] = mapped_column(Integer, default=0)
    imported_at: Mapped[datetime] = mapped_column(TZDateTime)
    imported_by: Mapped[str] = mapped_column(String(36), default="")
    source: Mapped[str] = mapped_column(String(20), default="upload")
    normalized_by: Mapped[str] = mapped_column(String(64), default="")


# --- incident lifecycle (S4-T02) ---------------------------------------------


class IncidentRow(Base):
    """An incident: the record of a decision a person made (REQ-043).

    On the incident database rather than beside the detection rollups, and that
    is the whole distinction the two stores exist to keep. A stance is derived,
    recomputed every five seconds and safe to delete; an incident is a
    declaration, and it is kept and sealed with the events that belong to it.

    `proposal` is a JSON snapshot of what detection was saying at the instant
    Declare was pressed, copied rather than referenced: the rollups it was drawn
    from age out in hours, and an incident that could only explain itself by
    re-reading live telemetry would explain itself differently every time.

    The `ticket_*` columns are the ITSM link. `ticket_state` has five values and
    `uncertain` is the one that earns its place - a create whose outcome is
    unknown must never be retried as though it had failed.
    """

    __tablename__ = "incidents"
    __table_args__ = (
        UniqueConstraint("tenant_id", "reference", name="uq_incidents_reference"),
        Index("ix_incidents_app_state", "tenant_id", "app_id", "state"),
        Index("ix_incidents_tenant_declared", "tenant_id", "declared_at"),
        # The ITSM retry job's cross-tenant work list.
        Index("ix_incidents_ticket_state", "ticket_state"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    app_id: Mapped[str] = mapped_column(String(100))
    reference: Mapped[str] = mapped_column(String(40))
    ordinal: Mapped[int] = mapped_column(Integer, default=1)
    title: Mapped[str] = mapped_column(String(200))
    summary: Mapped[str] = mapped_column(Text, default="")
    severity: Mapped[str] = mapped_column(String(4))
    severity_source: Mapped[str] = mapped_column(String(10), default="proposed")
    state: Mapped[str] = mapped_column(String(20))
    stance_at_declare: Mapped[str] = mapped_column(String(20), default="")
    declared_at: Mapped[datetime] = mapped_column(TZDateTime)
    declared_by: Mapped[str] = mapped_column(String(36))
    declared_by_display: Mapped[str] = mapped_column(String(200), default="")
    updated_at: Mapped[datetime] = mapped_column(TZDateTime)
    recovering_at: Mapped[Optional[datetime]] = mapped_column(TZDateTime, nullable=True)
    closed_at: Mapped[Optional[datetime]] = mapped_column(TZDateTime, nullable=True)
    closed_by: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    closure_reason: Mapped[str] = mapped_column(Text, default="")
    proposal: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    change_windows: Mapped[list] = mapped_column(JSON, default=list)
    ticket_state: Mapped[str] = mapped_column(String(20), default="pending")
    ticket_id: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    ticket_reference: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    ticket_status: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    ticket_adapter_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    ticket_binding_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    ticket_attempts: Mapped[int] = mapped_column(Integer, default=0)
    ticket_detail: Mapped[str] = mapped_column(Text, default="")
    ticket_last_attempt_at: Mapped[Optional[datetime]] = mapped_column(
        TZDateTime, nullable=True
    )
    ticket_synced_at: Mapped[Optional[datetime]] = mapped_column(
        TZDateTime, nullable=True
    )


class IncidentTransitionRow(Base):
    """Every lifecycle move, append-only.

    Never summarised into the incident row and never pruned: "who moved this to
    recovering, when, and what did they say" is the first question a
    post-incident review asks, and a current-state column cannot answer it.
    """

    __tablename__ = "incident_transitions"
    __table_args__ = (
        Index("ix_incident_transitions_incident", "tenant_id", "incident_id", "id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    incident_id: Mapped[str] = mapped_column(String(36))
    app_id: Mapped[str] = mapped_column(String(100))
    from_state: Mapped[str] = mapped_column(String(20))
    to_state: Mapped[str] = mapped_column(String(20))
    at: Mapped[datetime] = mapped_column(TZDateTime)
    actor_type: Mapped[str] = mapped_column(String(20))
    actor_id: Mapped[str] = mapped_column(String(200))
    reason: Mapped[str] = mapped_column(Text, default="")
    severity_before: Mapped[Optional[str]] = mapped_column(String(4), nullable=True)
    severity_after: Mapped[Optional[str]] = mapped_column(String(4), nullable=True)
    detail: Mapped[dict] = mapped_column(JSON, default=dict)


class ChangeWindowRow(Base):
    """One change, folded from every deployment event that named it (REQ-044).

    A projection of the timeline rather than a second source of truth: the
    events remain what an auditor replays, and this table exists so that "is a
    change in flight right now" is a keyed read instead of a scan of the log.
    Keyed on (tenant, app, change_id), which is what makes a re-triggered
    pipeline one window rather than two.
    """

    __tablename__ = "change_windows"
    __table_args__ = (
        Index("ix_change_windows_app_started", "tenant_id", "app_id", "started_at"),
        Index("ix_change_windows_open", "announced_completed"),
    )

    tenant_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    app_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    change_id: Mapped[str] = mapped_column(String(120), primary_key=True)
    change_type: Mapped[str] = mapped_column(String(20))
    service: Mapped[str] = mapped_column(String(200), default="")
    status: Mapped[str] = mapped_column(String(20))
    started_at: Mapped[datetime] = mapped_column(TZDateTime)
    completed_at: Mapped[Optional[datetime]] = mapped_column(TZDateTime, nullable=True)
    first_seen_at: Mapped[datetime] = mapped_column(TZDateTime)
    last_event_at: Mapped[datetime] = mapped_column(TZDateTime)
    version: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    actor: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    diff_ref: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    source_system: Mapped[str] = mapped_column(String(100), default="")
    simulated: Mapped[bool] = mapped_column(Boolean, default=False)
    announced_started: Mapped[bool] = mapped_column(Boolean, default=False)
    announced_completed: Mapped[bool] = mapped_column(Boolean, default=False)


# --- the war room (S4-T03) ----------------------------------------------------


class WarRoomRow(Base):
    """The room an incident is run from (REQ-045).

    Keyed on the incident rather than on its own id, uniquely, because one
    incident has one room. That constraint is the duplicate guard: opening is
    called from declare and again from any retry, and a second room would be a
    second set of pages to the same people.

    `radius` is a JSON snapshot of the blast radius as it was when the room
    opened, copied rather than recomputed. Re-deriving it on every read would
    make the room's membership drift as the topology is re-imported and the
    telemetry moves, and "why was I paged" has to have a stable answer.

    `last_harvest_at` is the bridge cursor. It is what makes the commitment
    harvest resumable rather than re-reading a six-hour transcript every minute.
    """

    __tablename__ = "war_rooms"
    __table_args__ = (
        UniqueConstraint("tenant_id", "incident_id", name="uq_war_rooms_incident"),
        Index("ix_war_rooms_open", "closed_at", "bridge_binding_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    incident_id: Mapped[str] = mapped_column(String(36))
    app_id: Mapped[str] = mapped_column(String(100))
    opened_at: Mapped[datetime] = mapped_column(TZDateTime)
    opened_by: Mapped[str] = mapped_column(String(36))
    severity: Mapped[str] = mapped_column(String(4))
    radius: Mapped[dict] = mapped_column(JSON, default=dict)
    gaps: Mapped[list] = mapped_column(JSON, default=list)
    bridge_channel: Mapped[str] = mapped_column(String(200), default="")
    bridge_adapter_id: Mapped[str] = mapped_column(String(64), default="")
    bridge_binding_id: Mapped[str] = mapped_column(String(36), default="")
    closed_at: Mapped[Optional[datetime]] = mapped_column(TZDateTime, nullable=True)
    last_harvest_at: Mapped[Optional[datetime]] = mapped_column(
        TZDateTime, nullable=True
    )


class RoomMemberRow(Base):
    """Who is in the room, and why (REQ-045).

    `required`, `paged` and `present` are three separate columns rather than one
    status, because they are three separate facts and the difference between them
    is the useful thing on the screen. A room where all three agree went well; a
    room where four were required, four were paged and two are present is a room
    that needs somebody to notice.
    """

    __tablename__ = "war_room_members"
    __table_args__ = (
        Index("ix_war_room_members_incident", "tenant_id", "incident_id"),
    )

    tenant_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    incident_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    role: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    reason: Mapped[str] = mapped_column(Text, default="")
    source: Mapped[str] = mapped_column(String(20))
    display_name: Mapped[str] = mapped_column(String(200), default="")
    added_at: Mapped[datetime] = mapped_column(TZDateTime)
    required: Mapped[bool] = mapped_column(Boolean, default=False)
    paged: Mapped[bool] = mapped_column(Boolean, default=False)
    present: Mapped[bool] = mapped_column(Boolean, default=False)
    joined_at: Mapped[Optional[datetime]] = mapped_column(TZDateTime, nullable=True)
    last_seen_at: Mapped[Optional[datetime]] = mapped_column(TZDateTime, nullable=True)


class IncidentPageRow(Base):
    """One page: a question put to one person, and what happened to it (REQ-045).

    `ack_deadline_at` is stored rather than a timer being held in memory, and
    that single choice is what makes the countdown survive a restart, agree
    between the ack board and the escalation sweep, and run at 100x on a drill's
    virtual clock with no drill-aware code.

    `deliveries` links to the S3 receipts by notification id rather than copying
    their status. `notification_deliveries` stays the system of record for
    whether a person was told; a page that kept its own copy would eventually
    disagree with it, and the disagreement would be discovered during an
    incident.
    """

    __tablename__ = "incident_pages"
    __table_args__ = (
        Index("ix_incident_pages_incident", "tenant_id", "incident_id", "created_at"),
        Index("ix_incident_pages_recipient", "tenant_id", "recipient", "state"),
        # The escalation sweep's cross-tenant work list: still awaiting an
        # answer, and past its deadline.
        Index("ix_incident_pages_deadline", "state", "ack_deadline_at"),
        Index("ix_incident_pages_chain", "tenant_id", "escalated_from_page_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    incident_id: Mapped[str] = mapped_column(String(36))
    app_id: Mapped[str] = mapped_column(String(100))
    recipient: Mapped[str] = mapped_column(String(36))
    recipient_display: Mapped[str] = mapped_column(String(200), default="")
    role: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    reason: Mapped[str] = mapped_column(Text, default="")
    severity: Mapped[str] = mapped_column(String(4), default="P2")
    state: Mapped[str] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(TZDateTime)
    ack_timeout_s: Mapped[int] = mapped_column(Integer, default=300)
    paged_at: Mapped[Optional[datetime]] = mapped_column(TZDateTime, nullable=True)
    ack_deadline_at: Mapped[Optional[datetime]] = mapped_column(
        TZDateTime, nullable=True
    )
    acked_at: Mapped[Optional[datetime]] = mapped_column(TZDateTime, nullable=True)
    acked_by: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    ack_note: Mapped[str] = mapped_column(Text, default="")
    escalated_at: Mapped[Optional[datetime]] = mapped_column(TZDateTime, nullable=True)
    escalated_to_page_id: Mapped[Optional[str]] = mapped_column(
        String(36), nullable=True
    )
    escalated_from_page_id: Mapped[Optional[str]] = mapped_column(
        String(36), nullable=True
    )
    hop: Mapped[int] = mapped_column(Integer, default=0)
    rotation_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    policy_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    policy_step: Mapped[int] = mapped_column(Integer, default=0)
    detail: Mapped[str] = mapped_column(Text, default="")
    deliveries: Mapped[list] = mapped_column(JSON, default=list)
    requested_by: Mapped[str] = mapped_column(String(36), default="")
    requested_by_type: Mapped[str] = mapped_column(String(20), default="system")


class IncidentTaskRow(Base):
    """A commitment with an owner and a due time (REQ-045).

    `owner_user_id` is nullable and `owner_label` exists beside it because a
    commitment harvested from a bridge often names somebody this platform cannot
    resolve. An unassigned task carrying the name as typed is honest and fixable
    in one click; a task assigned to a user id guessed from a first name is how
    the wrong person gets paged.

    `harvest_key` is the derived idempotency key from the bridge. Unique per
    incident, which is what makes re-reading the transcript produce one task
    rather than one per sweep.
    """

    __tablename__ = "incident_tasks"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "incident_id", "harvest_key", name="uq_incident_tasks_harvest"
        ),
        Index("ix_incident_tasks_incident", "tenant_id", "incident_id", "created_at"),
        Index("ix_incident_tasks_owner", "tenant_id", "owner_user_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    incident_id: Mapped[str] = mapped_column(String(36))
    app_id: Mapped[str] = mapped_column(String(100))
    title: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(20))
    source: Mapped[str] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(TZDateTime)
    created_by: Mapped[str] = mapped_column(String(200), default="")
    owner_user_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    owner_label: Mapped[str] = mapped_column(String(200), default="")
    due_at: Mapped[Optional[datetime]] = mapped_column(TZDateTime, nullable=True)
    due_label: Mapped[str] = mapped_column(String(60), default="")
    note: Mapped[str] = mapped_column(Text, default="")
    evidence_ref: Mapped[str] = mapped_column(Text, default="")
    harvest_key: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(TZDateTime, nullable=True)
    completed_by: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    updated_at: Mapped[Optional[datetime]] = mapped_column(TZDateTime, nullable=True)
    detail: Mapped[dict] = mapped_column(JSON, default=dict)


class LlmCacheRow(Base):
    """Model answers already paid for, keyed by a hash of the whole input (REQ-051).

    The primary key is `(tenant_id, cache_key)` rather than the hash alone. The
    hash is a pure function of the input, so two tenants asking the identical
    question produce the identical key - and a shared row would mean one
    tenant's call could be served an answer generated inside another's
    incident. Tenant isolation is cheaper than the debate (REQ-013).

    `payload` holds the answer and its provenance, never the prompt: a warm
    cache should not become a second, unredacted copy of everything the
    platform has ever sent a model.
    """

    __tablename__ = "llm_cache"
    __table_args__ = (Index("ix_llm_cache_tenant_created", "tenant_id", "created_at"),)

    tenant_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    cache_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    provider: Mapped[str] = mapped_column(String(40))
    model: Mapped[str] = mapped_column(String(120))
    purpose: Mapped[str] = mapped_column(String(80))
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    hits: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(TZDateTime)


class EventSealRow(Base):
    __tablename__ = "event_seals"
    __table_args__ = (Index("ix_event_seals_tenant", "tenant_id", "sealed_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    scope: Mapped[str] = mapped_column(String(20))
    plane: Mapped[str] = mapped_column(String(20))
    incident_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    from_seq: Mapped[int] = mapped_column(Integer)
    to_seq: Mapped[int] = mapped_column(Integer)
    event_count: Mapped[int] = mapped_column(Integer)
    seal_hash: Mapped[str] = mapped_column(String(64))
    prev_seal_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    sealed_at: Mapped[datetime] = mapped_column(TZDateTime)
    sealed_by: Mapped[str] = mapped_column(String(36))


# --- incident brain (S5-T01) --------------------------------------------------


class IncidentGraphNodeRow(Base):
    """One thing the platform believes about an incident, at one version (REQ-050).

    Rows are inserted and marked superseded; content is never rewritten and rows
    are never deleted. `(tenant_id, incident_id, derived_key, version)` is unique,
    which is what makes a redelivered event, a restarted worker and two agents
    racing produce one version rather than three - and the partial index on the
    current row is what makes "what do we believe now" one indexed read rather
    than a scan of the incident's whole history.
    """

    __tablename__ = "incident_graph_nodes"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "incident_id",
            "derived_key",
            "version",
            name="uq_graph_node_version",
        ),
        Index("ix_graph_nodes_incident", "tenant_id", "incident_id", "seq"),
        Index("ix_graph_nodes_current", "tenant_id", "incident_id", "status", "node_type"),
        Index("ix_graph_nodes_key", "tenant_id", "incident_id", "derived_key"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    incident_id: Mapped[str] = mapped_column(String(100))
    node_type: Mapped[str] = mapped_column(String(20))
    derived_key: Mapped[str] = mapped_column(String(200))
    version: Mapped[int] = mapped_column(Integer, default=1)
    seq: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(20), default="current")
    label: Mapped[str] = mapped_column(String(400))
    content: Mapped[str] = mapped_column(Text, default="")
    untrusted: Mapped[bool] = mapped_column(Boolean, default=False)
    confidence: Mapped[Optional[float]] = mapped_column(nullable=True)
    confirmation: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    supersedes: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    superseded_by: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    retired_at: Mapped[Optional[datetime]] = mapped_column(TZDateTime, nullable=True)
    retired_reason: Mapped[str] = mapped_column(String(400), default="")
    created_at: Mapped[datetime] = mapped_column(TZDateTime)
    provenance: Mapped[dict] = mapped_column(JSON, default=dict)
    tags: Mapped[list] = mapped_column(JSON, default=list)
    detail: Mapped[dict] = mapped_column(JSON, default=dict)


class IncidentGraphEdgeRow(Base):
    """A relation between two node versions, kept forever.

    Unique on (incident, source, target, relation) so that re-linking the same
    evidence to the same hypothesis is a no-op rather than a doubling of its
    weight in the confidence policy.
    """

    __tablename__ = "incident_graph_edges"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "incident_id",
            "source_id",
            "target_id",
            "relation",
            name="uq_graph_edge",
        ),
        Index("ix_graph_edges_target", "tenant_id", "incident_id", "target_id"),
        Index("ix_graph_edges_source", "tenant_id", "incident_id", "source_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    incident_id: Mapped[str] = mapped_column(String(100))
    source_id: Mapped[str] = mapped_column(String(36))
    target_id: Mapped[str] = mapped_column(String(36))
    relation: Mapped[str] = mapped_column(String(20))
    weight: Mapped[float] = mapped_column(default=0.0)
    note: Mapped[str] = mapped_column(String(400), default="")
    created_at: Mapped[datetime] = mapped_column(TZDateTime)
    detail: Mapped[dict] = mapped_column(JSON, default=dict)


class ConfidenceHistoryRow(Base):
    """Why a hypothesis moved (REQ-053). Append-only, and the reason the cockpit
    can answer "why did #2 go from 65% to 32%" from stored numbers rather than
    from a narrative somebody has to trust."""

    __tablename__ = "incident_confidence_history"
    __table_args__ = (
        Index(
            "ix_confidence_history_hypothesis",
            "tenant_id",
            "incident_id",
            "hypothesis_key",
            "id",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    incident_id: Mapped[str] = mapped_column(String(100))
    hypothesis_key: Mapped[str] = mapped_column(String(200))
    at: Mapped[datetime] = mapped_column(TZDateTime)
    previous: Mapped[Optional[float]] = mapped_column(nullable=True)
    current: Mapped[float] = mapped_column()
    net_weight: Mapped[float] = mapped_column(default=0.0)
    supporting: Mapped[list] = mapped_column(JSON, default=list)
    refuting: Mapped[list] = mapped_column(JSON, default=list)
    added: Mapped[list] = mapped_column(JSON, default=list)
    removed: Mapped[list] = mapped_column(JSON, default=list)
    trigger: Mapped[str] = mapped_column(String(120), default="")
    narrative: Mapped[str] = mapped_column(Text, default="")
    narrative_degraded: Mapped[bool] = mapped_column(Boolean, default=False)
    detail: Mapped[dict] = mapped_column(JSON, default=dict)


class SituationSnapshotRow(Base):
    """The whole reasoning state at one moment (REQ-050).

    Stores node ids rather than content: the nodes are immutable, so the ids
    reconstruct the view exactly, and a snapshot that copied content would be a
    second copy of incident text with its own redaction problem.
    """

    __tablename__ = "incident_situation_snapshots"
    __table_args__ = (
        Index("ix_situation_snapshots_incident", "tenant_id", "incident_id", "taken_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    incident_id: Mapped[str] = mapped_column(String(100))
    taken_at: Mapped[datetime] = mapped_column(TZDateTime)
    reason: Mapped[str] = mapped_column(String(200), default="")
    node_ids: Mapped[list] = mapped_column(JSON, default=list)
    hypotheses: Mapped[dict] = mapped_column(JSON, default=dict)
    topology_version: Mapped[str] = mapped_column(String(120), default="")
    max_seq: Mapped[int] = mapped_column(Integer, default=0)
    degraded: Mapped[bool] = mapped_column(Boolean, default=False)
    fingerprint: Mapped[str] = mapped_column(String(64), default="")
    detail: Mapped[dict] = mapped_column(JSON, default=dict)


# --- governance and execution (S6) --------------------------------------------


class GovernancePolicyRow(Base):
    """One version of one application's policy (REQ-060).

    Append-only: an edit inserts version n+1 and the old row stays. `author_id`
    is not nullable because an unattributed rule change is not auditable, and
    `source` keeps the YAML the author actually submitted rather than a
    re-serialisation of what was parsed.

    There is no `audit_enabled` column, and there never will be. Audit is not a
    configurable field, so it cannot be configured off (CLAUDE.md rule 4).
    """

    __tablename__ = "governance_policies"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "app_id", "version", name="uq_governance_policy_version"
        ),
        Index("ix_governance_policies_app", "tenant_id", "app_id", "version"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    app_id: Mapped[str] = mapped_column(String(100))
    version: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(TZDateTime)
    author_id: Mapped[str] = mapped_column(String(36))
    author_label: Mapped[str] = mapped_column(String(200), default="")
    note: Mapped[str] = mapped_column(Text, default="")
    approval_ttl_s: Mapped[int] = mapped_column(Integer, default=900)
    stability_window_s: Mapped[int] = mapped_column(Integer, default=900)
    stability_samples: Mapped[int] = mapped_column(Integer, default=3)
    max_action_retries: Mapped[int] = mapped_column(Integer, default=1)
    revalidation_cycles: Mapped[int] = mapped_column(Integer, default=3)
    blocked_classes: Mapped[list] = mapped_column(JSON, default=list)
    require_approval_classes: Mapped[list] = mapped_column(JSON, default=list)
    approver_roles: Mapped[list] = mapped_column(JSON, default=list)
    action_rules: Mapped[dict] = mapped_column(JSON, default=dict)
    checksum: Mapped[str] = mapped_column(String(32), default="")
    source: Mapped[str] = mapped_column(Text, default="")


class ExecutionLedgerRow(Base):
    """One attempt to change somebody's production estate (REQ-061, REQ-062).

    `idempotency_key` is unique per tenant, and that constraint *is* the
    duplicate-prevention control: two concurrent submits of the same request
    derive the same key, and the second insert fails rather than reaching the
    adapter. Doing it with a unique index rather than a read-then-write is the
    difference between preventing a double rollback and usually preventing one.

    `args` is already redacted before it arrives (`ledger.redact_args`); the
    ledger is not a place credentials are stored.
    """

    __tablename__ = "execution_ledger"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "idempotency_key", name="uq_execution_idempotency"
        ),
        Index("ix_execution_ledger_incident", "tenant_id", "incident_id", "requested_at"),
        Index("ix_execution_ledger_state", "state", "requested_at"),
        Index("ix_execution_ledger_action", "tenant_id", "incident_id", "action_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    incident_id: Mapped[str] = mapped_column(String(36))
    app_id: Mapped[str] = mapped_column(String(100))
    action_id: Mapped[str] = mapped_column(String(100))
    action_class: Mapped[str] = mapped_column(String(30))
    risk_class: Mapped[str] = mapped_column(String(20))
    state: Mapped[str] = mapped_column(String(24))
    idempotency_key: Mapped[str] = mapped_column(String(64))
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    requested_by: Mapped[str] = mapped_column(String(36))
    requested_at: Mapped[datetime] = mapped_column(TZDateTime)
    args: Mapped[dict] = mapped_column(JSON, default=dict)
    args_digest: Mapped[str] = mapped_column(String(32), default="")
    decision: Mapped[str] = mapped_column(String(20), default="block")
    decision_reason: Mapped[str] = mapped_column(Text, default="")
    rules_hit: Mapped[list] = mapped_column(JSON, default=list)
    policy_version: Mapped[int] = mapped_column(Integer, default=0)
    policy_ref: Mapped[str] = mapped_column(String(120), default="")
    floor_version: Mapped[str] = mapped_column(String(40), default="")
    decision_fingerprint: Mapped[str] = mapped_column(String(32), default="")
    approval_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    binding_id: Mapped[str] = mapped_column(String(36), default="")
    adapter_execution_id: Mapped[str] = mapped_column(String(120), default="")
    started_at: Mapped[Optional[datetime]] = mapped_column(TZDateTime, nullable=True)
    finished_at: Mapped[Optional[datetime]] = mapped_column(TZDateTime, nullable=True)
    error: Mapped[str] = mapped_column(Text, default="")
    error_category: Mapped[str] = mapped_column(String(20), default="none")
    retry_blocked: Mapped[bool] = mapped_column(Boolean, default=False)
    reconcile_outcome: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    reconciled_at: Mapped[Optional[datetime]] = mapped_column(TZDateTime, nullable=True)
    reconcile_detail: Mapped[dict] = mapped_column(JSON, default=dict)
    compensates_execution_id: Mapped[Optional[str]] = mapped_column(
        String(36), nullable=True
    )
    compensated_by_execution_id: Mapped[Optional[str]] = mapped_column(
        String(36), nullable=True
    )
    supersedes_execution_id: Mapped[Optional[str]] = mapped_column(
        String(36), nullable=True
    )
    takeover_by: Mapped[str] = mapped_column(String(36), default="")
    takeover_note: Mapped[str] = mapped_column(Text, default="")
    result_ref: Mapped[str] = mapped_column(String(400), default="")
    detail: Mapped[dict] = mapped_column(JSON, default=dict)


class ApprovalRow(Base):
    """One person's decision about one execution (CTL-AUD-03).

    Every clause of the claim is a column - who, which roles, which permission,
    from when to when, under which policy version, bound to which decision - so
    that "was this approval valid at the moment it was spent" is answerable from
    the row rather than reconstructed.
    """

    __tablename__ = "execution_approvals"
    __table_args__ = (
        Index("ix_approvals_execution", "tenant_id", "execution_id", "requested_at"),
        Index("ix_approvals_incident", "tenant_id", "incident_id", "requested_at"),
        Index("ix_approvals_expiry", "state", "expires_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    incident_id: Mapped[str] = mapped_column(String(36))
    app_id: Mapped[str] = mapped_column(String(100))
    execution_id: Mapped[str] = mapped_column(String(36))
    action_id: Mapped[str] = mapped_column(String(100))
    state: Mapped[str] = mapped_column(String(20))
    requested_by: Mapped[str] = mapped_column(String(36))
    requested_at: Mapped[datetime] = mapped_column(TZDateTime)
    decision_fingerprint: Mapped[str] = mapped_column(String(32))
    policy_version: Mapped[int] = mapped_column(Integer, default=0)
    policy_ref: Mapped[str] = mapped_column(String(120), default="")
    required_roles: Mapped[list] = mapped_column(JSON, default=list)
    approver_user_id: Mapped[str] = mapped_column(String(36), default="")
    approver_label: Mapped[str] = mapped_column(String(200), default="")
    approver_roles: Mapped[list] = mapped_column(JSON, default=list)
    permission: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    issued_at: Mapped[Optional[datetime]] = mapped_column(TZDateTime, nullable=True)
    expires_at: Mapped[Optional[datetime]] = mapped_column(TZDateTime, nullable=True)
    used_at: Mapped[Optional[datetime]] = mapped_column(TZDateTime, nullable=True)
    revoked_at: Mapped[Optional[datetime]] = mapped_column(TZDateTime, nullable=True)
    note: Mapped[str] = mapped_column(Text, default="")
    detail: Mapped[dict] = mapped_column(JSON, default=dict)


class ResponsePlanRow(Base):
    """A version of the response plan (REQ-063).

    Versioned rather than replaced, so the kept/paused/added diff is computed
    from two stored rows instead of from a memory of what the screen used to
    show.
    """

    __tablename__ = "response_plans"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "incident_id", "version", name="uq_response_plan_version"
        ),
        Index("ix_response_plans_incident", "tenant_id", "incident_id", "version"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    incident_id: Mapped[str] = mapped_column(String(36))
    version: Mapped[int] = mapped_column(Integer)
    previous_version: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(TZDateTime)
    created_by: Mapped[str] = mapped_column(String(36))
    items: Mapped[list] = mapped_column(JSON, default=list)
    changed_assumptions: Mapped[list] = mapped_column(JSON, default=list)
    evidence_refs: Mapped[list] = mapped_column(JSON, default=list)
    note: Mapped[str] = mapped_column(Text, default="")
    detail: Mapped[dict] = mapped_column(JSON, default=dict)


class CommunicationRow(Base):
    """One piece of text for one audience (REQ-064).

    `grounded_node_ids` is scanned rather than indexed, deliberately: the
    contradiction sweep runs per incident over tens of rows, and a JSON
    containment index would be a Postgres-only feature this SQLite deployment
    cannot have. `issued_at` being null is what "nothing auto-sends" looks like
    in the data.
    """

    __tablename__ = "incident_communications"
    __table_args__ = (
        Index("ix_communications_incident", "tenant_id", "incident_id", "created_at"),
        Index("ix_communications_state", "tenant_id", "incident_id", "state"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    incident_id: Mapped[str] = mapped_column(String(36))
    app_id: Mapped[str] = mapped_column(String(100))
    audience: Mapped[str] = mapped_column(String(24))
    state: Mapped[str] = mapped_column(String(20))
    subject: Mapped[str] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(Text)
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(TZDateTime)
    created_by: Mapped[str] = mapped_column(String(36))
    grounded_node_ids: Mapped[list] = mapped_column(JSON, default=list)
    evidence_refs: Mapped[list] = mapped_column(JSON, default=list)
    model: Mapped[str] = mapped_column(String(120), default="")
    model_class: Mapped[str] = mapped_column(String(40), default="")
    degraded: Mapped[bool] = mapped_column(Boolean, default=True)
    approved_by: Mapped[str] = mapped_column(String(36), default="")
    approved_by_label: Mapped[str] = mapped_column(String(200), default="")
    approved_permission: Mapped[Optional[str]] = mapped_column(
        String(40), nullable=True
    )
    approved_at: Mapped[Optional[datetime]] = mapped_column(TZDateTime, nullable=True)
    issued_by: Mapped[str] = mapped_column(String(36), default="")
    issued_at: Mapped[Optional[datetime]] = mapped_column(TZDateTime, nullable=True)
    contradicted_at: Mapped[Optional[datetime]] = mapped_column(
        TZDateTime, nullable=True
    )
    contradiction_reason: Mapped[str] = mapped_column(String(200), default="")
    contradicting_node_ids: Mapped[list] = mapped_column(JSON, default=list)
    corrects_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    corrected_by_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    redline: Mapped[list] = mapped_column(JSON, default=list)
    withdrawn_at: Mapped[Optional[datetime]] = mapped_column(TZDateTime, nullable=True)
    note: Mapped[str] = mapped_column(Text, default="")
    detail: Mapped[dict] = mapped_column(JSON, default=dict)


class ValidationCycleRow(Base):
    """One pass over the four validation layers (REQ-065).

    `cycle` accumulates rather than overwriting, because the escalation rule is
    "after N failed cycles" and only a history can answer it. `layers` holds the
    per-layer results including each check's `source` and `result_ref`, so an
    auditor can follow a failing customer check back to the probe run it came
    from.
    """

    __tablename__ = "validation_cycles"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "incident_id", "cycle", name="uq_validation_cycle"
        ),
        Index("ix_validation_cycles_incident", "tenant_id", "incident_id", "cycle"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    incident_id: Mapped[str] = mapped_column(String(36))
    app_id: Mapped[str] = mapped_column(String(100))
    cycle: Mapped[int] = mapped_column(Integer)
    started_at: Mapped[datetime] = mapped_column(TZDateTime)
    finished_at: Mapped[Optional[datetime]] = mapped_column(TZDateTime, nullable=True)
    requested_by: Mapped[str] = mapped_column(String(36))
    verdict: Mapped[str] = mapped_column(String(20), default="not_recovered")
    layers: Mapped[list] = mapped_column(JSON, default=list)
    window: Mapped[dict] = mapped_column(JSON, default=dict)
    escalated: Mapped[bool] = mapped_column(Boolean, default=False)
    returned_to_active: Mapped[bool] = mapped_column(Boolean, default=False)
    evidence_node_ids: Mapped[list] = mapped_column(JSON, default=list)
    note: Mapped[str] = mapped_column(Text, default="")
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
