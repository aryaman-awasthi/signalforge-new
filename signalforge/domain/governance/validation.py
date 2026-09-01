"""Layered validation and the closure gate (REQ-065).

Recovery is a claim about the world, so it is checked against the world. Every
layer here reads the *application* through its adapters - metrics, probes,
health, business counters - and none of them reads the Incident Brain. Asking
the reasoning engine whether the system is healthy would be asking the platform
to mark its own homework: the brain's beliefs were formed from the same
telemetry, and a hypothesis with high confidence that the pool is fixed is not
evidence that the pool is fixed.

## Four layers, four different questions

| Layer | The question | Typical source |
|---|---|---|
| `technical` | is the thing running and configured? | health endpoint, action verification |
| `service` | is it serving? | SLO success rate, error rate, latency |
| `customer` | are people getting what they came for? | journey probes, stuck transactions |
| `business` | did the money start moving again? | business-family counters |

They are separate because they fail separately, and the gap between them is the
most expensive thing in incident response: a service whose instances are healthy
and whose error rate is clean while customers still cannot pay is a service that
looks recovered on every dashboard an engineer opens.

## The gate

`customer` blocks closure. Technical and service passing while customer fails is
not "nearly recovered" - it is the case above, and the whole point of measuring
four layers is to refuse to close on three of them. `business` is advisory when
the application published no business counters, because a gate nobody can
satisfy is a gate people route around.

## The stability window

A layer is not passed by going green once. `StabilityWindow` requires the checks
to hold for a configured duration *and* a minimum number of consecutive samples,
and **any** regression resets it to zero rather than decrementing it. Intermittent
green is what a flapping service looks like, and counting it as recovery is how
an incident gets closed twice.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import Enum
from typing import Any, Mapping, Optional, Sequence

from signalforge.domain.governance.errors import (
    ClosureBlockedError,
    GovernanceValidationError,
)

MAX_DETAIL_CHARS = 400


class ValidationLayer(str, Enum):
    TECHNICAL = "technical"
    SERVICE = "service"
    CUSTOMER = "customer"
    BUSINESS = "business"

    @property
    def order(self) -> int:
        return {"technical": 0, "service": 1, "customer": 2, "business": 3}[self.value]

    @property
    def gates_closure(self) -> bool:
        """Which layers can refuse to let an incident close.

        Technical and service are *necessary* - an incident closed while the
        service is erroring is obviously wrong - and customer is the one that
        catches the expensive case. Business does not gate: many applications
        publish no business counters at all, and a gate that cannot be satisfied
        is a gate somebody will disable.
        """
        return self in (
            ValidationLayer.TECHNICAL,
            ValidationLayer.SERVICE,
            ValidationLayer.CUSTOMER,
        )


class LayerVerdict(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    UNKNOWN = "unknown"
    """No source could answer. Deliberately *not* a pass: an unmeasurable layer
    is not a healthy one, and a closure gate that treated silence as consent
    would close every incident on an application whose probes were down."""
    NOT_APPLICABLE = "not_applicable"
    """The application published nothing this layer could read - no business
    counters, no journey probes. Distinct from UNKNOWN, which means a source
    existed and did not answer."""

    @property
    def satisfied(self) -> bool:
        return self in (LayerVerdict.PASS, LayerVerdict.NOT_APPLICABLE)


@dataclass(frozen=True)
class LayerCheck:
    """One measurement inside a layer, with where it came from."""

    check_id: str
    label: str
    verdict: LayerVerdict
    source: str
    """The adapter or subsystem that answered - `observe-health`, `probe-runner`,
    `rollups`. Never `incident-brain`: see the module docstring."""
    observed: str = ""
    expected: str = ""
    result_ref: str = ""
    detail: str = ""

    def __post_init__(self) -> None:
        if not self.source:
            raise GovernanceValidationError(
                "a validation check must name the system it read; an unsourced "
                "check is an opinion"
            )
        if self.source.strip().lower() in _FORBIDDEN_SOURCES:
            raise GovernanceValidationError(
                f"validation may not read {self.source!r}: recovery is checked "
                "against the application, never against the platform's own "
                "reasoning about it (REQ-065)"
            )
        object.__setattr__(self, "detail", str(self.detail or "")[:MAX_DETAIL_CHARS])

    def as_dict(self) -> dict[str, Any]:
        return {
            "check_id": self.check_id,
            "label": self.label,
            "verdict": self.verdict.value,
            "source": self.source,
            "observed": self.observed,
            "expected": self.expected,
            "result_ref": self.result_ref,
            "detail": self.detail,
        }


_FORBIDDEN_SOURCES: frozenset[str] = frozenset(
    {"incident-brain", "brain", "graph", "incident_graph", "copilot", "governor"}
)
"""Enforced at construction rather than by review. The one rule of this file is
that validation is independent of the reasoning, and a rule enforced only in a
docstring is a rule that lasts until somebody is in a hurry."""


@dataclass(frozen=True)
class LayerResult:
    """One layer's verdict, and the checks it was computed from."""

    layer: ValidationLayer
    verdict: LayerVerdict
    checks: tuple[LayerCheck, ...] = ()
    summary: str = ""

    @property
    def failing(self) -> tuple[LayerCheck, ...]:
        return tuple(c for c in self.checks if c.verdict is LayerVerdict.FAIL)

    @property
    def satisfied(self) -> bool:
        return self.verdict.satisfied

    def as_dict(self) -> dict[str, Any]:
        return {
            "layer": self.layer.value,
            "verdict": self.verdict.value,
            "summary": self.summary,
            "checks": [check.as_dict() for check in self.checks],
            "failing": [check.check_id for check in self.failing],
        }


def verdict_of(checks: Sequence[LayerCheck]) -> LayerVerdict:
    """A layer is as good as its worst check.

    Any FAIL fails the layer; otherwise any UNKNOWN makes it unknown; a layer
    with only NOT_APPLICABLE checks (or none at all) is not applicable. There is
    no averaging and no quorum: "three of our four customer journeys work" is a
    failing customer layer, and rounding it to a pass is the failure this whole
    module exists to prevent.
    """
    if not checks:
        return LayerVerdict.NOT_APPLICABLE
    verdicts = {check.verdict for check in checks}
    if LayerVerdict.FAIL in verdicts:
        return LayerVerdict.FAIL
    if LayerVerdict.UNKNOWN in verdicts:
        return LayerVerdict.UNKNOWN
    if verdicts == {LayerVerdict.NOT_APPLICABLE}:
        return LayerVerdict.NOT_APPLICABLE
    return LayerVerdict.PASS


@dataclass(frozen=True)
class StabilityWindow:
    """How long everything has been good, and what reset it.

    `consecutive_passes` counts samples in which every gating layer was
    satisfied. A regression sets it to zero and records when - not a decrement,
    because a service that flaps green-red-green has not been stable for two of
    three samples, it has been unstable throughout.
    """

    required_s: int
    required_samples: int
    started_at: Optional[datetime] = None
    consecutive_passes: int = 0
    last_sample_at: Optional[datetime] = None
    regressions: int = 0
    last_regression_at: Optional[datetime] = None
    last_regression_reason: str = ""

    def __post_init__(self) -> None:
        if self.required_s < 0 or self.required_samples < 1:
            raise GovernanceValidationError(
                "a stability window needs a non-negative duration and at least "
                "one sample"
            )

    def observed(
        self, *, at: datetime, healthy: bool, reason: str = ""
    ) -> "StabilityWindow":
        if not healthy:
            return replace(
                self,
                started_at=None,
                consecutive_passes=0,
                last_sample_at=at,
                regressions=self.regressions + 1,
                last_regression_at=at,
                last_regression_reason=str(reason or "")[:MAX_DETAIL_CHARS],
            )
        return replace(
            self,
            started_at=self.started_at or at,
            consecutive_passes=self.consecutive_passes + 1,
            last_sample_at=at,
        )

    def elapsed_s(self, now: datetime) -> int:
        if self.started_at is None:
            return 0
        return max(0, int((now - self.started_at).total_seconds()))

    def satisfied(self, now: datetime) -> bool:
        return (
            self.consecutive_passes >= self.required_samples
            and self.elapsed_s(now) >= self.required_s
        )

    def remaining_s(self, now: datetime) -> int:
        return max(0, self.required_s - self.elapsed_s(now))

    def as_dict(self, now: Optional[datetime] = None) -> dict[str, Any]:
        return {
            "required_s": self.required_s,
            "required_samples": self.required_samples,
            "consecutive_passes": self.consecutive_passes,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "elapsed_s": self.elapsed_s(now) if now else 0,
            "remaining_s": self.remaining_s(now) if now else self.required_s,
            "satisfied": self.satisfied(now) if now else False,
            "regressions": self.regressions,
            "last_regression_at": (
                self.last_regression_at.isoformat()
                if self.last_regression_at
                else None
            ),
            "last_regression_reason": self.last_regression_reason,
        }


class RecoveryVerdict(str, Enum):
    RECOVERED = "recovered"
    NOT_RECOVERED = "not_recovered"
    STABILISING = "stabilising"
    """Every gating layer passes but the window has not been held long enough.
    A separate verdict rather than a not-yet-recovered, because the screen has
    something genuinely different to say and the ring has something to fill."""


@dataclass(frozen=True)
class ValidationCycle:
    """One pass over the four layers, kept forever.

    Cycles accumulate rather than overwrite: `cycle` counts how many times this
    incident has claimed recovery and been checked, and the escalation rule reads
    exactly that number. An incident on its fourth failed validation is a
    different situation from one on its first, and only a history says so.
    """

    id: str
    tenant_id: str
    incident_id: str
    app_id: str
    cycle: int
    started_at: datetime
    requested_by: str
    layers: tuple[LayerResult, ...] = ()
    window: Optional[StabilityWindow] = None
    verdict: RecoveryVerdict = RecoveryVerdict.NOT_RECOVERED
    finished_at: Optional[datetime] = None
    escalated: bool = False
    returned_to_active: bool = False
    evidence_node_ids: tuple[str, ...] = ()
    """Graph nodes this cycle *wrote* - the failure is fed back into the
    reasoning as evidence. The arrow points that way and only that way: the
    brain learns from validation, validation never reads the brain."""
    note: str = ""
    detail: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.cycle < 1:
            raise GovernanceValidationError("validation cycles start at 1")

    # -- reads ---------------------------------------------------------------

    def layer(self, layer: ValidationLayer) -> Optional[LayerResult]:
        return next((r for r in self.layers if r.layer is layer), None)

    @property
    def gating_layers(self) -> tuple[LayerResult, ...]:
        return tuple(r for r in self.layers if r.layer.gates_closure)

    @property
    def blocking(self) -> tuple[LayerResult, ...]:
        """Layers that stand between this incident and closure, worst first."""
        return tuple(
            sorted(
                (r for r in self.gating_layers if not r.satisfied),
                key=lambda r: r.layer.order,
            )
        )

    @property
    def all_layers_pass(self) -> bool:
        return all(r.satisfied for r in self.gating_layers) and bool(self.gating_layers)

    @property
    def failing_checks(self) -> tuple[LayerCheck, ...]:
        return tuple(check for result in self.layers for check in result.failing)

    def closure_lock_reason(self) -> str:
        """The sentence under the greyed-out Close button. Empty when it is not
        locked, which is what the cockpit branches on."""
        blocking = self.blocking
        if not blocking:
            return ""
        worst = blocking[0]
        names = ", ".join(check.label for check in worst.failing[:3]) or worst.summary
        return (
            f"the {worst.layer.value} layer is {worst.verdict.value}"
            + (f": {names}" if names else "")
            + ". An incident is not closed while the people it affects are still "
            "affected."
        )

    def as_dict(self, now: Optional[datetime] = None) -> dict[str, Any]:
        return {
            "validation_id": self.id,
            "incident_id": self.incident_id,
            "app_id": self.app_id,
            "cycle": self.cycle,
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "requested_by": self.requested_by,
            "verdict": self.verdict.value,
            "layers": [result.as_dict() for result in self.layers],
            "window": self.window.as_dict(now) if self.window else None,
            "closure_blocked": bool(self.blocking),
            "closure_lock_reason": self.closure_lock_reason(),
            "escalated": self.escalated,
            "returned_to_active": self.returned_to_active,
            "evidence_node_ids": list(self.evidence_node_ids),
            "note": self.note,
        }


def decide_verdict(
    cycle: ValidationCycle, *, now: datetime
) -> RecoveryVerdict:
    """The verdict from the layers and the window. Pure and total."""
    if not cycle.all_layers_pass:
        return RecoveryVerdict.NOT_RECOVERED
    if cycle.window is not None and not cycle.window.satisfied(now):
        return RecoveryVerdict.STABILISING
    return RecoveryVerdict.RECOVERED


def assert_closable(cycle: Optional[ValidationCycle], *, now: datetime) -> None:
    """The closure gate. Raises `ClosureBlockedError`; there is no override flag.

    A `None` cycle - an incident nobody validated - does *not* block: closing an
    incident is a human act and a commander who never asked for validation is
    making a judgement the platform has no standing to refuse. What the platform
    refuses is closing in the face of its own failing customer check, which is a
    different thing entirely: the measurement exists, it says people are still
    affected, and it is on the screen.
    """
    if cycle is None:
        return
    blocking = cycle.blocking
    if not blocking:
        return
    raise ClosureBlockedError(
        f"validation cycle {cycle.cycle} is {cycle.verdict.value}: "
        + cycle.closure_lock_reason()
    )
