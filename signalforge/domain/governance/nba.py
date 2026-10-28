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


# --- causal fit: an action must address the mechanism, not the symptom ----------
#
# The four scoring terms above say how *powerful* and how *dangerous* an action
# is. None of them says whether it acts on the thing that is actually broken,
# and without that a rollback - the highest mitigation power in the table - wins
# every ranking in which a rollback exists, including the incident whose root
# cause is a full filesystem. "A recent deployment exists" is not evidence that
# the deployment caused the fault, and a plan built on that reasoning restarts
# the estate without freeing a byte.
#
# So each candidate is asked one question before it is ranked: how does this
# action remove the identified failure mechanism? An action that cannot answer
# it from the evidence is not eliminated - sometimes it is all there is - but it
# is scored as what it is and it cannot be the primary recommendation while
# something that can answer it is available.

CAUSAL_UNSUPPORTED_FACTOR = 0.15
"""What an action's impact is worth when nothing connects it to the mechanism.

Not zero: an unexplained action is still an action, and a war room with no
causally-supported option left should still see its least-bad choice ranked
rather than hidden."""

CAUSAL_INDIRECT_FACTOR = 0.45
"""For an action that slows the mechanism without removing it - shifting traffic
away from a filling disk buys time and does not reclaim a byte. Worth doing when
nothing direct is available, never the first answer when something direct is."""

DEPLOYMENT_ROOT_CAUSES: frozenset[str] = frozenset(
    {"deployment_change", "configuration_change"}
)
"""Root causes for which undoing the change is, by construction, the thing that
removes the mechanism."""

DEPLOYMENT_CAUSAL_MARKER = "deployment_induced"
"""The marker a mechanism carries when the evidence links a deployment to the
failure - `deployment_induced_log_growth`, say. The brain writes it only when
that link is evidenced, so this, and never the bare existence of a change window,
is what makes a rollback causally supported for some other root cause."""

REGIONAL_ROOT_CAUSES: frozenset[str] = frozenset(
    {"infrastructure_fault", "regional_fault"}
)

REMEDIATION_KEYWORDS: Mapping[str, tuple[str, ...]] = {
    # Matched against the action's own name and description, because the class
    # of an action ("config_change") says nothing about what it acts on and the
    # application's manifest is where "rotate-and-compress-logs" is written down.
    "license_expiry": (
        # "licen" covers license and licence. Nothing capacity-shaped belongs
        # here: scaling, throttling and restarting do not renew an entitlement.
        "licen", "renew", "entitlement", "activation", "subscription",
        "re-activate", "reactivate",
    ),
    "disk_saturation": (
        "log rotat", "logrotate", "rotate log", "rotate", "compress", "clean",
        "cleanup", "purge", "prune", "truncate", "reclaim", "evict", "vacuum",
        "archive", "retention", "disk", "filesystem", "file system", "volume",
        "storage", "capacity", "expand", "resize", "grow", "inode", "temp",
        "tmp", "cache", "core dump", "coredump", "garbage", "free space",
        "log level", "log volume", "verbosity", "debug logging",
    ),
    "memory_exhaustion": (
        "memory", "heap", "cache", "evict", "garbage", "gc", "limit", "scale",
        "restart", "leak",
    ),
    "cpu_saturation": (
        "cpu", "scale", "throttle", "rate limit", "shed", "concurrency",
        "worker", "queue",
    ),
    "connection_pool_exhaustion": (
        "pool", "connection", "timeout", "throttle", "shed", "limit", "scale",
        "restart", "recycle",
    ),
    "dependency_saturation": (
        "throttle", "rate limit", "shed", "scale", "pool", "timeout", "circuit",
        "retry", "backoff", "queue",
    ),
}

INDIRECT_RELIEF: Mapping[str, frozenset[ActionClass]] = {
    # Nothing slows an expired license down. Moving traffic moves it to the same
    # refusal; a restart re-reads the same lapsed key.
    "license_expiry": frozenset(),
    "disk_saturation": frozenset({ActionClass.TRAFFIC_SHIFT}),
    "memory_exhaustion": frozenset({ActionClass.TRAFFIC_SHIFT, ActionClass.RESTART}),
    "cpu_saturation": frozenset({ActionClass.TRAFFIC_SHIFT}),
    "connection_pool_exhaustion": frozenset(
        {ActionClass.TRAFFIC_SHIFT, ActionClass.RESTART}
    ),
    "dependency_saturation": frozenset({ActionClass.TRAFFIC_SHIFT}),
}


@dataclass(frozen=True)
class CausalFit:
    """Whether an action addresses the identified mechanism, and how directly."""

    supported: bool
    factor: float
    reason: str = ""

    @property
    def direct(self) -> bool:
        return self.supported and self.factor >= 1.0


NEUTRAL_FIT = CausalFit(True, 1.0, "")
"""No root cause has been established yet, so there is nothing to test an action
against. The ranking is exactly what it was before this check existed - the
platform does not penalise an action for a conclusion it has not reached."""


def is_action_causally_supported(
    root_cause: str,
    mechanism: str,
    action: Any,
    evidence: Sequence[str] = (),
) -> CausalFit:
    """How does this action directly address the identified failure mechanism?

    `action` is anything carrying `action_class`, `name` and (optionally)
    `description` - the application's own manifest entry, in its own words.
    `evidence` is the supporting evidence's labels, used the same way the
    mechanism is: as text that may establish a deployment link.

    Answerable from the evidence -> supported. Not answerable -> not the primary
    recommendation, whatever its mitigation power says.
    """
    cause = (root_cause or "").strip().lower()
    if not cause:
        return NEUTRAL_FIT

    action_class = getattr(action, "action_class", ActionClass.OTHER)
    text = " ".join(
        str(getattr(action, field_name, "") or "")
        for field_name in ("name", "description", "action_id")
    ).lower()
    corpus = " ".join(
        [(mechanism or "").strip().lower(), *(str(item).lower() for item in evidence)]
    )
    named = (mechanism or "").strip().lower().replace("_", " ") or cause.replace(
        "_", " "
    )

    if action_class is ActionClass.DIAGNOSTIC:
        # Reading the system is always causally defensible: the thing an
        # unproven mechanism most needs is the evidence that would prove it.
        return CausalFit(True, 1.0, f"gathers evidence about {named}")

    if action_class is ActionClass.ROLLBACK:
        if cause in DEPLOYMENT_ROOT_CAUSES or DEPLOYMENT_CAUSAL_MARKER in corpus:
            return CausalFit(
                True,
                1.0,
                f"the evidence connects a deployment to {named}, so undoing it "
                "removes the mechanism",
            )
        return CausalFit(
            False,
            CAUSAL_UNSUPPORTED_FACTOR,
            f"no evidence connects a deployment to {named}; a change near the "
            "onset is not evidence that it caused this",
        )

    if action_class is ActionClass.FAILOVER:
        if cause in REGIONAL_ROOT_CAUSES:
            return CausalFit(
                True, 1.0, f"{named} follows the region, so moving off it helps"
            )
        return CausalFit(
            False,
            CAUSAL_UNSUPPORTED_FACTOR,
            f"moves traffic elsewhere without removing {named}",
        )

    keywords = REMEDIATION_KEYWORDS.get(cause, ())
    if not keywords:
        # A root cause this table has no opinion about. Saying nothing is the
        # honest answer; inventing a verdict would be worse than the symptom
        # ranking this check exists to fix.
        return NEUTRAL_FIT
    if any(word in text for word in keywords):
        return CausalFit(True, 1.0, f"directly reduces {named}")
    if action_class in INDIRECT_RELIEF.get(cause, frozenset()):
        return CausalFit(
            True, CAUSAL_INDIRECT_FACTOR, f"slows {named} without removing it"
        )
    return CausalFit(
        False,
        CAUSAL_UNSUPPORTED_FACTOR,
        f"does not act on {named}",
    )


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
    root_cause: str = ""
    causal_mechanism: str = ""
    causally_supported: bool = True
    causal_fit: float = 1.0
    causal_reason: str = ""
    """Why this action does or does not address the identified mechanism.

    Defaults are the neutral answer, so a candidate built without a root cause -
    an incident the brain has not reasoned about yet, or a caller that does not
    supply one - ranks exactly as it did before this check existed."""

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
        if self.causal_reason:
            parts.append(
                ("causally supported: " if self.causally_supported else "not causally supported: ")
                + self.causal_reason
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
            "root_cause": self.root_cause,
            "causal_mechanism": self.causal_mechanism,
            "causally_supported": self.causally_supported,
            "causal_fit": round(self.causal_fit, 4),
            "causal_reason": self.causal_reason,
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
    causal_fit: float = 1.0,
) -> float:
    """How much of the harm this action can remove.

    The power of the class, scaled by how sure the platform is that it is
    attacking the right thing. A rollback aimed at a 20%-confidence hypothesis is
    a 20%-confidence rollback, and the arithmetic should say that out loud rather
    than let "rollback is powerful" carry the ranking.

    `causal_fit` is the second half of that same argument: confidence says how
    sure we are of the cause, and the fit says whether *this action* removes it.
    A rollback attached to a 90%-confidence disk-saturation hypothesis is a
    confident action against a mechanism it does not touch, and multiplying the
    power by the fit is where the arithmetic says so.
    """
    power = MITIGATION_POWER.get(action_class, 0.3) * max(0.0, min(1.0, causal_fit))
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


ACT_CONFIDENCE_THRESHOLD = 0.7
"""How sure the platform has to be about a mechanism before fixing it outranks
measuring it. The application's policy carries its own `confidence_threshold`
and the service passes that in; this is the domain's default when nothing does,
and it matches the onboarding profile's."""


def acts_on_established_mechanism(
    candidate: ActionCandidate, threshold: float
) -> bool:
    """Would this action *remove* a mechanism the evidence has already settled?

    True for an eligible, causally supported remediation whose hypothesis is at
    or above the threshold. A diagnostic is never one: reading the system is
    always defensible, but once the licence is 97% established, reading its
    configuration again is worth 3% of what it was worth at 50%, and the
    scoring - where a read-only action carries no risk toll at all - cannot say
    so on its own. This is the incident-command rule written down: diagnose
    until you are sure, then act, and do not keep diagnosing instead of acting.
    """
    return (
        candidate.eligible
        and candidate.causally_supported
        and candidate.action_class is not ActionClass.DIAGNOSTIC
        and candidate.hypothesis_confidence >= threshold
    )


def rank(
    candidates: Sequence[ActionCandidate],
    *,
    act_threshold: float = ACT_CONFIDENCE_THRESHOLD,
) -> tuple[ActionCandidate, ...]:
    """Eligible first, then causally supported, then what removes an
    established mechanism, then by score, then by action id.

    The causal tier is a tier rather than another term in the arithmetic because
    the requirement is categorical: an action nobody can connect to the
    mechanism must not be the primary recommendation while one that can is
    available, however powerful the unconnected one is. The third tier is
    categorical for the same reason in the other direction: a remediation that
    removes a mechanism the evidence has settled must not sit behind a
    diagnostic whose only merit is that it is free. Inside each tier the score
    decides, so the four numbers still do the ordering.

    The last key is what makes the ranking *stable*: two actions that score
    identically must come back in the same order on every call, or the top
    recommendation flickers between refreshes and nobody trusts the panel.
    """
    return tuple(
        sorted(
            candidates,
            key=lambda c: (
                0 if c.eligible else 1,
                0 if c.causally_supported else 1,
                0 if acts_on_established_mechanism(c, act_threshold) else 1,
                -c.value,
                c.action_id,
            ),
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
    candidates: Sequence[ActionCandidate],
    *,
    at: Optional[datetime] = None,
    act_threshold: float = ACT_CONFIDENCE_THRESHOLD,
) -> Recommendation:
    ranked = rank(candidates, act_threshold=act_threshold)
    eligible = [c for c in ranked if c.eligible]
    ineligible = tuple(c for c in ranked if not c.eligible)
    primary = eligible[0] if eligible else None
    alternatives = tuple(eligible[1:])
    if (
        primary is not None
        and primary.rationale_degraded
        and acts_on_established_mechanism(primary, act_threshold)
        and any(c.value > primary.value for c in alternatives)
    ):
        # The tier put it first over something that scores higher. The
        # deterministic sentence says so, because a card that reads "scores
        # -0.31" above a card that reads "+0.11" has to explain itself.
        primary = primary.with_rationale(
            f"Ranked first because it removes the established mechanism: "
            f"\"{primary.hypothesis_label}\" stands at "
            f"{primary.hypothesis_confidence:.0%}, above the "
            f"{act_threshold:.0%} the policy asks for before acting, and the "
            f"higher-scoring alternatives only measure it. {primary.rationale}",
            degraded=True,
        )
        alternatives = tuple(
            c for c in eligible[1:] if c.action_id != primary.action_id
        )
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
    act_threshold: float = ACT_CONFIDENCE_THRESHOLD,
) -> ResponsePlan:
    """Version n+1, with every action labelled against version n.

    An action that was in the previous plan and is still eligible is KEPT. One
    that was in it and is no longer eligible - or no longer scores above zero -
    is PAUSED, with the reason. One that was not in it is ADDED. Nothing is
    dropped: a paused action stays on the screen with its reason, because "we
    considered that and stopped" is information and an empty row is not.
    """
    # The same ordering `recommend` uses, under the same policy threshold: a
    # plan whose first row disagreed with the recommendation card beside it
    # would be two answers to one question.
    ranked = rank(candidates, act_threshold=act_threshold)
    eligible = [c for c in ranked if c.eligible]
    current = {c.action_id: c for c in ranked}
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
