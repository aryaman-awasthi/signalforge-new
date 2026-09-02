"""Next best action: deterministic scoring and plan revision (REQ-063).

The score is arithmetic. Not a model's opinion, not a prompt, not a ranking a
language model produced and a template explained - arithmetic over numbers this
module derives from the incident's own state, in a formula a person can check on
paper:

    score = urgency x impact x information_gain - risk

Multiplication for the first three because they are *conjunctive*: an action
that would tell you a great deal about an incident nobody is being hurt by is
not the next thing to do, and neither is a decisive fix for a problem you have
not identified. Any one of them near zero should sink the product, and it does.
Subtraction for risk because risk is a *toll*, not a discount - a dangerous
action is worth doing when the payoff is large, and the formula should say so
rather than scaling the payoff away.

The LLM's role is one sentence of `rationale` written *from* this score, and the
score is computed and stored before the model is asked. If the model is
unavailable the recommendation is unchanged and the rationale is the
deterministic sentence this module already wrote (`explain`).

## Eligibility comes before ranking

A candidate that cannot be performed right now is not ranked - it is listed with
`eligible = False` and the reason, and it can never be the primary
recommendation. Three gates:

- **policy**: the engine returned BLOCK;
- **permission**: nobody in the room holds what approving it would need;
- **prerequisite**: the action's declared preconditions are not met.

Showing an impossible action at the top of a war-room screen at 03:00 is how
fifteen minutes get spent discovering it is impossible.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import Enum
from typing import Any, Mapping, Optional, Sequence

from signalforge.contracts.canonical import ActionClass
from signalforge.domain.governance.errors import GovernanceValidationError
from signalforge.domain.governance.policy import PolicyDecision, RiskClass

# --- the weights ---------------------------------------------------------------

SEVERITY_URGENCY: Mapping[str, float] = {"P0": 1.0, "P1": 0.7, "P2": 0.4}
"""How fast this has to happen. Read from the incident's severity, which a human
set or confirmed - never from a model's view of how bad things look."""

CUSTOMER_FACING_URGENCY_BONUS = 0.1

MITIGATION_POWER: Mapping[ActionClass, float] = {
    ActionClass.ROLLBACK: 1.0,
    ActionClass.FAILOVER: 0.9,
    ActionClass.TRAFFIC_SHIFT: 0.85,
    ActionClass.RESTART: 0.7,
    ActionClass.CONFIG_CHANGE: 0.65,
    ActionClass.CUSTOMER_IMPACTING: 0.6,
    ActionClass.DIAGNOSTIC: 0.1,
    ActionClass.OTHER: 0.3,
}
"""How much of the impact this class of action can remove when it is the right
action. Multiplied by how confident the platform is that it *is* the right one,
so a powerful action attached to a weak hypothesis scores low."""

INFORMATION_GAIN: Mapping[ActionClass, float] = {
    ActionClass.DIAGNOSTIC: 1.0,
    ActionClass.ROLLBACK: 0.5,
    ActionClass.CONFIG_CHANGE: 0.35,
    ActionClass.RESTART: 0.3,
    ActionClass.TRAFFIC_SHIFT: 0.3,
    ActionClass.FAILOVER: 0.25,
    ActionClass.CUSTOMER_IMPACTING: 0.2,
    ActionClass.OTHER: 0.2,
}
"""What you learn by doing it. A rollback scores well here as well as on impact:
if rolling back fixes it, the change was the cause, and that is the single most
useful thing an incident can learn."""

RISK_TOLL: Mapping[RiskClass, float] = {
    RiskClass.READ_ONLY: 0.0,
    RiskClass.LOW_RISK: 0.1,
    RiskClass.MEDIUM_RISK: 0.3,
    RiskClass.HIGH_RISK: 0.6,
}

REVERSIBLE_RISK_FACTOR = 0.5
"""An action you can undo costs half. Reversibility is the cheapest risk control
there is and the scoring should reward declaring it honestly."""

IDEMPOTENT_RISK_RELIEF = 0.05

BASE_VALIDATION_S = 60
PER_CHECK_VALIDATION_S = 30


class Eligibility(str, Enum):
    ELIGIBLE = "eligible"
    BLOCKED_BY_POLICY = "blocked_by_policy"
    NO_APPROVER = "no_approver"
    MISSING_PREREQUISITE = "missing_prerequisite"
    DISABLED = "disabled"

    @property
    def ok(self) -> bool:
        return self is Eligibility.ELIGIBLE


class Disposition(str, Enum):
    """How one action fared across a plan revision."""

    KEPT = "kept"
    PAUSED = "paused"
    ADDED = "added"


@dataclass(frozen=True)
class Score:
    """The four numbers and the product, kept apart so the card can show them.

    Stored rather than recomputed on read: "why was this the recommendation an
    hour ago" has to be answerable from the row, and a formula that changed in a
    later release would otherwise rewrite history.
    """

    urgency: float
    impact: float
    information_gain: float
    risk: float

    def __post_init__(self) -> None:
        for name in ("urgency", "impact", "information_gain", "risk"):
            value = float(getattr(self, name))
            if not 0.0 <= value <= 1.0:
                raise GovernanceValidationError(
                    f"{name} must be within [0, 1]; got {value}"
                )
            object.__setattr__(self, name, round(value, 4))

    @property
    def value(self) -> float:
        return round(
            self.urgency * self.impact * self.information_gain - self.risk, 4
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "score": self.value,
            "urgency": self.urgency,
            "impact": self.impact,
            "information_gain": self.information_gain,
            "risk": self.risk,
        }


@dataclass(frozen=True)
class ActionCandidate:
    """One thing that could be done, with everything a commander needs to choose.

    `rationale` is the only field a language model may write, and it is written
    *after* `score` exists. `explain()` is what fills it when there is no model.
    """

    action_id: str
    name: str
    action_class: ActionClass
    risk_class: RiskClass
    score: Score
    decision: PolicyDecision
    eligibility: Eligibility
    eligibility_reason: str = ""
    hypothesis_key: str = ""
    hypothesis_label: str = ""
    hypothesis_confidence: float = 0.0
    evidence_refs: tuple[str, ...] = ()
    prerequisites: tuple[str, ...] = ()
    unmet_prerequisites: tuple[str, ...] = ()
    expected_validation_s: int = BASE_VALIDATION_S
    rollback_action_id: Optional[str] = None
    rollback_summary: str = ""
    reversible: bool = False
    idempotent: bool = False
    params_schema: Mapping[str, Any] = field(default_factory=dict)
    """The application's own JSON schema for this action's arguments, carried
    straight from its manifest.

    It is on the candidate because the cockpit cannot otherwise know what an
    action needs. Without it the Run button posts `args: {}` at an action that
    declares a required `region` and gets a 422 nobody can act on - which is what
    it did until S8-T10 walked the demo. The platform does not invent a value:
    it asks, and the Tool Gateway validates the answer against this same schema
    before anything is authorised."""
    rationale: str = ""
    rationale_degraded: bool = True
    """True until a model wrote it. The cockpit renders the badge from this, so
    a deterministic sentence is never presented as model reasoning."""

    @property
    def value(self) -> float:
        return self.score.value

    @property
    def eligible(self) -> bool:
        return self.eligibility.ok

    @property
    def requires_approval(self) -> bool:
        return self.decision.needs_approval

    def explain(self) -> str:
        """The deterministic rationale. Written from the numbers, always present.

        This is the floor the model improves on rather than replaces: if the
        gateway to the model is down, this sentence is what the war room reads,
        and it says the same thing the score says.
        """
        parts = [
            f"{self.name} scores {self.value:+.2f}",
            f"urgency {self.score.urgency:.2f} x impact {self.score.impact:.2f} "
            f"x information gain {self.score.information_gain:.2f} "
            f"- risk {self.score.risk:.2f}",
        ]
        if self.hypothesis_label:
            parts.append(
                f"addresses \"{self.hypothesis_label}\" "
                f"({self.hypothesis_confidence:.0%} confidence)"
            )
        if self.decision.needs_approval:
            parts.append(
                "needs approval by "
                + ", ".join(r.value for r in self.decision.approver_roles)
            )
        if not self.eligible:
            parts.append(f"not available now: {self.eligibility_reason}")
        return "; ".join(parts) + "."

    def with_rationale(self, text: str, *, degraded: bool) -> "ActionCandidate":
        return replace(self, rationale=text.strip(), rationale_degraded=degraded)

    def as_dict(self) -> dict[str, Any]:
        return {
            "action_id": self.action_id,
            "name": self.name,
            "action_class": self.action_class.value,
            "risk_class": self.risk_class.value,
            **self.score.as_dict(),
            "eligible": self.eligible,
            "eligibility": self.eligibility.value,
            "eligibility_reason": self.eligibility_reason,
            "policy": self.decision.as_dict(),
            "requires_approval": self.decision.needs_approval,
            "approver_roles": [r.value for r in self.decision.approver_roles],
            "hypothesis_key": self.hypothesis_key,
            "hypothesis_label": self.hypothesis_label,
            "hypothesis_confidence": round(self.hypothesis_confidence, 4),
            "evidence_refs": list(self.evidence_refs),
            "prerequisites": list(self.prerequisites),
            "unmet_prerequisites": list(self.unmet_prerequisites),
            "expected_validation_s": self.expected_validation_s,
            "rollback_action_id": self.rollback_action_id,
            "rollback_summary": self.rollback_summary,
            "reversible": self.reversible,
            "params_schema": dict(self.params_schema or {}),
            "rationale": self.rationale or self.explain(),
            "rationale_degraded": self.rationale_degraded,
        }


def urgency_of(severity: str, *, customer_facing: bool) -> float:
    base = SEVERITY_URGENCY.get(str(severity).upper(), 0.4)
    if customer_facing:
        base += CUSTOMER_FACING_URGENCY_BONUS
    return min(1.0, round(base, 4))


def impact_of(
    action_class: ActionClass,
    *,
    hypothesis_confidence: float,
    customer_facing: bool,
) -> float:
    """How much of the harm this action can remove.

    The power of the class, scaled by how sure the platform is that it is
    attacking the right thing. A rollback aimed at a 20%-confidence hypothesis is
    a 20%-confidence rollback, and the arithmetic should say that out loud rather
    than let "rollback is powerful" carry the ranking.
    """
    power = MITIGATION_POWER.get(action_class, 0.3)
    confidence = max(0.0, min(1.0, float(hypothesis_confidence)))
    # A floor of 0.15 so that an action with no hypothesis attached is not
    # multiplied out of existence: sometimes the right move is to restart the
    # thing that is obviously down before anybody has a theory about why.
    scaled = power * max(0.15, confidence)
    if customer_facing:
        scaled = min(1.0, scaled * 1.1)
    return round(min(1.0, scaled), 4)


def information_gain_of(
    action_class: ActionClass, *, discriminating: bool = False
) -> float:
    base = INFORMATION_GAIN.get(action_class, 0.2)
    if discriminating:
        # This action is the test that tells two live hypotheses apart - the
        # single most valuable thing to do at minute ten.
        base = min(1.0, base + 0.3)
    return round(base, 4)


def risk_of(
    risk_class: RiskClass, *, reversible: bool, idempotent: bool
) -> float:
    toll = RISK_TOLL[risk_class]
    if reversible:
        toll *= REVERSIBLE_RISK_FACTOR
    if idempotent:
        toll = max(0.0, toll - IDEMPOTENT_RISK_RELIEF)
    return round(min(1.0, toll), 4)


def expected_validation_s(verification_checks: int, *, stability_window_s: int) -> int:
    """How long before anybody will know whether it worked.

    The stability window dominates on purpose: a change is not validated when the
    first probe goes green, it is validated when it *stays* green, and a card
    that promised "30 seconds" would set a false expectation the closure gate
    then refuses to meet.
    """
    return int(
        BASE_VALIDATION_S
        + PER_CHECK_VALIDATION_S * max(0, int(verification_checks))
        + max(0, int(stability_window_s))
    )


def rank(candidates: Sequence[ActionCandidate]) -> tuple[ActionCandidate, ...]:
    """Eligible first, then by score, then by action id.

    The third key is what makes the ranking *stable*: two actions that score
    identically must come back in the same order on every call, or the top
    recommendation flickers between refreshes and nobody trusts the panel.
    """
    return tuple(
        sorted(
            candidates,
            key=lambda c: (0 if c.eligible else 1, -c.value, c.action_id),
        )
    )


@dataclass(frozen=True)
class Recommendation:
    """What the panel shows: one primary, the alternatives, and a fallback."""

    primary: Optional[ActionCandidate]
    alternatives: tuple[ActionCandidate, ...] = ()
    ineligible: tuple[ActionCandidate, ...] = ()
    fallback: Optional[ActionCandidate] = None
    """The next eligible candidate whose risk class is strictly lower than the
    primary's - "if you are not ready to roll back, do this instead". A less
      dangerous option is only useful if it is genuinely less dangerous."""
    generated_at: Optional[datetime] = None
    degraded: bool = True

    @property
    def empty(self) -> bool:
        return self.primary is None

    def as_dict(self) -> dict[str, Any]:
        return {
            "primary": self.primary.as_dict() if self.primary else None,
            "alternatives": [c.as_dict() for c in self.alternatives],
            "ineligible": [c.as_dict() for c in self.ineligible],
            "fallback": self.fallback.as_dict() if self.fallback else None,
            "generated_at": (
                self.generated_at.isoformat() if self.generated_at else None
            ),
            "degraded": self.degraded,
        }


def recommend(
    candidates: Sequence[ActionCandidate], *, at: Optional[datetime] = None
) -> Recommendation:
    ranked = rank(candidates)
    eligible = [c for c in ranked if c.eligible]
    ineligible = tuple(c for c in ranked if not c.eligible)
    primary = eligible[0] if eligible else None
    alternatives = tuple(eligible[1:])
    fallback = next(
        (
            candidate
            for candidate in alternatives
            if primary is not None
            and candidate.risk_class.rank < primary.risk_class.rank
        ),
        None,
    )
    return Recommendation(
        primary=primary,
        alternatives=alternatives,
        ineligible=ineligible,
        fallback=fallback,
        generated_at=at,
        degraded=all(c.rationale_degraded for c in ranked) if ranked else True,
    )


# --- plan revision -------------------------------------------------------------


@dataclass(frozen=True)
class PlanItem:
    action_id: str
    name: str
    disposition: Disposition
    score: float
    reason: str = ""
    changed_assumption: str = ""
    evidence_refs: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "action_id": self.action_id,
            "name": self.name,
            "disposition": self.disposition.value,
            "score": round(self.score, 4),
            "reason": self.reason,
            "changed_assumption": self.changed_assumption,
            "evidence_refs": list(self.evidence_refs),
        }


@dataclass(frozen=True)
class ResponsePlan:
    """A version of "what we are going to do", kept beside its predecessors.

    Never replaced in place. When evidence moves, `revise` produces version n+1
    whose items are marked KEPT, PAUSED or ADDED against version n, and both
    versions stay readable - because "we stopped doing that at 03:14 because the
    pool metric came back clean" is the sentence a post-incident review is made
    of, and a plan that silently rewrote itself would have deleted it.
    """

    id: str
    tenant_id: str
    incident_id: str
    version: int
    created_at: datetime
    created_by: str
    items: tuple[PlanItem, ...] = ()
    changed_assumptions: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    previous_version: Optional[int] = None
    note: str = ""
    detail: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.version < 1:
            raise GovernanceValidationError("plan versions start at 1")

    @property
    def kept(self) -> tuple[PlanItem, ...]:
        return tuple(i for i in self.items if i.disposition is Disposition.KEPT)

    @property
    def paused(self) -> tuple[PlanItem, ...]:
        return tuple(i for i in self.items if i.disposition is Disposition.PAUSED)

    @property
    def added(self) -> tuple[PlanItem, ...]:
        return tuple(i for i in self.items if i.disposition is Disposition.ADDED)

    def as_dict(self) -> dict[str, Any]:
        return {
            "plan_id": self.id,
            "incident_id": self.incident_id,
            "version": self.version,
            "previous_version": self.previous_version,
            "created_at": self.created_at.isoformat(),
            "created_by": self.created_by,
            "items": [item.as_dict() for item in self.items],
            "kept": [item.action_id for item in self.kept],
            "paused": [item.action_id for item in self.paused],
            "added": [item.action_id for item in self.added],
            "changed_assumptions": list(self.changed_assumptions),
            "evidence_refs": list(self.evidence_refs),
            "note": self.note,
        }


def revise_plan(
    previous: Optional[ResponsePlan],
    candidates: Sequence[ActionCandidate],
    *,
    plan_id: str,
    tenant_id: str,
    incident_id: str,
    at: datetime,
    by: str,
    changed_assumptions: Sequence[str] = (),
    evidence_refs: Sequence[str] = (),
    note: str = "",
) -> ResponsePlan:
    """Version n+1, with every action labelled against version n.

    An action that was in the previous plan and is still eligible is KEPT. One
    that was in it and is no longer eligible - or no longer scores above zero -
    is PAUSED, with the reason. One that was not in it is ADDED. Nothing is
    dropped: a paused action stays on the screen with its reason, because "we
    considered that and stopped" is information and an empty row is not.
    """
    eligible = [c for c in rank(candidates) if c.eligible]
    current = {c.action_id: c for c in rank(candidates)}
    before = {item.action_id for item in previous.items} if previous else set()
    assumption = changed_assumptions[0] if changed_assumptions else ""

    items: list[PlanItem] = []
    for candidate in eligible:
        disposition = (
            Disposition.KEPT if candidate.action_id in before else Disposition.ADDED
        )
        items.append(
            PlanItem(
                action_id=candidate.action_id,
                name=candidate.name,
                disposition=disposition,
                score=candidate.value,
                reason=candidate.rationale or candidate.explain(),
                changed_assumption=(
                    assumption if disposition is Disposition.ADDED else ""
                ),
                evidence_refs=candidate.evidence_refs,
            )
        )
    for action_id in sorted(before - {c.action_id for c in eligible}):
        items.append(
            _paused_item(
                action_id,
                candidate=current.get(action_id),
                previous=previous,
                assumption=assumption,
            )
        )
    return ResponsePlan(
        id=plan_id,
        tenant_id=tenant_id,
        incident_id=incident_id,
        version=(previous.version + 1) if previous else 1,
        created_at=at,
        created_by=by,
        items=tuple(items),
        changed_assumptions=tuple(dict.fromkeys(changed_assumptions))[:10],
        evidence_refs=tuple(dict.fromkeys(evidence_refs))[:20],
        previous_version=previous.version if previous else None,
        note=note,
    )


def _paused_item(
    action_id: str,
    *,
    candidate: Optional[ActionCandidate],
    previous: Optional[ResponsePlan],
    assumption: str,
) -> PlanItem:
    """An action the last plan carried and this one will not run.

    Kept on the screen rather than dropped, with whatever the platform can still
    say about it: the current candidate's reason if it is still a candidate, the
    previous plan's name if it is not.
    """
    name = action_id
    if candidate is not None:
        name = candidate.name
    elif previous is not None:
        earlier = next((i for i in previous.items if i.action_id == action_id), None)
        if earlier is not None:
            name = earlier.name
    reason = "no longer available or no longer worth doing"
    if candidate is not None and candidate.eligibility_reason:
        reason = candidate.eligibility_reason
    return PlanItem(
        action_id=action_id,
        name=name,
        disposition=Disposition.PAUSED,
        score=candidate.value if candidate else 0.0,
        reason=reason,
        changed_assumption=assumption,
        evidence_refs=candidate.evidence_refs if candidate else (),
    )
