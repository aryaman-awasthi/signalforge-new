"""SQLAlchemy models for identity, secrets and the audit chain (S1).

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
