"""Wire shapes for the event backbone. The event itself is returned as the
frozen envelope contract - the cockpit and any consumer decode exactly what the
store holds - so only the wrappers are defined here."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field

from signalforge.application.events.replay import ReplayPage
from signalforge.application.events.sealing import SealVerification
from signalforge.contracts.envelope import EventEnvelope
from signalforge.domain.events.entities import DeadLetter, EventSeal


class EventPageOut(BaseModel):
    events: list[EventEnvelope]
    count: int
    after_seq: int
    next_after_seq: int
    has_more: bool


class CausalChainOut(BaseModel):
    event_id: str
    events: list[EventEnvelope]
    count: int


class SealIn(BaseModel):
    incident_id: Optional[str] = None
    until: Optional[int] = Field(default=None, ge=1, description="Inclusive seq bound")


class SealOut(BaseModel):
    id: str
    scope: str
    plane: str
    incident_id: Optional[str]
    from_seq: int
    to_seq: int
    event_count: int
    seal_hash: str
    prev_seal_hash: Optional[str]
    sealed_at: datetime
    sealed_by: str


class SealVerificationOut(BaseModel):
    seal_id: str
    ok: bool
    recomputed_hash: str
    broken_seqs: list[int]
    event_count: int
    detail: str


class DeadLetterOut(BaseModel):
    id: str
    group: str
    event_seq: int
    event_id: str
    topic: str
    partition_key: str
    lane: int
    attempts: int
    error: str
    state: str
    first_failed_at: datetime
    last_failed_at: datetime
    requeued_at: Optional[datetime] = None
    requeued_by: Optional[str] = None
    resolved_at: Optional[datetime] = None


def page_out(page: ReplayPage) -> EventPageOut:
    return EventPageOut(
        events=list(page.events),
        count=page.count,
        after_seq=page.after_seq,
        next_after_seq=page.next_after_seq,
        has_more=page.has_more,
    )


def seal_out(seal: EventSeal) -> SealOut:
    return SealOut(
        id=seal.id,
        scope=seal.scope.value,
        plane=seal.plane.value,
        incident_id=seal.incident_id,
        from_seq=seal.from_seq,
        to_seq=seal.to_seq,
        event_count=seal.event_count,
        seal_hash=seal.seal_hash,
        prev_seal_hash=seal.prev_seal_hash,
        sealed_at=seal.sealed_at,
        sealed_by=seal.sealed_by,
    )


def verification_out(verification: SealVerification) -> SealVerificationOut:
    return SealVerificationOut(
        seal_id=verification.seal_id,
        ok=verification.ok,
        recomputed_hash=verification.recomputed_hash,
        broken_seqs=list(verification.broken_seqs),
        event_count=verification.event_count,
        detail=verification.detail,
    )


def dead_letter_out(dead_letter: DeadLetter) -> DeadLetterOut:
    return DeadLetterOut(
        id=dead_letter.id,
        group=dead_letter.group,
        event_seq=dead_letter.event_seq,
        event_id=dead_letter.event_id,
        topic=dead_letter.topic,
        partition_key=dead_letter.partition_key,
        lane=dead_letter.lane,
        attempts=dead_letter.attempts,
        error=dead_letter.error,
        state=dead_letter.state.value,
        first_failed_at=dead_letter.first_failed_at,
        last_failed_at=dead_letter.last_failed_at,
        requeued_at=dead_letter.requeued_at,
        requeued_by=dead_letter.requeued_by,
        resolved_at=dead_letter.resolved_at,
    )
