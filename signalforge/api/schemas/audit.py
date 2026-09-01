"""Wire shapes for audit replay and the post-incident review (REQ-072)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field

from signalforge.domain.audit.entries import ReplayDocument, ReplayEntry
from signalforge.domain.pir.review import Lesson, PostIncidentReview, Section


class ChainStatusOut(BaseModel):
    valid: bool = Field(
        description="False means the stored audit records no longer match their "
        "hashes. A replay that reports false is not evidence and the PIR "
        "generator refuses to build on it."
    )
    checked: int
    first_broken_seq: Optional[int] = None
    broken_seqs: list[int] = []
    reason: str


class ReplayEntryOut(BaseModel):
    seq: int
    event_id: str
    topic: str
    at: datetime
    headline: str
    actor_type: str
    actor_id: str
    actor_label: str
    consequence: str = Field(
        description="consequential, decision, observation or administrative - "
        "how much a reviewer should care."
    )
    justification: list[str] = Field(
        description="Why this was allowed: the policy rules hit, the approval "
        "spent, the evidence cited, the reason given."
    )
    effect: str = ""
    references: list[str] = Field(
        description="Things that can be opened to check it: result refs, "
        "execution ids, node ids, seals."
    )
    payload: dict[str, Any] = {}


class SealOut(BaseModel):
    seal_id: str
    from_seq: int
    to_seq: int
    event_count: int
    seal_hash: str
    sealed_at: datetime
    sealed_by: str


class ReplayOut(BaseModel):
    incident_id: str
    generated_at: datetime
    chain: ChainStatusOut
    count: int
    from_seq: int
    to_seq: int
    entries: list[ReplayEntryOut] = []
    seals: list[SealOut] = []


class CitationOut(BaseModel):
    ref: str
    kind: str
    detail: str = ""


class LessonOut(BaseModel):
    id: str
    text: str
    status: str = Field(
        description="verified or proposed. A lesson is verified only when every "
        "one of its citations resolves to a record this incident produced."
    )
    source: str
    citations: list[CitationOut] = []
    unresolved: list[str] = Field(
        default=[],
        description="Citations that did not resolve. Present on a proposed "
        "lesson so a reviewer can see what could not be shown.",
    )
    verified_at: Optional[datetime] = None


class SectionOut(BaseModel):
    key: str
    title: str
    lines: list[str] = []
    citations: list[CitationOut] = []


class PirOut(BaseModel):
    incident_id: str
    reference: str
    title: str
    app_id: str
    generated_at: datetime
    chain_valid: bool
    chain_detail: str
    degraded: bool
    model: str
    sections: list[SectionOut] = []
    lessons: list[LessonOut] = []
    verified_lesson_count: int = 0
    proposed_lesson_count: int = 0
    followups: list[str] = []
    metadata: dict[str, Any] = {}
    markdown: str = Field(
        description="The exported review. Deterministic, so two exports of one "
        "incident are byte-identical."
    )


def replay_entry_out(entry: ReplayEntry) -> ReplayEntryOut:
    return ReplayEntryOut(
        seq=entry.seq,
        event_id=entry.event_id,
        topic=entry.topic,
        at=entry.at,
        headline=entry.headline,
        actor_type=entry.actor_type,
        actor_id=entry.actor_id,
        actor_label=entry.actor_label,
        consequence=entry.consequence.value,
        justification=list(entry.justification),
        effect=entry.effect,
        references=list(entry.references),
        payload=dict(entry.payload),
    )


def replay_out(document: ReplayDocument) -> ReplayOut:
    return ReplayOut(
        incident_id=document.incident_id,
        generated_at=document.generated_at,
        chain=ChainStatusOut(
            valid=document.chain.valid,
            checked=document.chain.checked,
            first_broken_seq=document.chain.first_broken_seq,
            broken_seqs=list(document.chain.broken_seqs),
            reason=document.chain.reason,
        ),
        count=document.count,
        from_seq=document.from_seq,
        to_seq=document.to_seq,
        entries=[replay_entry_out(entry) for entry in document.entries],
        seals=[SealOut(**dict(seal)) for seal in document.seals],
    )


def lesson_out(lesson: Lesson) -> LessonOut:
    return LessonOut(
        id=lesson.id,
        text=lesson.text,
        status=lesson.status.value,
        source=lesson.source.value,
        citations=[
            CitationOut(ref=c.ref, kind=c.kind, detail=c.detail) for c in lesson.citations
        ],
        unresolved=list(lesson.unresolved),
        verified_at=lesson.verified_at,
    )


def section_out(section: Section) -> SectionOut:
    return SectionOut(
        key=section.key,
        title=section.title,
        lines=list(section.lines),
        citations=[
            CitationOut(ref=c.ref, kind=c.kind, detail=c.detail)
            for c in section.citations
        ],
    )


def pir_out(review: PostIncidentReview) -> PirOut:
    return PirOut(
        incident_id=review.incident_id,
        reference=review.reference,
        title=review.title,
        app_id=review.app_id,
        generated_at=review.generated_at,
        chain_valid=review.chain_valid,
        chain_detail=review.chain_detail,
        degraded=review.degraded,
        model=review.model,
        sections=[section_out(section) for section in review.sections],
        lessons=[lesson_out(lesson) for lesson in review.lessons],
        verified_lesson_count=len(review.verified_lessons),
        proposed_lesson_count=len(review.proposed_lessons),
        followups=list(review.followups),
        metadata=dict(review.metadata),
        markdown=review.as_markdown(),
    )
