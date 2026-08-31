"""Event-backbone entities: what an operator sees and acts on when delivery
fails, and what an auditor is handed when a range is closed."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from enum import Enum
from typing import Optional

from signalforge.contracts.envelope import Plane


class DeadLetterState(str, Enum):
    DEAD = "dead"
    """Delivery exhausted its attempts; the event is parked and visible."""

    REQUEUED = "requeued"
    """An operator asked for redelivery; the next dispatcher sweep will take it."""

    RESOLVED = "resolved"
    """Redelivery succeeded; kept as the record that it once failed."""


@dataclass(frozen=True)
class DeadLetter:
    id: str
    group: str
    event_seq: int
    event_id: str
    tenant_id: str
    topic: str
    partition_key: str
    lane: int
    attempts: int
    error: str
    state: DeadLetterState
    first_failed_at: datetime
    last_failed_at: datetime
    requeued_at: Optional[datetime] = None
    requeued_by: Optional[str] = None
    resolved_at: Optional[datetime] = None

    def requeued(self, *, by: str, at: datetime) -> "DeadLetter":
        return replace(self, state=DeadLetterState.REQUEUED, requeued_at=at, requeued_by=by)

    def resolved(self, *, at: datetime) -> "DeadLetter":
        return replace(self, state=DeadLetterState.RESOLVED, resolved_at=at)

    def failed_again(self, *, error: str, at: datetime) -> "DeadLetter":
        return replace(
            self,
            state=DeadLetterState.DEAD,
            attempts=self.attempts + 1,
            error=error,
            last_failed_at=at,
        )


@dataclass(frozen=True)
class RetentionWatermark:
    """What retention removed from one (tenant, plane) chain, and the hash it
    left behind so the surviving chain still verifies. Without the anchor a
    prune and a deletion look identical (REQ-023)."""

    tenant_id: str
    plane: Plane
    pruned_through_seq: int
    anchor_hash: str
    pruned_count: int
    pruned_at: datetime


class SealScope(str, Enum):
    INCIDENT = "incident"
    TENANT = "tenant"


@dataclass(frozen=True)
class EventSeal:
    """A signed-off, immutable statement that a range of the chain looked
    exactly like this at seal time. Sealing is what closing an incident leaves
    behind for the auditor (REQ-023)."""

    id: str
    tenant_id: str
    scope: SealScope
    plane: Plane
    from_seq: int
    to_seq: int
    event_count: int
    seal_hash: str
    sealed_at: datetime
    sealed_by: str
    incident_id: Optional[str] = None
    prev_seal_hash: Optional[str] = None
