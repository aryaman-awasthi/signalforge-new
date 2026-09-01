"""The post-incident review, and the gate on its lessons (REQ-072).

The review itself is a derived document: every line of it is a stored record
somewhere else, and the value it adds is ordering, grouping and readability.
That is why nothing in this module can be constructed without citations.

## The verified-lessons gate

A PIR's most durable output is its lessons, because those are what the next
incident reads. So a lesson has two states and exactly one way to move between
them:

* ``PROPOSED`` - somebody, or something, thinks this is a lesson. Anyone may
  propose one. A model may propose one.
* ``VERIFIED`` - every claim in it resolves to a specific record from *this*
  incident: an audit entry, an execution, a validation cycle, an evidence node,
  a communication.

`verify` is the only way to reach VERIFIED, it takes the set of references the
incident actually produced, and it refuses a lesson whose citations do not all
resolve. There is no override, and a lesson can never be constructed VERIFIED,
because a platform that let anything mark its own recollection as institutional
memory would launder a guess into a fact - and the next incident would act on
it at three in the morning.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import Enum
from typing import Any, Mapping, Optional, Sequence


class LessonStatus(str, Enum):
    PROPOSED = "proposed"
    VERIFIED = "verified"


class LessonSource(str, Enum):
    DETERMINISTIC = "deterministic"
    """Derived by the platform from the incident's own records."""

    HUMAN = "human"
    """Written by a person in the review."""

    MODEL = "model"
    """Phrased or suggested by a model. Never verified on its own say-so."""


@dataclass(frozen=True)
class Citation:
    """One thing that can be opened to check a claim."""

    ref: str
    kind: str
    detail: str = ""

    def __post_init__(self) -> None:
        if not self.ref:
            raise ValueError("a citation must name something")


@dataclass(frozen=True)
class Lesson:
    id: str
    text: str
    citations: tuple[Citation, ...] = ()
    status: LessonStatus = LessonStatus.PROPOSED
    source: LessonSource = LessonSource.DETERMINISTIC
    unresolved: tuple[str, ...] = ()
    verified_at: Optional[datetime] = None

    def __post_init__(self) -> None:
        if not str(self.text or "").strip():
            raise ValueError("a lesson with no text is not a lesson")
        if self.status is LessonStatus.VERIFIED and not self.citations:
            raise ValueError(
                "a verified lesson cites the records that verify it; there is no "
                "path to VERIFIED without them"
            )

    def verify(
        self, available: Sequence[str], *, at: datetime
    ) -> "Lesson":
        """Promote to VERIFIED if - and only if - every citation resolves.

        Returns a lesson either way. A lesson that failed the gate comes back
        PROPOSED, carrying the refs that did not resolve, because "we thought
        this but could not show it" is itself worth reading.
        """
        if not self.citations:
            return replace(
                self,
                status=LessonStatus.PROPOSED,
                unresolved=("<no citation>",),
            )
        known = set(available)
        missing = tuple(c.ref for c in self.citations if c.ref not in known)
        if missing:
            return replace(self, status=LessonStatus.PROPOSED, unresolved=missing)
        return replace(
            self,
            status=LessonStatus.VERIFIED,
            unresolved=(),
            verified_at=at,
        )


@dataclass(frozen=True)
class Section:
    key: str
    title: str
    lines: tuple[str, ...] = ()
    citations: tuple[Citation, ...] = ()

    @property
    def empty(self) -> bool:
        return not self.lines


REQUIRED_SECTIONS: tuple[tuple[str, str], ...] = (
    ("summary", "Incident summary"),
    ("impact", "Impact"),
    ("timeline", "Timeline"),
    ("detection", "Detection"),
    ("changes", "Contributing changes"),
    ("hypotheses", "Hypotheses considered"),
    ("evidence", "Evidence that confirmed or rejected them"),
    ("actions", "Actions taken"),
    ("approvals", "Approvals"),
    ("uncertain", "Uncertain outcomes and reconciliation"),
    ("validation", "Validation"),
    ("communications", "Communications and corrections"),
    ("worked", "What worked"),
    ("failed", "What failed"),
    ("lessons", "Lessons"),
    ("followups", "Follow-up actions"),
)
"""Every section a review must have, in the order a reviewer reads them.

Fixed rather than configurable. A review whose awkward sections could be left
out would leave out the awkward sections.
"""


@dataclass(frozen=True)
class PostIncidentReview:
    incident_id: str
    tenant_id: str
    reference: str
    title: str
    app_id: str
    generated_at: datetime
    sections: tuple[Section, ...]
    lessons: tuple[Lesson, ...]
    followups: tuple[str, ...] = ()
    chain_valid: bool = True
    chain_detail: str = ""
    degraded: bool = True
    model: str = "deterministic"
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @property
    def verified_lessons(self) -> tuple[Lesson, ...]:
        return tuple(
            lesson for lesson in self.lessons if lesson.status is LessonStatus.VERIFIED
        )

    @property
    def proposed_lessons(self) -> tuple[Lesson, ...]:
        return tuple(
            lesson for lesson in self.lessons if lesson.status is LessonStatus.PROPOSED
        )

    def section(self, key: str) -> Section:
        for section in self.sections:
            if section.key == key:
                return section
        raise KeyError(key)

    def as_markdown(self) -> str:
        """The exported document. Deterministic, so two exports of one incident
        are byte-identical and a diff between them means something changed."""
        out = [f"# Post-incident review: {self.reference} {self.title}", ""]
        if not self.chain_valid:
            out += [
                "> **THIS REVIEW IS NOT EVIDENCE.** " + self.chain_detail,
                "",
            ]
        for section in self.sections:
            out.append(f"## {section.title}")
            if section.empty:
                out.append("_Nothing was recorded for this section._")
            else:
                out += [f"- {line}" for line in section.lines]
            out.append("")
        out.append("## Lessons")
        for lesson in self.lessons:
            marker = "VERIFIED" if lesson.status is LessonStatus.VERIFIED else "PROPOSED"
            refs = ", ".join(c.ref for c in lesson.citations) or "no citation"
            out.append(f"- **[{marker}]** {lesson.text} _(cites: {refs})_")
        out.append("")
        return "\n".join(out)
