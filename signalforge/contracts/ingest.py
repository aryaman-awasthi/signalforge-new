"""/api/v1/ingest/* : the push-ingestion contract SignalForge exposes.

Frozen contract (CLAUDE.md rule 3). Security model (enforced in Sprint 3):
bearer token identifies the connector binding; every request additionally
carries an HMAC-SHA256 signature over "<timestamp>.<raw body>" with the
binding's shared secret, and the timestamp must be within the replay window.
event_id is the idempotency unit: duplicates are acknowledged, not re-applied.
Responses are 202 Accepted; processing is asynchronous.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Generic, Optional, TypeVar

from pydantic import BaseModel, ConfigDict, Field, field_validator

from signalforge.contracts.canonical import (
    Alert,
    ChangeEvent,
    CollabMessage,
    Ticket,
    TopologySnapshot,
)

INGEST_PATHS: dict[str, str] = {
    "changes": "/api/v1/ingest/changes",
    "alerts": "/api/v1/ingest/alerts",
    "topology": "/api/v1/ingest/topology",
    "messages": "/api/v1/ingest/messages",
    "tickets": "/api/v1/ingest/tickets",
}

HDR_SIGNATURE = "X-SignalForge-Signature"   # hex hmac-sha256 over "<timestamp>.<raw body>"
HDR_TIMESTAMP = "X-SignalForge-Timestamp"   # unix seconds at signing time
HDR_KEY_ID = "X-SignalForge-Key-Id"         # optional: identifies the secret after rotation
REPLAY_WINDOW_S = 300

MAX_BATCH_ITEMS = 500

PayloadT = TypeVar(
    "PayloadT", ChangeEvent, Alert, TopologySnapshot, CollabMessage, Ticket
)


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class IngestItem(_Model, Generic[PayloadT]):
    event_id: uuid.UUID = Field(description="Producer-assigned id; the idempotency unit")
    occurred_at: datetime
    payload: PayloadT

    @field_validator("occurred_at")
    @classmethod
    def _tz_aware(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError("occurred_at must be timezone-aware")
        return v


class IngestRequest(_Model, Generic[PayloadT]):
    source_system: str = Field(min_length=1)
    simulated: bool = False
    items: list[IngestItem[PayloadT]] = Field(min_length=1, max_length=MAX_BATCH_ITEMS)


IngestChangesRequest = IngestRequest[ChangeEvent]
IngestAlertsRequest = IngestRequest[Alert]
IngestTopologyRequest = IngestRequest[TopologySnapshot]
IngestMessagesRequest = IngestRequest[CollabMessage]
IngestTicketsRequest = IngestRequest[Ticket]


class IngestRejection(_Model):
    index: int = Field(ge=0, description="Position of the rejected item in the batch")
    reason: str


class IngestAck(_Model):
    """Body of the 202 response."""

    accepted: int = Field(ge=0)
    duplicates: int = Field(ge=0, description="Items whose event_id was already recorded")
    rejected: list[IngestRejection] = Field(default_factory=list)
    request_id: Optional[str] = None
