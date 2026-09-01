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
