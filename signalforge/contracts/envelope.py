"""Event envelope: the one shape every SignalForge event travels in.

Frozen contract (CLAUDE.md rule 3). Fields marked "store-assigned" are None
when an emitter constructs the envelope and are filled by the event store in
recording order; everything else is the emitter's responsibility.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

SCHEMA_VERSION = 1

TOPIC_PATTERN = re.compile(r"^[a-z0-9_]+(\.[a-z0-9_]+)+$")


class ActorType(str, Enum):
    HUMAN = "human"
    AGENT = "agent"
    SYSTEM = "system"
    EXTERNAL = "external"


class Authority(str, Enum):
    """How much the source's statement can be trusted as ground truth."""

    AUTHORITATIVE = "authoritative"  # the system of record for this fact
    OBSERVED = "observed"            # measured or received first-hand
    INFERRED = "inferred"            # derived, correlated or model-produced


class Plane(str, Enum):
    """Retention plane (Sprint 2): signal events age out, incident events are kept and sealed."""

    SIGNAL = "signal"
    INCIDENT = "incident"


class AdapterKind(str, Enum):
    """The 13 integration kinds. Every adapter implements exactly one."""

    METRICS = "metrics"
    LOGS = "logs"
    TRACES = "traces"
    HEALTH = "health"
    ALERTS = "alerts"
    CHANGES = "changes"
    TOPOLOGY = "topology"
    COLLABORATION = "collaboration"
    ITSM = "itsm"
    ACTIONS = "actions"
    NOTIFICATIONS = "notifications"
    KNOWLEDGE = "knowledge"
    CHAOS = "chaos"


class Actor(BaseModel):
    model_config = ConfigDict(frozen=True)

    type: ActorType
    id: str = Field(min_length=1)
    display: Optional[str] = None


class Source(BaseModel):
    model_config = ConfigDict(frozen=True)

    system: str = Field(min_length=1, description="Emitting system or adapter instance id")
    kind: Optional[AdapterKind] = None
    authority: Authority = Authority.OBSERVED
    freshness_s: Optional[float] = Field(
        default=None, ge=0, description="Age of the underlying data at emission, seconds"
    )
    simulated: bool = False


class Redaction(BaseModel):
    model_config = ConfigDict(frozen=True)

    applied: bool = False
    fields: tuple[str, ...] = ()
    policy_id: Optional[str] = None


def canonical_payload_hash(payload: dict[str, Any]) -> str:
    """Reproducible sha256 over a canonical JSON encoding. Part of the contract:
    the audit hash chain is only verifiable if every writer hashes identically."""
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class EventEnvelope(BaseModel):
    model_config = ConfigDict(validate_assignment=True)

    schema_version: int = SCHEMA_VERSION
    event_id: uuid.UUID = Field(default_factory=uuid.uuid4)
    topic: str = Field(description="Dot-namespaced type, e.g. system.llm.degraded")

    tenant_id: str = Field(min_length=1)
    app_id: Optional[str] = None
    incident_id: Optional[str] = None
    plane: Plane = Plane.SIGNAL

    occurred_at: datetime = Field(
        description="When it happened in the observed world; may follow a drill virtual clock"
    )
    recorded_at: Optional[datetime] = Field(
        default=None, description="Store-assigned wall-clock time of durable write"
    )
    seq: Optional[int] = Field(
        default=None, ge=1, description="Store-assigned global recording order"
    )

    actor: Actor
    source: Source
    confidence: Optional[float] = Field(default=None, ge=0.0, le=1.0)

    correlation_id: Optional[str] = None
    causation_id: Optional[uuid.UUID] = Field(
        default=None, description="event_id of the event that directly caused this one"
    )
    supersedes: Optional[uuid.UUID] = Field(
        default=None, description="event_id this event replaces; nothing is ever deleted"
    )
    idempotency_key: Optional[str] = Field(default=None, max_length=256)

    redaction: Redaction = Redaction()
    payload: dict[str, Any] = Field(default_factory=dict)

    payload_hash: Optional[str] = Field(
        default=None, pattern=r"^[0-9a-f]{64}$",
        description="Store-assigned sha256 of the canonical payload JSON",
    )
    prev_hash: Optional[str] = Field(
        default=None, pattern=r"^[0-9a-f]{64}$",
        description="Store-assigned hash of the previous event in this tenant's chain",
    )

    @field_validator("topic")
    @classmethod
    def _topic_namespaced(cls, v: str) -> str:
        if not TOPIC_PATTERN.match(v):
            raise ValueError("topic must be dot-namespaced lowercase, e.g. deployment.started")
        return v

    @field_validator("occurred_at", "recorded_at")
    @classmethod
    def _tz_aware(cls, v: Optional[datetime]) -> Optional[datetime]:
        if v is not None and v.tzinfo is None:
            raise ValueError("timestamps must be timezone-aware")
        return v

    @property
    def partition_key(self) -> str:
        """Ordering lane (Sprint 2): incident, else app, else the tenant itself."""
        return self.incident_id or self.app_id or self.tenant_id


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Topics:
    """Well-known topics referenced by contracts and specs. Not exhaustive."""

    LLM_DEGRADED = "system.llm.degraded"
    CONNECTOR_HEALTH = "system.connector.health"
    DEPLOYMENT_STARTED = "deployment.started"
    DEPLOYMENT_COMPLETED = "deployment.completed"
