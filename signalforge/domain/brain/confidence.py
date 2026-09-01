"""Hypothesis confidence: deterministic, weighted, and explainable (REQ-053).

The rule this module exists to enforce is short: **a model never produces a
number.** An LLM asked "how confident are you?" answers 83% with the same
fluency whether it has three corroborating signals or none, and that number then
travels onto a screen an incident commander is making decisions from. So
confidence here is arithmetic over evidence the platform actually holds, and the
model's only job is to write the sentence explaining a delta the arithmetic
already computed.

The policy, in full:

1. Every piece of evidence linked to a hypothesis carries a **kind** and a
   **stance** (supports or refutes). The kind decides its base weight - a
   deterministic table below, arguable line by line, which is the point.
2. The base weight is scaled by the evidence's **authority**: an authoritative
   source (the change system saying a deploy happened) counts fully, an observed
   one nearly so, an inferred one - anything a model concluded - counts a
   quarter. That is what stops a chain of model inferences from manufacturing
   certainty.
3. Weights are summed, supporting positive, refuting negative, and squashed
   through a logistic into (0, 1). A logistic rather than a ratio because it has
   the right shape at the ends: the tenth corroborating signal moves the number
   much less than the second, which is how evidence actually works.
4. A hypothesis with no evidence at all sits at `PRIOR`, not at zero. Zero would
   mean "ruled out", and a brand-new hypothesis has not been ruled out; it is
   merely unevidenced, and the screen says so.

Every recomputation produces a `ConfidenceEntry` naming the delta, the evidence
that moved it and the arithmetic, so "why did hypothesis #2 move from 65% to
32%?" is answered from stored numbers rather than from a narrative.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Mapping, Optional, Sequence

from signalforge.contracts.envelope import Authority
from signalforge.domain.brain.errors import BrainValidationError

PRIOR = 0.35
"""Where a hypothesis with no evidence sits. Below even odds - most hypotheses
raised in the first ten minutes of an incident are wrong - and well clear of
zero, which would read as "ruled out"."""

MIN_CONFIDENCE = 0.02
MAX_CONFIDENCE = 0.97
"""Never 0 and never 1. A platform that reported certainty about a running
incident would be claiming something no evidence in this system can support, and
the two ends are exactly where an operator stops reading the evidence."""

LOGISTIC_SCALE = 1.6
"""How much one unit of net weight moves the number. Tuned so that a single
strong corroborated signal moves a fresh hypothesis from the prior to roughly
two thirds, and two independent ones to roughly four fifths."""


class Stance(str, Enum):
    SUPPORTS = "supports"
    REFUTES = "refutes"

    @property
    def sign(self) -> int:
        return 1 if self is Stance.SUPPORTS else -1


class EvidenceKind(str, Enum):
    """What sort of thing is being offered as evidence.

    A closed vocabulary with a published weight each, because the alternative -
    a free-text kind with a weight chosen per call site - is a scoring policy
    nobody can review. Adding a kind is a deliberate act with a number attached.
    """

    TELEMETRY_ANOMALY = "telemetry_anomaly"
    SLO_BURN = "slo_burn"
    PROBE_FAILURE = "probe_failure"
    ALERT_CLUSTER = "alert_cluster"
    HEALTH_SCORE = "health_score"
    CORROBORATION = "corroboration"
    CHANGE_PROXIMITY = "change_proximity"
    TOPOLOGY_PATH = "topology_path"
    HUMAN_STATEMENT = "human_statement"
    HISTORICAL_SIMILARITY = "historical_similarity"
    CONTRADICTION = "contradiction"
    DIAGNOSTIC_RESULT = "diagnostic_result"


BASE_WEIGHTS: Mapping[EvidenceKind, float] = {
    # Measured, first-hand, and about the thing that is broken.
    EvidenceKind.TELEMETRY_ANOMALY: 1.0,
    EvidenceKind.SLO_BURN: 1.1,
    EvidenceKind.PROBE_FAILURE: 1.2,
    # A storm of alerts is one fact about one fault, not fifty facts.
    EvidenceKind.ALERT_CLUSTER: 0.7,
    EvidenceKind.HEALTH_SCORE: 0.6,
    # Two independent families agreeing is the strongest thing this platform
    # can say on its own (S4's corroboration rule).
    EvidenceKind.CORROBORATION: 1.5,
    # Deliberately mid-weight: temporal proximity to a deploy is a strong hint
    # and is *not* causality, and this number is where that belief is written
    # down. A change window alone must not carry a hypothesis past even odds.
    EvidenceKind.CHANGE_PROXIMITY: 0.9,
    EvidenceKind.TOPOLOGY_PATH: 0.8,
    # A person on the bridge saying something is real evidence and is not
    # measurement. Weighted below a probe on purpose.
    EvidenceKind.HUMAN_STATEMENT: 0.8,
    # Advisory only. "This caused it last time" must never on its own carry a
    # hypothesis - the whole failure mode Historical Learning has to avoid.
    EvidenceKind.HISTORICAL_SIMILARITY: 0.45,
    # A direct contradiction is heavy, in whichever direction it points.
    EvidenceKind.CONTRADICTION: 1.3,
    EvidenceKind.DIAGNOSTIC_RESULT: 1.4,
}

ADVISORY_KINDS: frozenset[EvidenceKind] = frozenset(
    {EvidenceKind.HISTORICAL_SIMILARITY}
)
"""Evidence about a *different* incident.

A prior incident that resembles this one is worth saying and is not a
measurement of the system that is broken now. A low base weight is not enough on
its own to keep it honest: four similar incidents all pointing the same way sum
to more than one corroborated measurement, and the platform would then be
asserting a cause from history alone - the exact failure Historical Learning has
to avoid."""

ADVISORY_CAP = 0.35
"""The most the advisory kinds may contribute *in total*, in either direction.

Chosen against the prior and the logistic scale so that a hypothesis supported
by nothing but history stays below even odds however many prior incidents agree,
and so that history alone can never rule one out either. Firm evidence is
unaffected: one real measurement outweighs the whole advisory budget, which is
the ordering this platform wants."""

AUTHORITY_FACTOR: Mapping[Authority, float] = {
    Authority.AUTHORITATIVE: 1.0,
    Authority.OBSERVED: 0.9,
    # Anything a model concluded. A quarter, so that a chain of inferences
    # cannot manufacture certainty out of nothing measured.
    Authority.INFERRED: 0.25,
}


@dataclass(frozen=True)
class WeightedEvidence:
    """One evidence link, as the scorer sees it.

    `node_id` is carried so a history entry can cite the exact node that moved
    the number, and `strength` lets a caller express "this anomaly is 3 sigma,
    that one is barely over" without inventing a new kind.
    """

    node_id: str
    kind: EvidenceKind
    stance: Stance
    authority: Authority = Authority.OBSERVED
    strength: float = 1.0
    label: str = ""

    def __post_init__(self) -> None:
        if not 0.0 <= self.strength <= 2.0:
            raise BrainValidationError(
                f"evidence strength {self.strength} is outside [0, 2]; strength "
                "scales a published weight, it does not replace it"
            )

    @property
    def weight(self) -> float:
        """The signed contribution this link makes to the net weight."""
        base = BASE_WEIGHTS[self.kind]
        factor = AUTHORITY_FACTOR.get(self.authority, 0.25)
        return self.stance.sign * base * factor * self.strength


def score(evidence: Sequence[WeightedEvidence]) -> float:
    """The confidence for one hypothesis. Pure, total, and order-independent.

    Order-independence matters more than it looks: two agents merging evidence
    in different orders after a redelivery must land on the same number, or the
    history fills with deltas nobody caused.
    """
    if not evidence:
        return PRIOR
    # Anchored at the prior: with a net weight of zero - as much against as for -
    # the answer is the prior, not a coin flip.
    raw = _logit(PRIOR) + LOGISTIC_SCALE * net_weight(evidence)
    return _clamp(1.0 / (1.0 + math.exp(-raw)))


def net_weight(evidence: Sequence[WeightedEvidence]) -> float:
    """The signed weight a hypothesis carries, with the advisory budget applied.

    Split rather than summed, because the two halves are different kinds of
    claim. Firm evidence is a measurement of the system that is broken now and
    is summed in full. Advisory evidence is about a *different* incident, and
    however much of it there is, it is capped - so "this caused it before,
    therefore this is the cause" is arithmetically unavailable rather than
    discouraged.
    """
    firm = sum(item.weight for item in evidence if item.kind not in ADVISORY_KINDS)
    advisory = sum(item.weight for item in evidence if item.kind in ADVISORY_KINDS)
    if abs(advisory) > ADVISORY_CAP:
        advisory = math.copysign(ADVISORY_CAP, advisory)
    return firm + advisory


def _logit(probability: float) -> float:
    return math.log(probability / (1.0 - probability))


def _clamp(value: float) -> float:
    return max(MIN_CONFIDENCE, min(MAX_CONFIDENCE, value))


@dataclass(frozen=True)
class ConfidenceEntry:
    """One movement, with the arithmetic that caused it (REQ-053).

    This is the row the "Why did #2 move from 65% to 32%?" panel renders. It
    holds the numbers *and* the evidence ids, so the narrative underneath is
    decoration: strip the model out entirely and an operator can still read the
    delta and click through to what moved it.
    """

    hypothesis_key: str
    at: datetime
    previous: Optional[float]
    current: float
    net_weight: float
    supporting: tuple[str, ...] = ()
    refuting: tuple[str, ...] = ()
    added: tuple[str, ...] = ()
    """Evidence node ids new since the previous entry - the actual cause of the
    delta, as opposed to everything that happens to be attached now."""
    removed: tuple[str, ...] = ()
    trigger: str = ""
    narrative: str = ""
    """One sentence a model may write about why this moved. Never the source of
    a number, and empty is a perfectly good value - the deterministic
    explanation below is always populated."""
    narrative_degraded: bool = False
    detail: Mapping[str, Any] = field(default_factory=dict)

    @property
    def delta(self) -> float:
        return self.current - (self.previous if self.previous is not None else PRIOR)

    @property
    def direction(self) -> str:
        if abs(self.delta) < 0.005:
            return "steady"
        return "rose" if self.delta > 0 else "fell"

    def explain(self) -> str:
        """The deterministic explanation, always available.

        Written from the stored numbers, so it says the same thing whether or
        not a model was reachable. The model's sentence, when there is one, sits
        *beside* this rather than replacing it.
        """
        previous = "no prior score" if self.previous is None else f"{self.previous:.0%}"
        head = (
            f"{previous} -> {self.current:.0%} ({self.direction}, "
            f"net weight {self.net_weight:+.2f})"
        )
        parts = [head]
        if self.added:
            parts.append(f"{len(self.added)} new piece(s) of evidence")
        parts.append(
            f"{len(self.supporting)} for, {len(self.refuting)} against"
        )
        if self.trigger:
            parts.append(f"recomputed after {self.trigger}")
        return "; ".join(parts)


def entry_for(
    *,
    hypothesis_key: str,
    at: datetime,
    previous: Optional[float],
    evidence: Sequence[WeightedEvidence],
    previous_ids: Sequence[str] = (),
    trigger: str = "",
) -> ConfidenceEntry:
    """Score a hypothesis and describe the movement in one step.

    Kept together deliberately: a caller that could compute a score without
    producing a history entry would eventually be written, and then a
    confidence would move with nothing recording why.
    """
    current = score(evidence)
    now_ids = {item.node_id for item in evidence}
    before = {node_id for node_id in previous_ids if node_id}
    return ConfidenceEntry(
        hypothesis_key=hypothesis_key,
        at=at,
        previous=previous,
        current=current,
        net_weight=net_weight(evidence),
        supporting=tuple(
            sorted(item.node_id for item in evidence if item.stance is Stance.SUPPORTS)
        ),
        refuting=tuple(
            sorted(item.node_id for item in evidence if item.stance is Stance.REFUTES)
        ),
        added=tuple(sorted(now_ids - before)),
        removed=tuple(sorted(before - now_ids)),
        trigger=trigger,
    )


def discriminating_need(
    evidence: Sequence[WeightedEvidence], confidence: float
) -> str:
    """What would most move this hypothesis next.

    Deterministic and deliberately blunt. A hypothesis sitting near the prior
    with nothing measured against it needs *any* measurement; one that is high
    on a single family needs a second family; one that is high and corroborated
    needs a diagnostic that could falsify it. Stating the next test is what
    turns a confidence number into something an operator can act on.
    """
    kinds = {item.kind for item in evidence}
    if not evidence:
        return (
            "Nothing measured is attached yet: any telemetry, probe or change "
            "evidence would move this."
        )
    if EvidenceKind.CORROBORATION not in kinds and len(kinds) < 2:
        return (
            "Only one family of signal supports this. A second, independent "
            "family - a probe or an SLO burn - would corroborate or break it."
        )
    if not any(item.stance is Stance.REFUTES for item in evidence):
        return (
            "Nothing has been tried that could disprove this. A diagnostic that "
            "would fail if this were true is the next useful step."
        )
    if confidence >= 0.7:
        return (
            "Confident but unfalsified: run the cheapest check that would be "
            "negative if this hypothesis were wrong."
        )
    return (
        "Evidence points both ways. The next useful step is the check that "
        "separates this from the competing hypothesis."
    )
