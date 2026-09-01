"""Progressive evidence: broad, then aggregate, then narrow (REQ-054).

The naive way to answer "why is this incident happening" with a model is to put
the transcript, the telemetry and the change log into a prompt. It works once,
on a small incident, and then it is a per-incident cost that scales with how bad
the incident is - the P0 with six hours of bridge traffic being exactly the one
that blows the budget, and exactly the one where a truncated-at-random prompt
does the most damage.

So evidence is gathered in three passes, and each is bounded before the next
begins:

1. **BROAD** - cheap, structured, no model. What kinds of thing exist and how
   many: 340 anomalies across 3 families, 2 change windows, 61 bridge messages,
   4 prior incidents above the relevance floor. Counts, not content.
2. **AGGREGATE** - the same material collapsed into rows a person would write:
   top families, the storm's clusters, the change windows by service, the
   statement kinds by count. Still no free text beyond bounded labels.
3. **NARROW** - and only now, per hypothesis, under a query budget: the
   handful of items that would actually discriminate. This is the only pass
   that carries excerpts, each one capped and each one carrying a `result_ref`
   so the full thing is one click away rather than in the prompt.

Nothing here talks to a store. The plan is a value object; the application layer
executes it against repositories and adapters, which is what makes budgets,
caps, redaction and reproducibility testable without a database.

**What the model received is recorded.** Not by copying the prompt - which would
make every evidence bundle a second, unredacted copy of the incident - but by
storing the refs, the caps and the plan version. That is enough to reproduce the
retrieval exactly, and it is the reproducibility an auditor actually asks for.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Mapping, Optional, Sequence

from signalforge.domain.brain.errors import BrainValidationError

PLAN_VERSION = "1"

MAX_EXCERPT_CHARS = 600
"""One item's excerpt. Small on purpose: an excerpt is a pointer with enough
context to recognise, and `result_ref` is how the whole thing is read."""

MAX_ITEMS_PER_QUERY = 12
MAX_QUERIES_PER_HYPOTHESIS = 4
"""The per-hypothesis query budget the sprint asks for. Four narrow queries per
hypothesis, times three hypotheses, is twelve retrievals per governor pass - a
number that stays the same whether the incident is ten minutes or ten hours
old."""
MAX_TOTAL_ITEMS = 60


class Stage(str, Enum):
    BROAD = "broad"
    AGGREGATE = "aggregate"
    NARROW = "narrow"


class SourceKind(str, Enum):
    """Where a piece of evidence can come from. Closed, because it is a metric
    label and because an agent inventing a source is an agent inventing
    evidence."""

    TELEMETRY = "telemetry"
    PROBE = "probe"
    ALERT = "alert"
    CHANGE = "change"
    TOPOLOGY = "topology"
    BRIDGE = "bridge"
    GRAPH = "graph"
    HISTORY = "history"
    KNOWLEDGE = "knowledge"


@dataclass(frozen=True)
class EvidenceQuery:
    """One bounded retrieval. The unit the budget counts."""

    source: SourceKind
    stage: Stage
    purpose: str
    """Why this query is being run, in a few words. Rendered on the drill-down
    and stored on the bundle, so "what did it look at and why" is answerable
    without re-running anything."""
    limit: int = MAX_ITEMS_PER_QUERY
    subject: str = ""
    hypothesis_key: str = ""
    filters: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.limit < 1 or self.limit > MAX_ITEMS_PER_QUERY:
            raise BrainValidationError(
                f"a query limit of {self.limit} is outside 1..{MAX_ITEMS_PER_QUERY}; "
                "the cap is the cost control, so it is not negotiable per call site"
            )
        if not self.purpose:
            raise BrainValidationError("an evidence query must say what it is for")


@dataclass(frozen=True)
class EvidenceItem:
    """One retrieved thing, bounded and attributable.

    `excerpt` is what a prompt may see; `result_ref` is where the whole thing
    lives. `untrusted` marks text this platform did not write, and it is set by
    the retriever rather than inferred later, because by the time it reaches a
    prompt builder the origin is no longer visible.
    """

    ref: str
    source: SourceKind
    label: str
    excerpt: str = ""
    result_ref: str = ""
    at: Optional[datetime] = None
    untrusted: bool = False
    redacted: bool = False
    authority: str = "observed"
    detail: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "excerpt", str(self.excerpt or "")[:MAX_EXCERPT_CHARS]
        )
        if not self.ref:
            raise BrainValidationError(
                "every evidence item needs a ref; an item that cannot be cited "
                "cannot be used to ground an answer"
            )


@dataclass(frozen=True)
class EvidenceBundle:
    """What one gathering pass produced, and enough to reproduce it.

    The reproducibility contract is `plan_version` + `queries` + `refs`: given
    the same store at the same sequence number, running these queries returns
    these refs. The prompt itself is deliberately not stored - it would be a
    second copy of incident content with its own retention and redaction
    problems, and it is derivable from what is here.
    """

    incident_id: str
    stage: Stage
    gathered_at: datetime
    queries: tuple[EvidenceQuery, ...] = ()
    items: tuple[EvidenceItem, ...] = ()
    counts: Mapping[str, int] = field(default_factory=dict)
    summaries: tuple[str, ...] = ()
    truncated: bool = False
    budget_exhausted: bool = False
    plan_version: str = PLAN_VERSION
    detail: Mapping[str, Any] = field(default_factory=dict)

    @property
    def refs(self) -> tuple[str, ...]:
        return tuple(item.ref for item in self.items)

    @property
    def fingerprint(self) -> str:
        """Stable over the same retrieval. Two bundles with the same fingerprint
        put the same evidence in front of the model."""
        payload = "|".join(
            sorted(f"{item.ref}:{len(item.excerpt)}" for item in self.items)
        )
        return hashlib.sha256(
            f"{self.plan_version}#{payload}".encode("utf-8")
        ).hexdigest()[:24]

    def merged(self, other: "EvidenceBundle") -> "EvidenceBundle":
        """Fold a later stage into an earlier one, keeping the caps.

        Dedupes on `ref`, so the same change window found by the broad pass and
        again by a narrow one is one item - and the narrow version, which
        carries the excerpt, wins.
        """
        by_ref: dict[str, EvidenceItem] = {item.ref: item for item in self.items}
        for item in other.items:
            existing = by_ref.get(item.ref)
            if existing is None or len(item.excerpt) > len(existing.excerpt):
                by_ref[item.ref] = item
        items = tuple(by_ref.values())[:MAX_TOTAL_ITEMS]
        counts = dict(self.counts)
        for key, value in other.counts.items():
            counts[key] = counts.get(key, 0) + value
        return EvidenceBundle(
            incident_id=self.incident_id,
            stage=other.stage,
            gathered_at=other.gathered_at,
            queries=self.queries + other.queries,
            items=items,
            counts=counts,
            summaries=self.summaries + other.summaries,
            truncated=self.truncated
            or other.truncated
            or len(by_ref) > MAX_TOTAL_ITEMS,
            budget_exhausted=self.budget_exhausted or other.budget_exhausted,
            detail={**dict(self.detail), **dict(other.detail)},
        )


@dataclass(frozen=True)
class QueryBudget:
    """How much retrieval one governor pass is allowed.

    Separate from the token budget and enforced first, because a query that is
    never run costs no tokens: bounding the *retrieval* is a cheaper and more
    predictable control than bounding what happens after it.
    """

    per_hypothesis: int = MAX_QUERIES_PER_HYPOTHESIS
    total: int = 20
    max_items: int = MAX_TOTAL_ITEMS

    def __post_init__(self) -> None:
        if self.per_hypothesis < 1 or self.total < 1:
            raise BrainValidationError("a query budget must allow at least one query")


class BudgetLedger:
    """Counts queries as they are spent. Not a domain entity - a scratch counter
    for one gathering pass - and it lives here so the rule and its enforcement
    are in the same file."""

    def __init__(self, budget: Optional[QueryBudget] = None):
        self.budget = budget or QueryBudget()
        self._per_hypothesis: dict[str, int] = {}
        self._total = 0
        self.refused = 0

    @property
    def spent(self) -> int:
        return self._total

    def allows(self, hypothesis_key: str = "") -> bool:
        if self._total >= self.budget.total:
            return False
        if hypothesis_key:
            return (
                self._per_hypothesis.get(hypothesis_key, 0)
                < self.budget.per_hypothesis
            )
        return True

    def take(self, hypothesis_key: str = "") -> bool:
        """Spend one query. False means the budget refused it, and the caller
        stops rather than continuing quietly with less evidence than it thinks
        it has."""
        if not self.allows(hypothesis_key):
            self.refused += 1
            return False
        self._total += 1
        if hypothesis_key:
            self._per_hypothesis[hypothesis_key] = (
                self._per_hypothesis.get(hypothesis_key, 0) + 1
            )
        return True

    def summary(self) -> dict[str, int]:
        return {
            "queries": self._total,
            "refused": self.refused,
            "limit": self.budget.total,
        }


def broad_plan() -> tuple[EvidenceQuery, ...]:
    """Pass one: what exists, in counts. No excerpts, no model, no free text."""
    return (
        EvidenceQuery(
            source=SourceKind.GRAPH,
            stage=Stage.BROAD,
            purpose="what the incident graph already holds",
        ),
        EvidenceQuery(
            source=SourceKind.TELEMETRY,
            stage=Stage.BROAD,
            purpose="which signal families are anomalous, and how many",
        ),
        EvidenceQuery(
            source=SourceKind.CHANGE,
            stage=Stage.BROAD,
            purpose="changes in flight around the onset",
        ),
        EvidenceQuery(
            source=SourceKind.TOPOLOGY,
            stage=Stage.BROAD,
            purpose="the map version and what it says is downstream",
        ),
    )


def aggregate_plan() -> tuple[EvidenceQuery, ...]:
    """Pass two: the same material collapsed into rows worth reading."""
    return (
        EvidenceQuery(
            source=SourceKind.TELEMETRY,
            stage=Stage.AGGREGATE,
            purpose="the worst signal per family, with its measured value",
            limit=8,
        ),
        EvidenceQuery(
            source=SourceKind.ALERT,
            stage=Stage.AGGREGATE,
            purpose="alert clusters rather than individual alerts",
            limit=6,
        ),
        EvidenceQuery(
            source=SourceKind.BRIDGE,
            stage=Stage.AGGREGATE,
            purpose="what has been said that changes what is believed",
            limit=10,
        ),
        EvidenceQuery(
            source=SourceKind.HISTORY,
            stage=Stage.AGGREGATE,
            purpose="prior incidents above the relevance floor",
            limit=5,
        ),
    )


def narrow_plan(
    hypothesis_key: str, *, subject: str = "", budget: Optional[QueryBudget] = None
) -> tuple[EvidenceQuery, ...]:
    """Pass three: what would discriminate *this* hypothesis, and nothing else.

    Ordered by cost and by discriminating power together - telemetry about the
    named subject first, then the change that touched it, then what a person
    said about it - and truncated to the per-hypothesis budget, so a fourth
    hypothesis cannot quietly cost four more queries.
    """
    limit = (budget or QueryBudget()).per_hypothesis
    queries = (
        EvidenceQuery(
            source=SourceKind.TELEMETRY,
            stage=Stage.NARROW,
            purpose=f"measurements on {subject or 'the subject of this hypothesis'}",
            subject=subject,
            hypothesis_key=hypothesis_key,
            limit=6,
        ),
        EvidenceQuery(
            source=SourceKind.CHANGE,
            stage=Stage.NARROW,
            purpose=f"changes touching {subject or 'this subject'}",
            subject=subject,
            hypothesis_key=hypothesis_key,
            limit=4,
        ),
        EvidenceQuery(
            source=SourceKind.BRIDGE,
            stage=Stage.NARROW,
            purpose=f"what anyone said about {subject or 'this subject'}",
            subject=subject,
            hypothesis_key=hypothesis_key,
            limit=6,
        ),
        EvidenceQuery(
            source=SourceKind.PROBE,
            stage=Stage.NARROW,
            purpose="probe results that would falsify this",
            subject=subject,
            hypothesis_key=hypothesis_key,
            limit=4,
        ),
    )
    return queries[:limit]
