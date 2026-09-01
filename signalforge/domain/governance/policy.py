"""The policy engine and the platform safety floor (REQ-060).

Two layers, and the whole design is in the relationship between them.

**The floor is code.** It is in this module, it is a constant, and there is no
configuration path to it. It is not a default that a document overrides, not a
validation the UI performs, and not a check a caller may skip: a `PolicyDocument`
that contradicts it cannot be constructed, so no reader anywhere can act on one.
An organisation that wanted rollbacks to auto-execute would have to change this
file and get it through review, which is exactly the bar that decision deserves.

**Per-app policy sits above it.** A `PolicyDocument` is a versioned, authored,
auditable YAML document that can make things *stricter* - block an action
outright, demand approval for something the floor would have allowed, shorten an
approval's life, narrow who may approve. Every field is one-directional: the
constructor refuses anything that would loosen the floor rather than silently
clamping it, because a policy that says one thing and does another is worse than
no policy at all.

## The floor, in one table

| Rule | What it guarantees |
|---|---|
| `FLOOR-HIGH-RISK-NO-AUTO` | the six high-risk classes never auto-execute - the best outcome they can reach is APPROVAL_REQUIRED |
| `FLOOR-HIGH-RISK-APPROVER` | their approvers must actually hold `approve_high_risk` in the frozen matrix |
| `FLOOR-READ-ONLY-NO-MUTATE` | nothing mutating can be classified read-only |
| `FLOOR-RISK-NEVER-LOWERED` | an override may raise an action's risk, never lower it |
| `FLOOR-APPROVAL-TTL-CAP` | an approval expires within one hour, always |
| `FLOOR-AUDIT-IMMUTABLE` | audit is not a configurable field; a document that names one is refused |
| `FLOOR-DISABLED-ACTION` | an action the operator disabled cannot execute |

## The decision

`PolicyEngine.decide` returns a `PolicyDecision` and never raises for a *denial*:
BLOCK is an answer, not an exception, because a blocked action still has to be
shown to an operator with the rule that blocked it. It raises only when asked
something incoherent (an action that is not in the application's manifest).

Every decision carries `rules_hit` - the ordered list of rules that fired, each
naming its source (`floor` or `app_policy`) - so the cockpit can render *why*
and an auditor can replay the reasoning six months later without the engine.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import Enum
from typing import Any, Iterable, Mapping, Optional, Sequence

from signalforge.contracts.canonical import (
    HIGH_RISK_ACTION_CLASSES,
    ActionClass,
    RiskLevel,
)
from signalforge.contracts.roles import ROLE_PERMISSIONS, Permission, Role
from signalforge.domain.governance.errors import (
    GovernanceValidationError,
    PolicyFloorViolation,
)
from signalforge.domain.onboarding.profile import (
    APPROVAL_TTL_CAP_S,
    MIN_APPROVAL_TTL_S,
    ExecutionMode,
    MappedAction,
)

# --- the floor's constants ----------------------------------------------------

FLOOR_VERSION = "floor-v1"
"""Stamped on every decision. A decision that was made under a different floor
is a different decision, and a replay has to be able to tell."""

MAX_APPROVAL_TTL_S = APPROVAL_TTL_CAP_S
"""One hour, taken from the onboarding floor rather than restated, so the two
cannot drift apart. Configuration may shorten it; nothing may lengthen it."""

APPROVE_HIGH_RISK_ROLES: frozenset[Role] = frozenset(
    role
    for role, permissions in ROLE_PERMISSIONS.items()
    if Permission.APPROVE_HIGH_RISK in permissions
)
"""Derived from the frozen matrix. Naming an Executive as the approver for a
rollback is a separation-of-duties violation and is refused where it is typed."""

FORBIDDEN_POLICY_KEYS: frozenset[str] = frozenset(
    {
        "audit",
        "audit_enabled",
        "auditing",
        "disable_audit",
        "audit_disabled",
        "skip_audit",
        "bypass_policy",
        "bypass_approval",
        "skip_approval",
    }
)
"""Keys whose *presence* is refused rather than ignored (`FLOOR-AUDIT-IMMUTABLE`).

Ignoring `audit: false` would leave an operator believing they had turned audit
off. Refusing the document tells them the truth."""


class RiskClass(str, Enum):
    """What the platform will do about an action, as opposed to how the
    application described it.

    Four classes because four different things happen: read-only never mutates,
    low risk may be configured to run on its own, medium always wants a person's
    assent, and high risk can never run without one.
    """

    READ_ONLY = "read_only"
    LOW_RISK = "low_risk"
    MEDIUM_RISK = "medium_risk"
    HIGH_RISK = "high_risk"

    @property
    def rank(self) -> int:
        return {"read_only": 0, "low_risk": 1, "medium_risk": 2, "high_risk": 3}[
            self.value
        ]

    @property
    def mutating(self) -> bool:
        return self is not RiskClass.READ_ONLY


class Outcome(str, Enum):
    """The three answers, and the only three."""

    ALLOW = "allow"
    APPROVAL_REQUIRED = "approval_required"
    BLOCK = "block"


class RuleSource(str, Enum):
    FLOOR = "floor"
    APP_POLICY = "app_policy"
    MANIFEST = "manifest"


NON_MUTATING_CLASSES: frozenset[ActionClass] = frozenset({ActionClass.DIAGNOSTIC})
"""The only class the platform will read as read-only.

`OTHER` is deliberately excluded: an application that declines to say what an
action does does not get the benefit of the doubt about whether it mutates."""


def classify(
    action_class: ActionClass,
    risk: RiskLevel,
    *,
    override: Optional[RiskLevel] = None,
) -> RiskClass:
    """The platform's risk class for one action. Pure, total, and the floor.

    High-risk classes are high risk whatever anybody declared or overrode - that
    branch is first and unconditional, and it is the reason an application
    publishing `risk: low` on a rollback changes nothing. Below that, an override
    may only raise.
    """
    if action_class in HIGH_RISK_ACTION_CLASSES:
        return RiskClass.HIGH_RISK
    effective = risk
    if override is not None and _risk_rank(override) > _risk_rank(risk):
        effective = override
    if effective is RiskLevel.HIGH:
        return RiskClass.HIGH_RISK
    if effective is RiskLevel.MEDIUM:
        return RiskClass.MEDIUM_RISK
    if action_class in NON_MUTATING_CLASSES:
        return RiskClass.READ_ONLY
    return RiskClass.LOW_RISK


def _risk_rank(level: RiskLevel) -> int:
    return {RiskLevel.LOW: 1, RiskLevel.MEDIUM: 2, RiskLevel.HIGH: 3}[level]


def classify_action(action: MappedAction) -> RiskClass:
    """The same classification for an action the wizard already mapped."""
    return classify(
        action.action_class, action.manifest_risk, override=action.risk_override
    )


@dataclass(frozen=True)
class RuleHit:
    """One rule that fired, in the words an operator will read on the card."""

    rule_id: str
    source: RuleSource
    effect: Outcome
    detail: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "source": self.source.value,
            "effect": self.effect.value,
            "detail": self.detail,
        }


# --- the per-app document ------------------------------------------------------


@dataclass(frozen=True)
class ActionRule:
    """What the application's policy says about one action, above the floor."""

    action_id: str
    blocked: bool = False
    require_approval: bool = False
    auto_execute: bool = False
    risk_override: Optional[RiskLevel] = None
    approver_roles: tuple[Role, ...] = ()
    approval_ttl_s: Optional[int] = None
    note: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "action_id": self.action_id,
            "blocked": self.blocked,
            "require_approval": self.require_approval,
            "auto_execute": self.auto_execute,
            "risk_override": self.risk_override.value if self.risk_override else None,
            "approver_roles": [role.value for role in self.approver_roles],
            "approval_ttl_s": self.approval_ttl_s,
            "note": self.note,
        }


@dataclass(frozen=True)
class PolicyDocument:
    """One version of one application's policy. Immutable, authored, hashed.

    Editing produces a new version rather than a mutation, for the reason every
    other record in this platform is append-only: "what were the rules when that
    rollback was approved" is a question somebody will ask, and a store that
    could answer only "the rules now" would make the approval unreviewable.

    The floor is checked in `__post_init__`, so a document that would weaken it
    does not exist even briefly. That is what makes this a domain floor rather
    than a validation: there is no path - a seed, a migration, a drill, an agent,
    a direct repository write - that produces a policy object the engine would
    then honour.
    """

    tenant_id: str
    app_id: str
    version: int
    created_at: datetime
    author_id: str
    author_label: str = ""
    note: str = ""
    approval_ttl_s: int = 900
    stability_window_s: int = 900
    stability_samples: int = 3
    max_action_retries: int = 1
    revalidation_cycles_before_escalation: int = 3
    blocked_classes: frozenset[ActionClass] = frozenset()
    require_approval_classes: frozenset[ActionClass] = field(
        default_factory=lambda: frozenset(HIGH_RISK_ACTION_CLASSES)
    )
    action_rules: Mapping[str, ActionRule] = field(default_factory=dict)
    approver_roles: tuple[Role, ...] = ()
    source: str = ""
    """The YAML the author submitted, kept verbatim. An auditor reads what was
    written, not a re-serialisation of what was parsed."""

    def __post_init__(self) -> None:
        if not self.tenant_id or not self.app_id:
            raise GovernanceValidationError(
                "a policy document belongs to exactly one application in one tenant"
            )
        if self.version < 1:
            raise GovernanceValidationError("policy versions start at 1")
        if not self.author_id:
            raise GovernanceValidationError(
                "a policy version names its author; an unattributed rule change "
                "is not auditable"
            )
        object.__setattr__(
            self, "require_approval_classes", frozenset(self.require_approval_classes)
        )
        object.__setattr__(self, "blocked_classes", frozenset(self.blocked_classes))
        object.__setattr__(self, "action_rules", dict(self.action_rules))
        self._assert_floor()

    # -- the floor ----------------------------------------------------------

    def _assert_floor(self) -> None:
        if not (MIN_APPROVAL_TTL_S <= int(self.approval_ttl_s) <= MAX_APPROVAL_TTL_S):
            raise PolicyFloorViolation(
                f"FLOOR-APPROVAL-TTL-CAP: approval_ttl_s must be between "
                f"{MIN_APPROVAL_TTL_S} and {MAX_APPROVAL_TTL_S} seconds; an "
                "approval that outlives the situation it was granted for is a "
                "standing permission nobody remembers giving"
            )
        missing = sorted(
            action_class.value
            for action_class in HIGH_RISK_ACTION_CLASSES
            if action_class not in self.require_approval_classes
        )
        if missing:
            raise PolicyFloorViolation(
                f"FLOOR-HIGH-RISK-NO-AUTO: the high-risk classes {missing} always "
                "require human approval; they cannot be removed from "
                "require_approval_classes"
            )
        powerless = sorted(
            role.value
            for role in self.approver_roles
            if role not in APPROVE_HIGH_RISK_ROLES
        )
        if powerless:
            raise PolicyFloorViolation(
                f"FLOOR-HIGH-RISK-APPROVER: {powerless} do not hold "
                "approve_high_risk in the roles matrix, so they cannot be the "
                "application's high-risk approvers - eligible roles are "
                f"{sorted(r.value for r in APPROVE_HIGH_RISK_ROLES)}"
            )
        if not (1 <= int(self.revalidation_cycles_before_escalation) <= 10):
            raise GovernanceValidationError(
                "revalidation_cycles_before_escalation must be between 1 and 10"
            )
        if not (1 <= int(self.stability_samples) <= 20):
            raise GovernanceValidationError(
                "stability_samples must be between 1 and 20"
            )
        if not (60 <= int(self.stability_window_s) <= 24 * 3_600):
            raise GovernanceValidationError(
                "stability_window_s must be between 60 seconds and 24 hours"
            )
        if not (0 <= int(self.max_action_retries) <= 5):
            raise GovernanceValidationError(
                "max_action_retries must be between 0 and 5"
            )
        for rule in self.action_rules.values():
            self._assert_rule_floor(rule)

    def _assert_rule_floor(self, rule: ActionRule) -> None:
        """Per-action floor checks that do not need the manifest.

        The ones that *do* need it - is this action id a rollback? - are made by
        `assert_against_actions` when the document is written against a known
        application, and again by the engine on every decision. Two checks rather
        than one because a policy can legitimately name an action whose manifest
        has not been imported yet, and the engine is the last gate either way.
        """
        if rule.auto_execute and rule.require_approval:
            raise GovernanceValidationError(
                f"action {rule.action_id!r} cannot both auto-execute and require "
                "approval; pick one"
            )
        if rule.approval_ttl_s is not None and not (
            MIN_APPROVAL_TTL_S <= int(rule.approval_ttl_s) <= MAX_APPROVAL_TTL_S
        ):
            raise PolicyFloorViolation(
                f"FLOOR-APPROVAL-TTL-CAP: action {rule.action_id!r} sets an "
                f"approval TTL outside {MIN_APPROVAL_TTL_S}..{MAX_APPROVAL_TTL_S}s"
            )
        powerless = sorted(
            role.value
            for role in rule.approver_roles
            if role not in APPROVE_HIGH_RISK_ROLES
        )
        if powerless:
            raise PolicyFloorViolation(
                f"FLOOR-HIGH-RISK-APPROVER: action {rule.action_id!r} names "
                f"{powerless} as approvers, and they do not hold approve_high_risk"
            )

    def assert_against_actions(self, actions: Iterable[MappedAction]) -> None:
        """The floor check that needs the application's manifest.

        This is where `rollback: auto_execute: true` dies. The class comes from
        the application's own action manifest - validated against the frozen
        schema at import - so an operator cannot escape the floor by renaming
        the action, and cannot reach it by inventing an action id either.
        """
        by_id = {action.action_id: action for action in actions}
        for rule in self.action_rules.values():
            action = by_id.get(rule.action_id)
            if action is None:
                continue
            risk_class = classify_action(action)
            if rule.auto_execute and risk_class is RiskClass.HIGH_RISK:
                raise PolicyFloorViolation(
                    f"FLOOR-HIGH-RISK-NO-AUTO: action {rule.action_id!r} is class "
                    f"{action.action_class.value!r}, which is high-risk. It can "
                    "never be configured to auto-execute - not by this policy, "
                    "not by an operator, not by an agent. Approval by a person "
                    "holding approve_high_risk is the only path to executing it."
                )
            if rule.auto_execute and risk_class is RiskClass.MEDIUM_RISK:
                raise PolicyFloorViolation(
                    f"FLOOR-HIGH-RISK-NO-AUTO: action {rule.action_id!r} is "
                    "medium risk; only read-only and low-risk actions may be "
                    "configured to run without a person"
                )
            if (
                rule.risk_override is not None
                and _risk_rank(rule.risk_override) < _risk_rank(action.manifest_risk)
            ):
                raise PolicyFloorViolation(
                    f"FLOOR-RISK-NEVER-LOWERED: {rule.action_id!r} was declared "
                    f"{action.manifest_risk.value!r} by the application; a policy "
                    "override may raise its risk, never lower it"
                )
            if (
                rule.risk_override is not None
                and action.action_class in HIGH_RISK_ACTION_CLASSES
                and rule.risk_override is not RiskLevel.HIGH
            ):
                raise PolicyFloorViolation(
                    f"FLOOR-RISK-NEVER-LOWERED: {rule.action_id!r} is class "
                    f"{action.action_class.value!r}; its risk cannot be overridden "
                    f"down to {rule.risk_override.value!r}"
                )
            if action.action_class not in NON_MUTATING_CLASSES and (
                rule.risk_override is None
                and rule.auto_execute
                and classify_action(action) is not RiskClass.LOW_RISK
            ):
                raise PolicyFloorViolation(
                    f"FLOOR-READ-ONLY-NO-MUTATE: {rule.action_id!r} mutates; it "
                    "cannot be treated as a read-only action"
                )

    # -- reads --------------------------------------------------------------

    @property
    def checksum(self) -> str:
        """A hash over what the document *means*, not over its formatting.

        Two versions with the same checksum are the same rules typed twice, and
        the editor uses that to avoid minting a version for a whitespace change.
        """
        payload = json.dumps(self.as_rules_dict(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]

    @property
    def ref(self) -> str:
        """How a decision, an approval and an execution name this document."""
        return f"{self.app_id}@v{self.version}"

    def rule_for(self, action_id: str) -> Optional[ActionRule]:
        return self.action_rules.get(action_id)

    def ttl_for(self, action_id: str) -> int:
        rule = self.action_rules.get(action_id)
        candidate = (
            rule.approval_ttl_s
            if rule is not None and rule.approval_ttl_s is not None
            else self.approval_ttl_s
        )
        # Belt and braces: the constructor already refused anything above the
        # cap, and this refuses it again at read time, because a TTL is the one
        # number a future bug could widen without any test noticing.
        return max(MIN_APPROVAL_TTL_S, min(int(candidate), MAX_APPROVAL_TTL_S))

    def as_rules_dict(self) -> dict[str, Any]:
        """The rules, normalised. The checksum's input and the dry-run's diff."""
        return {
            "approval_ttl_s": int(self.approval_ttl_s),
            "stability_window_s": int(self.stability_window_s),
            "stability_samples": int(self.stability_samples),
            "max_action_retries": int(self.max_action_retries),
            "revalidation_cycles_before_escalation": int(
                self.revalidation_cycles_before_escalation
            ),
            "blocked_classes": sorted(c.value for c in self.blocked_classes),
            "require_approval_classes": sorted(
                c.value for c in self.require_approval_classes
            ),
            "approver_roles": sorted(r.value for r in self.approver_roles),
            "actions": {
                action_id: rule.as_dict()
                for action_id, rule in sorted(self.action_rules.items())
            },
        }

    def next_version(
        self,
        *,
        at: datetime,
        author_id: str,
        author_label: str = "",
        note: str = "",
        **changes: Any,
    ) -> "PolicyDocument":
        return replace(
            self,
            version=self.version + 1,
            created_at=at,
            author_id=author_id,
            author_label=author_label,
            note=note,
            **changes,
        )


def default_policy(
    tenant_id: str, app_id: str, *, at: datetime, author_id: str
) -> PolicyDocument:
    """What an application gets before anybody edits anything.

    Deliberately the strictest thing that is still usable: nothing auto-executes,
    every high-risk class needs approval, the TTL is fifteen minutes.
    """
    return PolicyDocument(
        tenant_id=tenant_id,
        app_id=app_id,
        version=1,
        created_at=at,
        author_id=author_id,
        author_label="platform default",
        note="seeded default: nothing auto-executes",
    )


# --- the decision --------------------------------------------------------------


@dataclass(frozen=True)
class PolicyDecision:
    """The engine's answer, and everything a reader needs to act on it.

    Carries the full reasoning rather than a verdict, because three different
    audiences read the same object: the gateway (which acts on `outcome`), the
    cockpit (which renders `rules_hit`, `risk_class` and `approver_roles`) and an
    auditor replaying the incident (who needs `policy_ref` and `floor_version` to
    know which rules were in force).
    """

    outcome: Outcome
    action_id: str
    action_class: ActionClass
    risk_class: RiskClass
    reason: str
    rules_hit: tuple[RuleHit, ...] = ()
    approver_roles: tuple[Role, ...] = ()
    approval_ttl_s: int = 0
    policy_version: int = 0
    policy_ref: str = ""
    floor_version: str = FLOOR_VERSION
    reversible: bool = False
    reverse_action_id: Optional[str] = None
    idempotent: bool = False

    @property
    def allowed(self) -> bool:
        return self.outcome is Outcome.ALLOW

    @property
    def blocked(self) -> bool:
        return self.outcome is Outcome.BLOCK

    @property
    def needs_approval(self) -> bool:
        return self.outcome is Outcome.APPROVAL_REQUIRED

    @property
    def floor_rules(self) -> tuple[str, ...]:
        return tuple(
            hit.rule_id for hit in self.rules_hit if hit.source is RuleSource.FLOOR
        )

    def fingerprint(self, *, args_hash: str) -> str:
        """What an approval is bound to.

        The action, its arguments and the risk class the decision reached. An
        approval for "roll back payments to v11" is not an approval for "roll
        back payments to v9", and it is not an approval for the same action
        after the policy re-classified it.
        """
        payload = "|".join(
            [
                self.action_id,
                self.action_class.value,
                self.risk_class.value,
                str(self.policy_version),
                self.floor_version,
                args_hash,
            ]
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]

    def as_dict(self) -> dict[str, Any]:
        return {
            "outcome": self.outcome.value,
            "action_id": self.action_id,
            "action_class": self.action_class.value,
            "risk_class": self.risk_class.value,
            "reason": self.reason,
            "rules_hit": [hit.as_dict() for hit in self.rules_hit],
            "approver_roles": [role.value for role in self.approver_roles],
            "approval_ttl_s": self.approval_ttl_s,
            "policy_version": self.policy_version,
            "policy_ref": self.policy_ref,
            "floor_version": self.floor_version,
            "reversible": self.reversible,
            "reverse_action_id": self.reverse_action_id,
        }


class PolicyEngine:
    """Pure. No clock, no repository, no I/O - a function of (action, policy).

    That is deliberate and it is what makes the dry-run honest: the editor
    evaluates a *candidate* document with the same code the gateway runs, so
    "what would this change do" and "what does this do" cannot diverge.
    """

    @staticmethod
    def decide(
        action: MappedAction,
        policy: Optional[PolicyDocument],
        *,
        intent: Optional[ExecutionMode] = None,
    ) -> PolicyDecision:
        """The one decision function.

        `intent` is what the *caller* asked for, and it only ever narrows the
        answer: asking for AUTO on a high-risk action does not raise here, it
        produces a BLOCK naming the floor rule - because an attempt to
        auto-execute a rollback is an event a security review wants to see
        recorded, not an exception somebody's client swallowed.
        """
        hits: list[RuleHit] = []
        risk_class = classify_action(action)
        rule = policy.rule_for(action.action_id) if policy else None
        version = policy.version if policy else 0
        ref = policy.ref if policy else "none"
        ttl = policy.ttl_for(action.action_id) if policy else MIN_APPROVAL_TTL_S
        approvers = _approvers_for(action, policy, rule)

        def decision(outcome: Outcome, reason: str) -> PolicyDecision:
            return PolicyDecision(
                outcome=outcome,
                action_id=action.action_id,
                action_class=action.action_class,
                risk_class=risk_class,
                reason=reason,
                rules_hit=tuple(hits),
                approver_roles=approvers,
                approval_ttl_s=ttl if outcome is Outcome.APPROVAL_REQUIRED else 0,
                policy_version=version,
                policy_ref=ref,
                reversible=action.reversible,
                reverse_action_id=action.reverse_action_id,
                idempotent=action.idempotent,
            )

        # 1. Disabled at the manifest/wizard level. Nothing above can revive it.
        if not action.enabled:
            hits.append(
                RuleHit(
                    "FLOOR-DISABLED-ACTION",
                    RuleSource.FLOOR,
                    Outcome.BLOCK,
                    f"{action.action_id} is disabled for this application",
                )
            )
            return decision(Outcome.BLOCK, "the action is disabled")

        # 2. Blocked by the application's own policy.
        if policy is not None and action.action_class in policy.blocked_classes:
            hits.append(
                RuleHit(
                    "APP-BLOCKED-CLASS",
                    RuleSource.APP_POLICY,
                    Outcome.BLOCK,
                    f"policy blocks the whole class {action.action_class.value}",
                )
            )
            return decision(
                Outcome.BLOCK,
                f"this application's policy blocks every {action.action_class.value}",
            )
        if rule is not None and rule.blocked:
            hits.append(
                RuleHit(
                    "APP-BLOCKED-ACTION",
                    RuleSource.APP_POLICY,
                    Outcome.BLOCK,
                    rule.note or f"policy blocks {action.action_id}",
                )
            )
            return decision(
                Outcome.BLOCK, f"this application's policy blocks {action.action_id}"
            )

        # 3. The floor. High risk can never reach ALLOW, whatever was asked for.
        if risk_class is RiskClass.HIGH_RISK:
            hits.append(
                RuleHit(
                    "FLOOR-HIGH-RISK-NO-AUTO",
                    RuleSource.FLOOR,
                    Outcome.APPROVAL_REQUIRED,
                    f"{action.action_class.value} is a high-risk class: a person "
                    "holding approve_high_risk must approve it, and it can never "
                    "auto-execute",
                )
            )
            if intent is ExecutionMode.AUTO:
                # Not an error: a recorded refusal. The gateway audits this.
                hits.append(
                    RuleHit(
                        "FLOOR-AUTO-EXECUTE-REFUSED",
                        RuleSource.FLOOR,
                        Outcome.BLOCK,
                        "auto-execution was requested for a high-risk action and "
                        "refused by the platform safety floor",
                    )
                )
                return decision(
                    Outcome.BLOCK,
                    f"{action.action_id} is high risk and auto-execution was "
                    "requested; the platform safety floor refuses it",
                )
            if not approvers:
                hits.append(
                    RuleHit(
                        "FLOOR-HIGH-RISK-APPROVER",
                        RuleSource.FLOOR,
                        Outcome.BLOCK,
                        "no role that holds approve_high_risk is named as an "
                        "approver for this action",
                    )
                )
                return decision(
                    Outcome.BLOCK,
                    "no eligible approver role is configured for this high-risk "
                    "action, so nobody could lawfully approve it",
                )
            return decision(
                Outcome.APPROVAL_REQUIRED,
                f"{action.action_id} is a {action.action_class.value}: approval by "
                f"{', '.join(r.value for r in approvers)} is required, and the "
                f"approval expires after {ttl}s",
            )

        # 4. Read-only. Allowed, and marked as never mutating.
        if risk_class is RiskClass.READ_ONLY:
            hits.append(
                RuleHit(
                    "FLOOR-READ-ONLY-NO-MUTATE",
                    RuleSource.FLOOR,
                    Outcome.ALLOW,
                    "classified read-only: the gateway refuses to run it as a "
                    "mutation",
                )
            )
            if rule is not None and rule.require_approval:
                hits.append(
                    RuleHit(
                        "APP-REQUIRE-APPROVAL",
                        RuleSource.APP_POLICY,
                        Outcome.APPROVAL_REQUIRED,
                        rule.note or "policy asks for approval on this action",
                    )
                )
                return decision(
                    Outcome.APPROVAL_REQUIRED,
                    "this application's policy asks for approval even on a "
                    "read-only action",
                )
            return decision(Outcome.ALLOW, "read-only diagnostic; nothing mutates")

        # 5. Medium risk always needs a person; policy can only confirm it.
        if risk_class is RiskClass.MEDIUM_RISK:
            hits.append(
                RuleHit(
                    "FLOOR-MEDIUM-RISK-APPROVAL",
                    RuleSource.FLOOR,
                    Outcome.APPROVAL_REQUIRED,
                    "medium-risk actions are not auto-executable",
                )
            )
            return decision(
                Outcome.APPROVAL_REQUIRED,
                f"{action.action_id} is medium risk; a person must approve it",
            )

        # 6. Low risk. The one class where per-app policy decides.
        if policy is not None and action.action_class in policy.require_approval_classes:
            hits.append(
                RuleHit(
                    "APP-REQUIRE-APPROVAL-CLASS",
                    RuleSource.APP_POLICY,
                    Outcome.APPROVAL_REQUIRED,
                    f"policy requires approval for {action.action_class.value}",
                )
            )
            return decision(
                Outcome.APPROVAL_REQUIRED,
                f"this application's policy requires approval for every "
                f"{action.action_class.value}",
            )
        if rule is not None and rule.require_approval:
            hits.append(
                RuleHit(
                    "APP-REQUIRE-APPROVAL",
                    RuleSource.APP_POLICY,
                    Outcome.APPROVAL_REQUIRED,
                    rule.note or "policy asks for approval on this action",
                )
            )
            return decision(
                Outcome.APPROVAL_REQUIRED,
                "this application's policy requires approval for this action",
            )
        if action.execution_mode is ExecutionMode.APPROVAL:
            hits.append(
                RuleHit(
                    "MANIFEST-APPROVAL-MODE",
                    RuleSource.MANIFEST,
                    Outcome.APPROVAL_REQUIRED,
                    "the action was mapped in approval mode at onboarding",
                )
            )
            return decision(
                Outcome.APPROVAL_REQUIRED,
                "this action was mapped as approval-only at onboarding",
            )
        if rule is not None and rule.auto_execute:
            hits.append(
                RuleHit(
                    "APP-AUTO-EXECUTE-LOW-RISK",
                    RuleSource.APP_POLICY,
                    Outcome.ALLOW,
                    "policy permits this low-risk action to run without approval",
                )
            )
            return decision(
                Outcome.ALLOW, "low risk, and this application's policy allows it"
            )
        hits.append(
            RuleHit(
                "MANIFEST-MANUAL",
                RuleSource.MANIFEST,
                Outcome.ALLOW,
                "low-risk action run by a person on request",
            )
        )
        return decision(Outcome.ALLOW, "low risk, run on a person's request")


def _approvers_for(
    action: MappedAction,
    policy: Optional[PolicyDocument],
    rule: Optional[ActionRule],
) -> tuple[Role, ...]:
    """Who may approve, narrowed by whoever spoke last, then floored.

    Narrowing only: an action rule narrows the document's list, which narrows
    the manifest's, and every candidate is finally intersected with the roles
    that actually hold `approve_high_risk`. There is no configuration that adds
    an approver the frozen matrix does not empower.
    """
    candidates: Sequence[Role]
    if rule is not None and rule.approver_roles:
        candidates = rule.approver_roles
    elif policy is not None and policy.approver_roles:
        candidates = policy.approver_roles
    elif action.approver_roles:
        candidates = action.approver_roles
    else:
        candidates = tuple(sorted(APPROVE_HIGH_RISK_ROLES, key=lambda r: r.value))
    return tuple(
        dict.fromkeys(role for role in candidates if role in APPROVE_HIGH_RISK_ROLES)
    )


def args_hash(args: Mapping[str, Any]) -> str:
    """A stable hash over an action's arguments. Used by fingerprints and keys."""
    payload = json.dumps(
        _normalise(args), sort_keys=True, separators=(",", ":"), default=str
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


def _normalise(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(k): _normalise(v) for k, v in sorted(value.items())}
    if isinstance(value, (list, tuple)):
        return [_normalise(item) for item in value]
    return value
