"""Learning from incidents this platform has already run (REQ-052).

The value is obvious and so is the failure mode. "We saw this three weeks ago and
it was the connection pool" saves twenty minutes when it is right, and costs an
hour when it is wrong and nobody said it was a guess. So everything in this
module is built to make the second case impossible to mistake for the first:

- Similarity is a **score**, never a verdict. Nothing here returns a cause.
- A prior incident's outcome is offered as `HISTORICAL_SIMILARITY` evidence,
  whose weight in `domain/brain/confidence.py` is the lowest of any kind and
  cannot on its own carry a hypothesis past even odds.
- **Relevance decays with age.** An incident from last week is different
  evidence from one two years ago in an estate that has been rebuilt since, and
  the decay is applied to the score rather than left to the reader.
- **A mistake warning is a first-class output.** The most useful thing history
  offers is often not "this was the cause" but "we spent forty minutes on this
  hypothesis last time and it was wrong" - and that is a warning pinned to a
  hypothesis, not a suggestion to adopt it.

The fingerprint is structured, not textual, and that is deliberate: two incidents
are similar when the same *kinds of things* were wrong in the same *shape* of
estate, not when somebody happened to write a similar title. The optional
embedding cosine (from the same TCS gateway that serves everything else) is
blended in as a *minority* term, so a degraded embeddings path lowers precision
and never breaks the feature.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime
from typing import Mapping, Optional, Sequence

HALF_LIFE_DAYS = 120.0
"""How long before an incident's relevance halves. Four months: long enough that
last quarter's outage still counts, short enough that an estate two rebuilds ago
does not dominate."""

MIN_SIMILARITY = 0.25
"""Below this, the two incidents have nothing meaningful in common and offering
one as guidance would be noise wearing a percentage."""

STRUCTURAL_WEIGHT = 0.7
SEMANTIC_WEIGHT = 0.3
"""The blend. Structure dominates on purpose: the embedding is there to catch
"pool exhaustion" against "connections maxed out", not to decide the answer."""


@dataclass(frozen=True)
class IncidentFingerprint:
    """One incident reduced to the features similarity is computed over.

    Every field is something the platform recorded rather than something a model
    decided, so two fingerprints are comparable without either having been
    through a model.
    """

    incident_id: str
    reference: str
    app_id: str
    title: str
    declared_at: datetime
    severity: str = ""
    signal_families: frozenset[str] = frozenset()
    services: frozenset[str] = frozenset()
    regions: frozenset[str] = frozenset()
    change_types: frozenset[str] = frozenset()
    customer_facing: bool = False
    corroborated: bool = False
    closed_at: Optional[datetime] = None
    closure_reason: str = ""
    confirmed_cause: str = ""
    """The hypothesis that was standing when the incident closed, if one was.
    Advisory: it is what the room concluded, not a proven root cause."""
    ruled_out: tuple[str, ...] = ()
    """Hypotheses that were retracted - what the room chased and dropped. The
    source of mistake warnings, and often the more useful half."""
    text: str = ""
    """A short natural-language summary, used only for the optional embedding
    term."""

    @property
    def topology_shape(self) -> frozenset[str]:
        """The services and regions together: "roughly this part of the estate"."""
        return self.services | self.regions


def jaccard(left: frozenset[str], right: frozenset[str]) -> float:
    if not left and not right:
        return 0.0
    union = left | right
    if not union:
        return 0.0
    return len(left & right) / len(union)


def cosine(left: Sequence[float], right: Sequence[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    dot = sum(a * b for a, b in zip(left, right))
    norm = math.sqrt(sum(a * a for a in left)) * math.sqrt(sum(b * b for b in right))
    if norm == 0:
        return 0.0
    # Clamped: floating point can put a cosine a hair over 1 and a similarity
    # of 100.4% on a screen destroys confidence in every other number on it.
    return max(0.0, min(1.0, dot / norm))


def structural_similarity(
    current: IncidentFingerprint, prior: IncidentFingerprint
) -> tuple[float, dict[str, float]]:
    """How alike two incidents are, by what was measured. Returns the score and
    its components, because a similarity nobody can decompose is a similarity
    nobody should act on."""
    parts = {
        "signal_families": jaccard(current.signal_families, prior.signal_families),
        "services": jaccard(current.services, prior.services),
        "regions": jaccard(current.regions, prior.regions),
        "change_types": jaccard(current.change_types, prior.change_types),
        "same_app": 1.0 if current.app_id == prior.app_id else 0.0,
        "severity": 1.0 if current.severity and current.severity == prior.severity else 0.0,
    }
    weights = {
        "signal_families": 0.28,
        "services": 0.24,
        "regions": 0.08,
        "change_types": 0.16,
        "same_app": 0.16,
        "severity": 0.08,
    }
    total = sum(parts[key] * weights[key] for key in parts)
    return round(total, 4), parts


def decay(prior: IncidentFingerprint, *, now: datetime) -> float:
    """Relevance as a function of age. Exponential with a half life, floored at
    a tenth so that a genuinely identical incident from two years ago still
    appears - faintly - rather than disappearing."""
    reference = prior.closed_at or prior.declared_at
    age_days = max(0.0, (now - reference).total_seconds() / 86_400.0)
    return max(0.1, 0.5 ** (age_days / HALF_LIFE_DAYS))


@dataclass(frozen=True)
class SimilarIncident:
    """One prior incident offered as guidance, with everything needed to judge
    it: the raw similarity, the decay applied, the components, and the outcome."""

    fingerprint: IncidentFingerprint
    similarity: float
    relevance: float
    """`similarity * decay`. What the list is ordered by and what the screen
    shows, because an operator comparing "82% similar" between a two-year-old
    incident and last week's is comparing the wrong numbers."""
    decay_factor: float
    components: Mapping[str, float] = field(default_factory=dict)
    semantic: Optional[float] = None
    """The embedding cosine, when embeddings were available. `None` means the
    gateway was degraded and the score is structural only - which the screen
    says, rather than silently showing a lower number."""
    previous_relevance: Optional[float] = None
    """What this incident's relevance was at the last computation. The
    "relevance movement" S14 renders: an incident becoming *more* relevant as
    evidence arrives is the interesting event."""

    @property
    def movement(self) -> float:
        if self.previous_relevance is None:
            return 0.0
        return round(self.relevance - self.previous_relevance, 4)

    @property
    def outcome(self) -> str:
        if self.fingerprint.confirmed_cause:
            return self.fingerprint.confirmed_cause
        if self.fingerprint.closure_reason:
            return self.fingerprint.closure_reason
        return "no cause was recorded when it closed"

    def caveat(self) -> str:
        """The sentence that must travel with this row, everywhere it is shown."""
        return (
            "This is a prior incident that resembles the current one. It is "
            "guidance, not a finding about this incident: nothing here has been "
            "measured on the system that is broken now."
        )


def rank(
    current: IncidentFingerprint,
    priors: Sequence[IncidentFingerprint],
    *,
    now: datetime,
    semantic: Optional[Mapping[str, float]] = None,
    previous: Optional[Mapping[str, float]] = None,
    limit: int = 5,
    minimum: float = MIN_SIMILARITY,
) -> tuple[SimilarIncident, ...]:
    """The prior incidents worth showing, most relevant first.

    `semantic` is an optional map of incident id to embedding cosine. Absent -
    because the gateway degraded - the structural score stands alone at full
    weight rather than being penalised for a missing term, which would make a
    degraded model path look like a lack of similarity.
    """
    found: list[SimilarIncident] = []
    for prior in priors:
        if prior.incident_id == current.incident_id:
            continue
        structural, components = structural_similarity(current, prior)
        cosine_score = (semantic or {}).get(prior.incident_id)
        if cosine_score is None:
            blended = structural
        else:
            blended = (
                STRUCTURAL_WEIGHT * structural + SEMANTIC_WEIGHT * float(cosine_score)
            )
        if blended < minimum:
            continue
        factor = decay(prior, now=now)
        found.append(
            SimilarIncident(
                fingerprint=prior,
                similarity=round(blended, 4),
                relevance=round(blended * factor, 4),
                decay_factor=round(factor, 4),
                components=components,
                semantic=cosine_score,
                previous_relevance=(previous or {}).get(prior.incident_id),
            )
        )
    found.sort(key=lambda item: (-item.relevance, item.fingerprint.incident_id))
    return tuple(found[:limit])


@dataclass(frozen=True)
class MistakeWarning:
    """What went wrong in the reasoning last time, pinned where it is useful.

    `hypothesis_hint` is the text of a hypothesis that was pursued and dropped,
    so the current incident's matching hypothesis can carry the warning on its
    own card rather than in a general "history" list nobody reads at minute six.
    """

    source_incident_id: str
    source_reference: str
    hypothesis_hint: str
    detail: str
    relevance: float
    occurred_at: Optional[datetime] = None

    def __str__(self) -> str:
        return (
            f"{self.source_reference}: {self.detail} "
            f"(guidance from a prior incident, not a finding about this one)"
        )


def mistake_warnings(
    similar: Sequence[SimilarIncident], *, limit: int = 4
) -> tuple[MistakeWarning, ...]:
    """Hypotheses a similar incident chased and ruled out.

    Deliberately drawn from `ruled_out` rather than from `confirmed_cause`. "It
    was the pool last time" invites the room to adopt a conclusion; "we spent
    forty minutes on the pool last time and it was not the pool" invites the room
    to test one, which is the behaviour worth encouraging.
    """
    warnings: list[MistakeWarning] = []
    for item in similar:
        for hypothesis in item.fingerprint.ruled_out[:2]:
            warnings.append(
                MistakeWarning(
                    source_incident_id=item.fingerprint.incident_id,
                    source_reference=item.fingerprint.reference,
                    hypothesis_hint=hypothesis,
                    detail=(
                        f"a similar incident pursued “{hypothesis}” and "
                        "ruled it out"
                    ),
                    relevance=item.relevance,
                    occurred_at=item.fingerprint.declared_at,
                )
            )
    warnings.sort(key=lambda warning: -warning.relevance)
    return tuple(warnings[:limit])
