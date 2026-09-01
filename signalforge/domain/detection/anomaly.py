"""Sustained-breach anomaly detection (REQ-040).

A single evaluation past a threshold is not an anomaly. It is one scrape, and
one scrape is a garbage-collection pause, a redeploy, a slow disk or a
neighbour's backup job. An anomaly is a breach that is still there on the third
consecutive evaluation - fifteen seconds at the five-second cadence - and that
number is the whole reason the platform can put a Watch banner on a screen
without training its operators to ignore it.

Three rules make the streak trustworthy:

- **One clean evaluation clears it.** Not a decay, not a half-life: if the
  signal came back inside its threshold, the breach is over and the next one
  starts from zero. A streak that survived recovery would eventually fire on
  three unrelated blips an hour apart.
- **Missing data never advances a streak, and never clears one either.** A poll
  that could not reach the application lands STALE. Stale is not healthy - a
  detector that reads silence as health is worse than no detector - and it is
  not evidence of a breach either, so the streak resets and the state says why.
- **The state is durable.** The streak lives in `rollups.db`, so a restart in
  the middle of a developing incident resumes counting rather than starting
  again at zero and adding fifteen seconds to the detection time.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from enum import Enum
from typing import Optional

from signalforge.domain.detection.baselines import Basis, RollingBaseline
from signalforge.domain.detection.errors import DetectionValidationError
from signalforge.domain.onboarding.profile import GoldenSignal, SignalDirection

SUSTAINED_EVALUATIONS = 3
"""The architecture's rule, in one place. A breach becomes an anomaly on the
third consecutive evaluation that still sees it."""

MAX_DETAIL_CHARS = 300


class SignalStatus(str, Enum):
    """Deliberately five values, not two.

    UNKNOWN is a signal that has been mapped but never evaluated; STALE is one
    whose data stopped arriving. Collapsing either into HEALTHY would make the
    health score confident about something nobody measured, and collapsing
    either into BREACHING would page somebody because a scrape timed out.
    """

    HEALTHY = "healthy"
    BREACHING = "breaching"
    ANOMALOUS = "anomalous"
    STALE = "stale"
    UNKNOWN = "unknown"

    @property
    def anomalous(self) -> bool:
        return self is SignalStatus.ANOMALOUS

    @property
    def measured(self) -> bool:
        """Was a real value compared against a real threshold?"""
        return self in (
            SignalStatus.HEALTHY,
            SignalStatus.BREACHING,
            SignalStatus.ANOMALOUS,
        )


@dataclass(frozen=True)
class SignalObservation:
    """What one evaluation of one (signal, region) saw.

    `baseline` and `threshold` are None exactly when the detector could not
    compare - no data, or not enough history - and `reason` says which.
    """

    signal_key: str
    region: str
    family: GoldenSignal
    direction: SignalDirection
    customer_facing: bool
    basis: Basis
    value: Optional[float] = None
    at: Optional[datetime] = None
    baseline: Optional[RollingBaseline] = None
    threshold: Optional[float] = None
    deviation: float = 0.0
    breaching: bool = False
    reason: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "reason", (self.reason or "")[:MAX_DETAIL_CHARS])
        if self.breaching and self.threshold is None:
            raise DetectionValidationError(
                f"signal {self.signal_key!r} cannot breach a threshold it has none of"
            )

    @property
    def comparable(self) -> bool:
        return self.value is not None and self.threshold is not None

    @property
    def evidence_ref(self) -> str:
        """A stable pointer at the bucket this observation came from."""
        stamp = self.at.isoformat() if self.at else "none"
        region = self.region or "-"
        return f"rollup:{self.signal_key}/{region}@{stamp}"


@dataclass(frozen=True)
class SignalState:
    """The durable half: what the detector believes about one (signal, region).

    Written every evaluation, whether or not anything changed, because the
    freshness of the belief is part of it - "healthy as of four minutes ago" and
    "healthy as of now" are different facts to an operator.
    """

    tenant_id: str
    app_id: str
    signal_key: str
    region: str
    family: GoldenSignal
    status: SignalStatus = SignalStatus.UNKNOWN
    streak: int = 0
    value: Optional[float] = None
    basis: Basis = Basis.NONE
    baseline_mean: Optional[float] = None
    baseline_stddev: Optional[float] = None
    baseline_source: str = ""
    threshold: Optional[float] = None
    deviation: float = 0.0
    samples: int = 0
    first_breach_at: Optional[datetime] = None
    anomalous_since: Optional[datetime] = None
    last_value_at: Optional[datetime] = None
    evaluated_at: Optional[datetime] = None
    detail: str = ""

    def __post_init__(self) -> None:
        if int(self.streak) < 0:
            raise DetectionValidationError("a breach streak cannot be negative")
        object.__setattr__(self, "detail", (self.detail or "")[:MAX_DETAIL_CHARS])

    @property
    def key(self) -> tuple[str, str]:
        return (self.signal_key, self.region)


def initial_state(
    tenant_id: str,
    app_id: str,
    signal_key: str,
    region: str,
    family: GoldenSignal,
) -> SignalState:
    return SignalState(
        tenant_id=tenant_id,
        app_id=app_id,
        signal_key=signal_key,
        region=region,
        family=family,
        status=SignalStatus.UNKNOWN,
        detail="never evaluated",
    )


def advance(
    previous: SignalState, observation: SignalObservation, *, at: datetime
) -> SignalState:
    """The state machine, in one pure function.

    | Seen | Streak | Status |
    |---|---|---|
    | nothing comparable | reset to 0 | STALE (or UNKNOWN if never measured) |
    | inside the threshold | reset to 0 | HEALTHY |
    | past the threshold, streak < 3 | +1 | BREACHING |
    | past the threshold, streak >= 3 | +1 | ANOMALOUS |
    """
    if not observation.comparable:
        status = (
            SignalStatus.STALE
            if previous.status.measured or previous.last_value_at is not None
            else SignalStatus.UNKNOWN
        )
        return replace(
            previous,
            family=observation.family,
            status=status,
            streak=0,
            basis=observation.basis,
            first_breach_at=None,
            anomalous_since=None,
            evaluated_at=at,
            detail=observation.reason
            or "no comparable measurement in the evaluation window",
        )

    if not observation.breaching:
        return replace(
            previous,
            family=observation.family,
            status=SignalStatus.HEALTHY,
            streak=0,
            value=observation.value,
            basis=observation.basis,
            baseline_mean=observation.baseline.mean if observation.baseline else None,
            baseline_stddev=observation.baseline.stddev if observation.baseline else None,
            baseline_source=observation.baseline.source if observation.baseline else "",
            threshold=observation.threshold,
            deviation=observation.deviation,
            samples=observation.baseline.samples if observation.baseline else 0,
            first_breach_at=None,
            anomalous_since=None,
            last_value_at=observation.at,
            evaluated_at=at,
            detail=observation.reason or "within threshold",
        )

    streak = previous.streak + 1
    anomalous = streak >= SUSTAINED_EVALUATIONS
    return replace(
        previous,
        family=observation.family,
        status=SignalStatus.ANOMALOUS if anomalous else SignalStatus.BREACHING,
        streak=streak,
        value=observation.value,
        basis=observation.basis,
        baseline_mean=observation.baseline.mean if observation.baseline else None,
        baseline_stddev=observation.baseline.stddev if observation.baseline else None,
        baseline_source=observation.baseline.source if observation.baseline else "",
        threshold=observation.threshold,
        deviation=observation.deviation,
        samples=observation.baseline.samples if observation.baseline else 0,
        first_breach_at=previous.first_breach_at or observation.at or at,
        anomalous_since=(
            (previous.anomalous_since or observation.at or at) if anomalous else None
        ),
        last_value_at=observation.at,
        evaluated_at=at,
        detail=(
            observation.reason
            or (
                f"breach sustained {streak} of {SUSTAINED_EVALUATIONS} evaluations"
                if not anomalous
                else f"breach sustained across {streak} evaluations"
            )
        ),
    )
