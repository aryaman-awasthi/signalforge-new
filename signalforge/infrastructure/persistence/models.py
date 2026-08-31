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
