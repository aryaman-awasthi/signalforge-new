"""The onboarding profile: what the wizard learns about an application.

One value object per wizard step beyond Basics, and one aggregate that holds
them. Everything is frozen, everything validates itself in `__post_init__`, and
the aggregate's `with_*` methods return a new profile - so a half-valid mapping
cannot exist even briefly, and an invalid step never reaches storage.

Three of the rules here are permanent floors (CLAUDE.md rule 4), enforced in the
domain so that every future caller - the wizard, a seed, a drill, an agent -
passes through the same code rather than each re-deriving them:

1. **A high-risk action class can never auto-execute.** Not by configuration, not
   by an operator's risk override, not by an import from an application's own
   manifest. `ExecutionMode.AUTO` on one of the six high-risk classes raises.
2. **A risk override may raise risk, never lower it.** An application publishing
   `risk: low` on a rollback does not get to make the platform treat it as low,
   and an operator lowering a high-risk class to `low` is refused.
3. **An approval TTL is capped** and a high-risk action must name at least one
   approver role that actually holds `approve_high_risk` in the frozen roles
   matrix - naming Executive as the approver for a rollback is a separation-of-
   duties violation, and it is refused at the point somebody types it.
"""

from __future__ import annotations

import re
import statistics
from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import Enum
from typing import Any, Iterable, Optional, Sequence

from signalforge.contracts.canonical import (
    HIGH_RISK_ACTION_CLASSES,
    ActionClass,
    ActionDefinition,
    ProbeSpec,
    RiskLevel,
)
from signalforge.contracts.errors import AdapterError
from signalforge.contracts.roles import ROLE_PERMISSIONS, Permission, Role
from signalforge.domain.integrations.promtext import parse_selector
from signalforge.domain.onboarding.errors import (
    OnboardingValidationError,
    PolicyFloorError,
)

SLUG_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{0,62}[a-z0-9]$")
LABEL_NAME_PATTERN = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]{0,62}$")
DISPLAY_PATTERN = re.compile(r"^[\w][\w .:/&'()%-]{0,119}$")

MAX_SIGNALS = 40
MAX_JOURNEYS = 30
MAX_PROBES = 60
MAX_ACTIONS = 100
MAX_SLOS = 30
MAX_REGIONS = 20
MAX_BASELINES = 40

APPROVAL_TTL_CAP_S = 3_600
"""The policy floor's approval expiry ceiling (CLAUDE.md rule 4). Configuration
may shorten it; nothing may lengthen it. An approval that outlives the situation
it was granted for is a standing permission nobody remembers giving."""

MIN_APPROVAL_TTL_S = 60
MIN_STABILITY_WINDOW_S = 60
MAX_STABILITY_WINDOW_S = 24 * 3_600
MAX_ACTION_RETRIES = 5

MIN_BASELINE_SAMPLES = 3
"""Two points are a line, not a baseline. The detection rules in S4 need a
spread, and a "baseline" learned from one scrape would give one."""


class WizardStep(str, Enum):
    """The seven steps, in the order the wizard walks them."""

    BASICS = "basics"
    CONNECT = "connect"
    MAP = "map"
    TOPOLOGY = "topology"
    SLOS = "slos"
    BASELINE = "baseline"
    TEST = "test"


WIZARD_STEP_ORDER: tuple[WizardStep, ...] = tuple(WizardStep)


class GoldenSignal(str, Enum):
    """The six families detection reasons over. Business is a first-class family
    rather than a metric tag: a payments counter falling is the signal that
    matters most and the one no infrastructure dashboard carries."""

    TRAFFIC = "traffic"
    ERRORS = "errors"
    LATENCY = "latency"
    SATURATION = "saturation"
    AVAILABILITY = "availability"
    BUSINESS = "business"


CUSTOMER_FACING_SIGNALS = frozenset(
    {GoldenSignal.AVAILABILITY, GoldenSignal.BUSINESS, GoldenSignal.ERRORS}
)
"""Corroboration (S4) needs two independent families with one customer-facing.
Named here so the wizard can tell an operator their mapping cannot corroborate
anything before an incident proves it."""


class SignalDirection(str, Enum):
    """Which way is bad. Stored rather than inferred from the metric name,
    because `errors_total` going up and `success_ratio` going down are the same
    event and nothing in the string says so."""

    HIGHER_IS_WORSE = "higher_is_worse"
    LOWER_IS_WORSE = "lower_is_worse"


def _slug(value: str, what: str) -> str:
    text = (value or "").strip().lower()
    if not SLUG_PATTERN.match(text):
        raise OnboardingValidationError(
            f"{what} must be lowercase kebab/dot-case, 2-64 characters, got {value!r}"
        )
    return text


def _display(value: str, what: str) -> str:
    text = (value or "").strip()
    if not DISPLAY_PATTERN.match(text):
        raise OnboardingValidationError(
            f"{what} must be 1-120 printable characters, got {value!r}"
        )
    return text


def _unique(items: Iterable[str], what: str) -> None:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for item in items:
        if item in seen:
            duplicates.add(item)
        seen.add(item)
    if duplicates:
        raise OnboardingValidationError(f"duplicate {what}: {sorted(duplicates)}")


# --- map step ----------------------------------------------------------------


@dataclass(frozen=True)
class SignalMapping:
    """One golden signal wired to one metric selector.

    The selector is parsed with the same code the metrics adapter uses
    (`domain/integrations/promtext`), so a mapping that could never match is
    refused while an operator is looking at the field rather than at 3am.
    """

    key: str
    signal: GoldenSignal
    selector: str
    direction: SignalDirection
    unit: str = ""
    description: str = ""
    business_counter: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "key", _slug(self.key, "signal key"))
        selector = (self.selector or "").strip()
        if not selector:
            raise OnboardingValidationError(
                f"signal {self.key!r} needs a metric selector; an empty selector "
                "matches every series and measures nothing in particular"
            )
        try:
            parse_selector(selector)
        except AdapterError as exc:
            raise OnboardingValidationError(
                f"signal {self.key!r}: {exc}"
            ) from exc
        object.__setattr__(self, "selector", selector)
        if len(self.unit or "") > 20:
            raise OnboardingValidationError("unit must be at most 20 characters")
        if self.business_counter and self.signal is not GoldenSignal.BUSINESS:
            raise OnboardingValidationError(
                "business_counter belongs to the business signal family"
            )

    @property
    def customer_facing(self) -> bool:
        return self.signal in CUSTOMER_FACING_SIGNALS


@dataclass(frozen=True)
class ProbeTemplate:
    """A synthetic check SignalForge runs itself, per journey and per region.

    It carries its own synthetic header so the application can exclude probe
    traffic from the business counters it reports - otherwise the platform's own
    health checks inflate the very KPI it is watching.
    """

    probe_id: str
    path: str
    method: str = "GET"
    expected_status: int = 200
    timeout_s: float = 5.0
    journey_id: Optional[str] = None
    per_region: bool = True
    synthetic_header: str = "X-SignalForge-Synthetic"

    def __post_init__(self) -> None:
        object.__setattr__(self, "probe_id", _slug(self.probe_id, "probe id"))
        path = (self.path or "").strip()
        if not path or len(path) > 300:
            raise OnboardingValidationError(
                f"probe {self.probe_id!r} needs a path of 1-300 characters"
            )
        if "://" in path and not path.startswith(("http://", "https://")):
            raise OnboardingValidationError(
                f"probe {self.probe_id!r} target must be a path or an http(s) URL"
            )
        object.__setattr__(self, "path", path)
        method = (self.method or "GET").strip().upper()
        if method not in ("GET", "HEAD", "POST", "OPTIONS"):
            raise OnboardingValidationError(
                f"probe {self.probe_id!r}: a synthetic probe uses a safe method "
                "(GET, HEAD, OPTIONS) or POST, not "
                f"{self.method!r}"
            )
        object.__setattr__(self, "method", method)
        if not (100 <= int(self.expected_status) <= 599):
            raise OnboardingValidationError(
                f"probe {self.probe_id!r}: expected_status must be a status code"
            )
        if not (0 < float(self.timeout_s) <= 30):
            raise OnboardingValidationError(
                f"probe {self.probe_id!r}: timeout_s must be greater than 0 and at most 30"
            )
        if self.journey_id is not None:
            object.__setattr__(self, "journey_id", _slug(self.journey_id, "journey id"))
        if not LABEL_NAME_PATTERN.match(self.synthetic_header.replace("-", "_")):
            raise OnboardingValidationError("synthetic_header must be a header name")

    def to_probe_spec(
        self, base_url: Optional[str], region: Optional[str] = None
    ) -> ProbeSpec:
        """The canonical ProbeSpec the health adapter runs.

        A relative path is resolved against the application's base URL; an
        absolute target is used as it stands, which is how a probe can exercise
        a public entry point that is not under the observe root.
        """
        if self.path.startswith(("http://", "https://")):
            target = self.path
        elif base_url:
            target = f"{base_url.rstrip('/')}/{self.path.lstrip('/')}"
        else:
            raise OnboardingValidationError(
                f"probe {self.probe_id!r} is a relative path and the application "
                "has no base_url to resolve it against"
            )
        probe_id = f"{self.probe_id}:{region}" if region else self.probe_id
        return ProbeSpec(
            probe_id=probe_id,
            target=target,
            method=self.method,
            timeout_s=self.timeout_s,
            expected_status=self.expected_status,
            journey=self.journey_id,
        )


@dataclass(frozen=True)
class Journey:
    """A user journey: the unit customers actually experience, and the unit the
    severity matrix reasons about."""

    journey_id: str
    name: str
    tier: int = 2
    description: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "journey_id", _slug(self.journey_id, "journey id"))
        object.__setattr__(self, "name", _display(self.name, "journey name"))
        if int(self.tier) not in (1, 2, 3):
            raise OnboardingValidationError(
                f"journey {self.journey_id!r}: tier must be 1, 2 or 3"
            )
        object.__setattr__(self, "tier", int(self.tier))


class ExecutionMode(str, Enum):
    """How far an action may go without a human.

    MANUAL is a human doing it. APPROVAL is the platform doing it after a named
    human approves. AUTO is the platform doing it on its own - and AUTO is
    permanently unavailable to the six high-risk classes.
    """

    MANUAL = "manual"
    APPROVAL = "approval"
    AUTO = "auto"


APPROVE_HIGH_RISK_ROLES: frozenset[Role] = frozenset(
    role
    for role, permissions in ROLE_PERMISSIONS.items()
    if Permission.APPROVE_HIGH_RISK in permissions
)
"""Derived from the frozen matrix rather than restated, so a matrix change and
this rule cannot drift apart."""

_RISK_RANK = {RiskLevel.LOW: 1, RiskLevel.MEDIUM: 2, RiskLevel.HIGH: 3}


@dataclass(frozen=True)
class MappedAction:
    """An action imported from the application's own manifest, plus what the
    operator decided about it.

    The manifest's declaration is kept beside the override, permanently: "the
    app said this was low risk and we raised it" is a fact an audit will want,
    and overwriting the original would lose it.
    """

    action_id: str
    name: str
    action_class: ActionClass
    manifest_risk: RiskLevel
    risk_override: Optional[RiskLevel] = None
    approver_roles: tuple[Role, ...] = ()
    execution_mode: ExecutionMode = ExecutionMode.APPROVAL
    reversible: bool = False
    reverse_action_id: Optional[str] = None
    idempotent: bool = False
    enabled: bool = True
    description: str = ""
    imported_at: Optional[datetime] = None

    def __post_init__(self) -> None:
        if not (self.action_id or "").strip():
            raise OnboardingValidationError("an action needs an action_id")
        object.__setattr__(self, "action_id", self.action_id.strip()[:100])
        object.__setattr__(self, "name", _display(self.name or self.action_id, "action name"))
        _unique([role.value for role in self.approver_roles], "approver roles")
        self._assert_floor()

    # -- the floor ---------------------------------------------------------

    @property
    def high_risk(self) -> bool:
        """High-risk by class, not by anybody's opinion of it. The six classes
        are frozen in the contracts and the platform's answer to them is not
        configurable."""
        return self.action_class in HIGH_RISK_ACTION_CLASSES

    @property
    def effective_risk(self) -> RiskLevel:
        """The risk the platform acts on: the higher of what the manifest
        declared and what the operator set, and never below HIGH for a high-risk
        class."""
        if self.high_risk:
            return RiskLevel.HIGH
        candidates = [self.manifest_risk]
        if self.risk_override is not None:
            candidates.append(self.risk_override)
        return max(candidates, key=lambda level: _RISK_RANK[level])

    @property
    def requires_approval(self) -> bool:
        return self.execution_mode is not ExecutionMode.MANUAL and (
            self.high_risk
            or self.effective_risk is not RiskLevel.LOW
            or self.execution_mode is ExecutionMode.APPROVAL
        )

    def _assert_floor(self) -> None:
        if self.high_risk and self.execution_mode is ExecutionMode.AUTO:
            raise PolicyFloorError(
                f"action {self.action_id!r} is class {self.action_class.value!r}, "
                "which is high-risk: it can never be set to auto-execute"
            )
        if (
            self.risk_override is not None
            and self.high_risk
            and self.risk_override is not RiskLevel.HIGH
        ):
            raise PolicyFloorError(
                f"action {self.action_id!r} is class {self.action_class.value!r}; "
                f"its risk cannot be overridden down to {self.risk_override.value!r}"
            )
        if (
            self.risk_override is not None
            and _RISK_RANK[self.risk_override] < _RISK_RANK[self.manifest_risk]
        ):
            raise PolicyFloorError(
                f"action {self.action_id!r}: the application declared risk "
                f"{self.manifest_risk.value!r}; an override may raise it, never lower it"
            )
        if self.high_risk:
            if not self.approver_roles:
                raise PolicyFloorError(
                    f"action {self.action_id!r} is high-risk and must name at least "
                    "one approver role"
                )
            powerless = sorted(
                role.value
                for role in self.approver_roles
                if role not in APPROVE_HIGH_RISK_ROLES
            )
            if powerless:
                raise PolicyFloorError(
                    f"action {self.action_id!r}: {powerless} do not hold "
                    "approve_high_risk in the roles matrix, so they cannot approve "
                    f"a {self.action_class.value} - eligible roles are "
                    f"{sorted(r.value for r in APPROVE_HIGH_RISK_ROLES)}"
                )
        if self.reversible and not self.reverse_action_id:
            raise OnboardingValidationError(
                f"action {self.action_id!r} claims to be reversible but names no "
                "reverse action"
            )

    # -- transitions -------------------------------------------------------

    def overridden(
        self,
        *,
        risk_override: Optional[RiskLevel] = None,
        approver_roles: Optional[Sequence[Role]] = None,
        execution_mode: Optional[ExecutionMode] = None,
        enabled: Optional[bool] = None,
        clear_risk_override: bool = False,
    ) -> "MappedAction":
        return replace(
            self,
            risk_override=(
                None
                if clear_risk_override
                else (self.risk_override if risk_override is None else risk_override)
            ),
            approver_roles=(
                self.approver_roles
                if approver_roles is None
                else tuple(approver_roles)
            ),
            execution_mode=(
                self.execution_mode if execution_mode is None else execution_mode
            ),
            enabled=self.enabled if enabled is None else enabled,
        )


def action_from_definition(
    definition: ActionDefinition, *, imported_at: datetime
) -> MappedAction:
    """Import one action from the application's validated action manifest.

    The manifest's approver roles are honoured where they name real roles and
    dropped where they do not - an application declaring `["platform-team"]` is
    describing its own org chart, not this platform's roles matrix. A high-risk
    action left with no usable approver falls back to the roles that hold
    approve_high_risk, because refusing the whole import would make one sloppy
    manifest entry block onboarding entirely.
    """
    roles: list[Role] = []
    for raw in definition.approver_roles:
        try:
            role = Role(str(raw).strip().lower())
        except ValueError:
            continue
        if role not in roles:
            roles.append(role)
    high_risk = definition.action_class in HIGH_RISK_ACTION_CLASSES
    if high_risk:
        roles = [role for role in roles if role in APPROVE_HIGH_RISK_ROLES]
        if not roles:
            roles = sorted(APPROVE_HIGH_RISK_ROLES, key=lambda role: role.value)
    return MappedAction(
        action_id=definition.action_id,
        name=definition.name,
        action_class=definition.action_class,
        manifest_risk=definition.risk,
        approver_roles=tuple(roles),
        # Imported actions never arrive executable. AUTO is refused outright for
        # the high-risk classes and is not the default for anything else either.
        execution_mode=(
            ExecutionMode.APPROVAL if high_risk else ExecutionMode.MANUAL
        ),
        reversible=definition.reversible,
        reverse_action_id=definition.reverse_action_id,
        idempotent=definition.idempotent,
        description=definition.description[:400],
        imported_at=imported_at,
    )


# --- SLO, severity and policy step -------------------------------------------


@dataclass(frozen=True)
class SloTarget:
    slo_id: str
    name: str
    objective_pct: float
    window_days: int = 30
    journey_id: Optional[str] = None
    indicator: str = ""
    fast_burn_multiplier: float = 14.4
    slow_burn_multiplier: float = 6.0

    def __post_init__(self) -> None:
        object.__setattr__(self, "slo_id", _slug(self.slo_id, "slo id"))
        object.__setattr__(self, "name", _display(self.name, "slo name"))
        if not (0 < float(self.objective_pct) < 100):
            raise OnboardingValidationError(
                f"slo {self.slo_id!r}: objective must be between 0 and 100 percent "
                "exclusive; a 100 percent objective has no error budget to burn"
            )
        if not (1 <= int(self.window_days) <= 90):
            raise OnboardingValidationError(
                f"slo {self.slo_id!r}: window_days must be between 1 and 90"
            )
        object.__setattr__(self, "window_days", int(self.window_days))
        if self.journey_id is not None:
            object.__setattr__(self, "journey_id", _slug(self.journey_id, "journey id"))
        if self.fast_burn_multiplier <= self.slow_burn_multiplier:
            raise OnboardingValidationError(
                f"slo {self.slo_id!r}: the fast burn rate must exceed the slow one "
                "or a fast burn could never be distinguished from a slow one"
            )
        if self.slow_burn_multiplier <= 1:
            raise OnboardingValidationError(
                f"slo {self.slo_id!r}: a burn multiplier of 1 or less is the budget "
                "being spent exactly as planned, which is not a burn"
            )

    @property
    def error_budget_pct(self) -> float:
        return round(100.0 - float(self.objective_pct), 6)


class Severity(str, Enum):
    P0 = "P0"
    P1 = "P1"
    P2 = "P2"
    P3 = "P3"

    @property
    def rank(self) -> int:
        return {"P0": 0, "P1": 1, "P2": 2, "P3": 3}[self.value]


@dataclass(frozen=True)
class SeverityRule:
    """One row of the severity matrix.

    Deliberately declarative and deterministic: severity is proposed by
    arithmetic over observed impact and confirmed by a human (REQ-043). A rule
    matches when every threshold it states is met; thresholds it leaves unset do
    not constrain it.
    """

    severity: Severity
    max_journey_tier: Optional[int] = None
    min_regions_affected: Optional[int] = None
    success_rate_below_pct: Optional[float] = None
    customer_impacting: Optional[bool] = None
    description: str = ""

    def __post_init__(self) -> None:
        if self.max_journey_tier is not None and int(self.max_journey_tier) not in (1, 2, 3):
            raise OnboardingValidationError(
                "severity rule: max_journey_tier must be 1, 2 or 3"
            )
        if self.min_regions_affected is not None and int(self.min_regions_affected) < 1:
            raise OnboardingValidationError(
                "severity rule: min_regions_affected must be at least 1"
            )
        if self.success_rate_below_pct is not None and not (
            0 <= float(self.success_rate_below_pct) <= 100
        ):
            raise OnboardingValidationError(
                "severity rule: success_rate_below_pct must be a percentage"
            )

    def matches(
        self,
        *,
        journey_tier: Optional[int],
        regions_affected: int,
        success_rate_pct: Optional[float],
        customer_impacting: bool,
    ) -> bool:
        if self.max_journey_tier is not None:
            if journey_tier is None or journey_tier > self.max_journey_tier:
                return False
        if self.min_regions_affected is not None:
            if regions_affected < self.min_regions_affected:
                return False
        if self.success_rate_below_pct is not None:
            if success_rate_pct is None or success_rate_pct >= self.success_rate_below_pct:
                return False
        if self.customer_impacting is not None:
            if bool(customer_impacting) is not bool(self.customer_impacting):
                return False
        return True


@dataclass(frozen=True)
class SeverityMatrix:
    rules: tuple[SeverityRule, ...] = ()
    default: Severity = Severity.P3

    def __post_init__(self) -> None:
        if not self.rules:
            return
        _unique([rule.severity.value for rule in self.rules], "severity rules")
        if Severity.P0 not in {rule.severity for rule in self.rules}:
            raise OnboardingValidationError(
                "a severity matrix must define P0; an application whose worst case "
                "is not expressible cannot be escalated correctly"
            )
        object.__setattr__(
            self,
            "rules",
            tuple(sorted(self.rules, key=lambda rule: rule.severity.rank)),
        )

    @property
    def configured(self) -> bool:
        return bool(self.rules)

    def classify(
        self,
        *,
        journey_tier: Optional[int] = None,
        regions_affected: int = 0,
        success_rate_pct: Optional[float] = None,
        customer_impacting: bool = False,
    ) -> Severity:
        """Most severe matching rule wins; nothing matching is the default.

        Pure and total, so the same impact always proposes the same severity and
        a proposal can be explained by naming the rule that matched.
        """
        for rule in self.rules:
            if rule.matches(
                journey_tier=journey_tier,
                regions_affected=regions_affected,
                success_rate_pct=success_rate_pct,
                customer_impacting=customer_impacting,
            ):
                return rule.severity
        return self.default


def default_severity_matrix() -> SeverityMatrix:
    """What the wizard offers before anyone edits it, from the detection design:
    a tier-1 journey failing badly everywhere is a P0, a journey below target in
    one region is a P1, and no customer impact is a P2."""
    return SeverityMatrix(
        rules=(
            SeverityRule(
                severity=Severity.P0,
                max_journey_tier=1,
                min_regions_affected=2,
                success_rate_below_pct=80.0,
                customer_impacting=True,
                description="tier-1 journey below 80% in every region",
            ),
            SeverityRule(
                severity=Severity.P1,
                max_journey_tier=2,
                min_regions_affected=1,
                success_rate_below_pct=95.0,
                customer_impacting=True,
                description="a journey below target in at least one region",
            ),
            SeverityRule(
                severity=Severity.P2,
                customer_impacting=False,
                description="degraded with no customer impact",
            ),
        )
    )


@dataclass(frozen=True)
class PolicyDefaults:
    """The per-app policy the wizard seeds, sitting above the permanent floor.

    Everything here is editable in S6's policy editor. What is *not* editable is
    checked in `__post_init__`: high-risk classes always require approval, the
    approval TTL is capped, and audit is not a field at all - it cannot be
    disabled, so it cannot be configured.
    """

    approval_ttl_s: int = 900
    stability_window_s: int = 900
    max_action_retries: int = 1
    auto_execute_low_risk: bool = False
    require_approval_classes: frozenset[ActionClass] = field(
        default_factory=lambda: frozenset(HIGH_RISK_ACTION_CLASSES)
    )
    confidence_threshold: float = 0.7
    revalidation_cycles_before_escalation: int = 3

    def __post_init__(self) -> None:
        if not (MIN_APPROVAL_TTL_S <= int(self.approval_ttl_s) <= APPROVAL_TTL_CAP_S):
            raise PolicyFloorError(
                f"approval_ttl_s must be between {MIN_APPROVAL_TTL_S} and "
                f"{APPROVAL_TTL_CAP_S} seconds; an approval that outlives the "
                "situation it was granted for is a standing permission"
            )
        if not (
            MIN_STABILITY_WINDOW_S <= int(self.stability_window_s) <= MAX_STABILITY_WINDOW_S
        ):
            raise OnboardingValidationError(
                f"stability_window_s must be between {MIN_STABILITY_WINDOW_S} and "
                f"{MAX_STABILITY_WINDOW_S} seconds"
            )
        if not (0 <= int(self.max_action_retries) <= MAX_ACTION_RETRIES):
            raise OnboardingValidationError(
                f"max_action_retries must be between 0 and {MAX_ACTION_RETRIES}"
            )
        if not (0.0 < float(self.confidence_threshold) <= 1.0):
            raise OnboardingValidationError(
                "confidence_threshold must be greater than 0 and at most 1"
            )
        if not (1 <= int(self.revalidation_cycles_before_escalation) <= 10):
            raise OnboardingValidationError(
                "revalidation_cycles_before_escalation must be between 1 and 10"
            )
        missing = sorted(
            action_class.value
            for action_class in HIGH_RISK_ACTION_CLASSES
            if action_class not in self.require_approval_classes
        )
        if missing:
            raise PolicyFloorError(
                f"the high-risk classes {missing} always require approval; they "
                "cannot be removed from require_approval_classes"
            )


# --- baseline step -----------------------------------------------------------


@dataclass(frozen=True)
class Baseline:
    """What normal looked like for one mapped signal.

    `samples` and `window_s` are kept beside the statistics because a baseline
    is only as trustworthy as the window it was learned over, and a detection
    rule that fires against a baseline of three samples taken in five seconds
    should be able to say so.
    """

    signal_key: str
    samples: int
    window_s: float
    mean: float
    stddev: float
    minimum: float
    maximum: float
    p95: float
    series_count: int = 0
    learned_at: Optional[datetime] = None
    source: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "signal_key", _slug(self.signal_key, "signal key"))
        if int(self.samples) < MIN_BASELINE_SAMPLES:
            raise OnboardingValidationError(
                f"a baseline needs at least {MIN_BASELINE_SAMPLES} samples, got "
                f"{self.samples}"
            )
        if float(self.window_s) < 0:
            raise OnboardingValidationError("window_s cannot be negative")


def summarize(values: Sequence[float]) -> dict[str, float]:
    """Mean, standard deviation, min, max and p95 of a sample.

    Pure, and deliberately using the nearest-rank p95 rather than interpolating:
    with a handful of samples an interpolated percentile invents a value that was
    never observed, and a baseline should only ever contain measurements.
    """
    if not values:
        raise OnboardingValidationError("cannot summarise an empty sample")
    ordered = sorted(float(value) for value in values)
    index = max(0, min(len(ordered) - 1, int(round(0.95 * len(ordered) + 0.5)) - 1))
    return {
        "mean": statistics.fmean(ordered),
        "stddev": statistics.pstdev(ordered) if len(ordered) > 1 else 0.0,
        "minimum": ordered[0],
        "maximum": ordered[-1],
        "p95": ordered[index],
    }


# --- topology and test steps -------------------------------------------------


@dataclass(frozen=True)
class TopologyRef:
    """What the profile remembers about the map. The graph itself is a versioned
    row of its own; this is the pointer plus the size, so a wizard step can say
    "47 nodes, version sha256:… " without loading it."""

    version: str
    captured_at: datetime
    node_count: int
    edge_count: int
    imported_at: Optional[datetime] = None
    normalized_by: str = ""
    source: str = "upload"


class CheckOutcome(str, Enum):
    """Deliberately four values, not two.

    PENDING is what an inbound push integration reads before its sender has ever
    posted: it is not a failure, and colouring it red would train operators to
    ignore red. SKIPPED is what an uninstallable adapter reads.
    """

    PASSED = "passed"
    FAILED = "failed"
    PENDING = "pending"
    SKIPPED = "skipped"


@dataclass(frozen=True)
class TestSummary:
    """The last run of the wizard's test page."""

    ran_at: datetime
    passed: int = 0
    failed: int = 0
    pending: int = 0
    skipped: int = 0

    @property
    def total(self) -> int:
        return self.passed + self.failed + self.pending + self.skipped

    @property
    def all_green(self) -> bool:
        return self.total > 0 and self.failed == 0 and self.pending == 0


# --- the aggregate -----------------------------------------------------------


@dataclass(frozen=True)
class OnboardingProfile:
    tenant_id: str
    app_id: str
    signals: tuple[SignalMapping, ...] = ()
    region_label: Optional[str] = None
    regions: tuple[str, ...] = ()
    journeys: tuple[Journey, ...] = ()
    probes: tuple[ProbeTemplate, ...] = ()
    actions: tuple[MappedAction, ...] = ()
    slos: tuple[SloTarget, ...] = ()
    severity_matrix: SeverityMatrix = field(default_factory=SeverityMatrix)
    policy: PolicyDefaults = field(default_factory=PolicyDefaults)
    baselines: tuple[Baseline, ...] = ()
    topology: Optional[TopologyRef] = None
    last_test: Optional[TestSummary] = None
    updated_at: Optional[datetime] = None

    def __post_init__(self) -> None:
        self._bounded("signals", self.signals, MAX_SIGNALS)
        self._bounded("journeys", self.journeys, MAX_JOURNEYS)
        self._bounded("probes", self.probes, MAX_PROBES)
        self._bounded("actions", self.actions, MAX_ACTIONS)
        self._bounded("slos", self.slos, MAX_SLOS)
        self._bounded("regions", self.regions, MAX_REGIONS)
        self._bounded("baselines", self.baselines, MAX_BASELINES)
        _unique([signal.key for signal in self.signals], "signal keys")
        _unique([journey.journey_id for journey in self.journeys], "journey ids")
        _unique([probe.probe_id for probe in self.probes], "probe ids")
        _unique([action.action_id for action in self.actions], "action ids")
        _unique([slo.slo_id for slo in self.slos], "slo ids")
        _unique([baseline.signal_key for baseline in self.baselines], "baselines")
        if self.region_label is not None:
            label = self.region_label.strip()
            if not LABEL_NAME_PATTERN.match(label):
                raise OnboardingValidationError(
                    "region_label must be a metric label name, e.g. 'region'"
                )
            object.__setattr__(self, "region_label", label)
        object.__setattr__(
            self, "regions", tuple(dict.fromkeys(r.strip() for r in self.regions if r.strip()))
        )
        journey_ids = {journey.journey_id for journey in self.journeys}
        orphan_probes = sorted(
            f"{probe.probe_id} -> {probe.journey_id}"
            for probe in self.probes
            if probe.journey_id is not None and probe.journey_id not in journey_ids
        )
        if orphan_probes:
            raise OnboardingValidationError(
                f"probes {orphan_probes} name journeys that are not declared; a "
                "probe measuring a journey nobody defined cannot be scored"
            )
        orphan_slos = sorted(
            f"{slo.slo_id} -> {slo.journey_id}"
            for slo in self.slos
            if slo.journey_id is not None and slo.journey_id not in journey_ids
        )
        if orphan_slos:
            raise OnboardingValidationError(
                f"SLOs {orphan_slos} name journeys that are not declared"
            )
        signal_keys = {signal.key for signal in self.signals}
        orphan_baselines = sorted(
            baseline.signal_key
            for baseline in self.baselines
            if baseline.signal_key not in signal_keys
        )
        if orphan_baselines:
            raise OnboardingValidationError(
                f"baselines {orphan_baselines} were learned for signals that are no "
                "longer mapped"
            )

    @staticmethod
    def _bounded(what: str, items: Sequence[Any], ceiling: int) -> None:
        if len(items) > ceiling:
            raise OnboardingValidationError(
                f"an onboarding profile holds at most {ceiling} {what}, got {len(items)}"
            )

    # -- derived -----------------------------------------------------------

    @property
    def business_signals(self) -> tuple[SignalMapping, ...]:
        return tuple(
            signal for signal in self.signals if signal.signal is GoldenSignal.BUSINESS
        )

    @property
    def customer_facing_signals(self) -> tuple[SignalMapping, ...]:
        return tuple(signal for signal in self.signals if signal.customer_facing)

    @property
    def signal_families(self) -> frozenset[GoldenSignal]:
        return frozenset(signal.signal for signal in self.signals)

    @property
    def can_corroborate(self) -> bool:
        """Two independent families with one customer-facing (REQ-041). The
        wizard shows this so an operator learns their mapping cannot corroborate
        anything now rather than during the first incident."""
        return len(self.signal_families) >= 2 and bool(self.customer_facing_signals)

    @property
    def high_risk_actions(self) -> tuple[MappedAction, ...]:
        return tuple(action for action in self.actions if action.high_risk)

    def signal(self, key: str) -> Optional[SignalMapping]:
        return next((s for s in self.signals if s.key == key), None)

    def action(self, action_id: str) -> Optional[MappedAction]:
        return next((a for a in self.actions if a.action_id == action_id), None)

    def baseline(self, signal_key: str) -> Optional[Baseline]:
        return next((b for b in self.baselines if b.signal_key == signal_key), None)

    def probe_specs(self, base_url: Optional[str]) -> tuple[ProbeSpec, ...]:
        """Every probe, fanned out across the declared regions when it asks to
        be. A probe that runs in one region proves one region."""
        specs: list[ProbeSpec] = []
        for probe in self.probes:
            regions = self.regions if (probe.per_region and self.regions) else (None,)
            for region in regions:
                specs.append(probe.to_probe_spec(base_url, region))
        return tuple(specs)

    # -- transitions -------------------------------------------------------

    def with_mapping(
        self,
        *,
        at: datetime,
        signals: Optional[Sequence[SignalMapping]] = None,
        region_label: Optional[str] = None,
        regions: Optional[Sequence[str]] = None,
        journeys: Optional[Sequence[Journey]] = None,
        probes: Optional[Sequence[ProbeTemplate]] = None,
        clear_region_label: bool = False,
    ) -> "OnboardingProfile":
        next_signals = self.signals if signals is None else tuple(signals)
        next_keys = {signal.key for signal in next_signals}
        return replace(
            self,
            signals=next_signals,
            region_label=(
                None
                if clear_region_label
                else (self.region_label if region_label is None else region_label)
            ),
            regions=self.regions if regions is None else tuple(regions),
            journeys=self.journeys if journeys is None else tuple(journeys),
            probes=self.probes if probes is None else tuple(probes),
            # A baseline belongs to the signal it was learned for. Re-mapping the
            # signals drops the baselines that no longer have one, rather than
            # keeping a number nothing can be compared against.
            baselines=tuple(b for b in self.baselines if b.signal_key in next_keys),
            updated_at=at,
        )

    def with_actions(
        self, actions: Sequence[MappedAction], *, at: datetime
    ) -> "OnboardingProfile":
        return replace(self, actions=tuple(actions), updated_at=at)

    def with_governance(
        self,
        *,
        at: datetime,
        slos: Optional[Sequence[SloTarget]] = None,
        severity_matrix: Optional[SeverityMatrix] = None,
        policy: Optional[PolicyDefaults] = None,
    ) -> "OnboardingProfile":
        return replace(
            self,
            slos=self.slos if slos is None else tuple(slos),
            severity_matrix=(
                self.severity_matrix if severity_matrix is None else severity_matrix
            ),
            policy=self.policy if policy is None else policy,
            updated_at=at,
        )

    def with_baselines(
        self, baselines: Sequence[Baseline], *, at: datetime
    ) -> "OnboardingProfile":
        """Learned baselines are merged, not replaced: a run that could only
        reach three of five signals must not erase the two it did not touch."""
        merged: dict[str, Baseline] = {b.signal_key: b for b in self.baselines}
        for baseline in baselines:
            merged[baseline.signal_key] = baseline
        return replace(
            self,
            baselines=tuple(merged[key] for key in sorted(merged)),
            updated_at=at,
        )

    def with_topology(self, topology: TopologyRef, *, at: datetime) -> "OnboardingProfile":
        return replace(self, topology=topology, updated_at=at)

    def with_test(self, summary: TestSummary, *, at: datetime) -> "OnboardingProfile":
        return replace(self, last_test=summary, updated_at=at)


def empty_profile(tenant_id: str, app_id: str) -> OnboardingProfile:
    """A brand-new application's profile: nothing mapped, and the default
    severity matrix and policy already in place. The defaults are real values
    rather than blanks because the floor has to hold from the first second, not
    from whenever somebody visits the SLO step."""
    return OnboardingProfile(
        tenant_id=tenant_id,
        app_id=app_id,
        severity_matrix=default_severity_matrix(),
        policy=PolicyDefaults(),
    )


def parse_role(raw: str) -> Role:
    try:
        return Role(str(raw).strip().lower())
    except ValueError as exc:
        raise OnboardingValidationError(
            f"{raw!r} is not a role; the roles matrix defines "
            f"{[role.value for role in Role]}"
        ) from exc
