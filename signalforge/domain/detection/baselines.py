"""Rolling baselines and the threshold a breach is measured against (REQ-040).

Two decisions carry this module.

**The baseline is rolling, and it excludes the bucket being judged.** An
application at 2% errors on a Tuesday afternoon is not the same application at
2% errors during a release freeze, so the comparison is against its own recent
past rather than against a number learned once during onboarding. The most
recent bucket is left out of its own baseline: including it would let a single
large sample raise the bar it is being measured against, which is exactly how a
step change hides. The onboarding-time baseline (`profile.baselines`) is still
used - as the fallback for a signal that has not accumulated a window yet, which
is every signal for the first thirty minutes after onboarding.

**Whether a series is a level or a counter is derived, not configured.** The
frozen contract publishes Prometheus text exposition, which does not say whether
a number accumulates, and the onboarding profile records which *direction* is
bad rather than which shape the number has. So the shape is read off the window:
a window that never decreases and does move is a counter, and a counter is
evaluated as a per-second rate. `errors_total` rising forever is not an anomaly;
`errors_total` rising four times faster than it was is.

A counter that resets inside the window stops being monotonic and is evaluated
as a level for that window, which is conservative - a reset looks like a large
drop, and a drop in an accumulating error count is not something to page anyone
about.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Optional, Sequence

from signalforge.domain.detection.errors import DetectionValidationError
from signalforge.domain.detection.rollups import MetricRollup
from signalforge.domain.onboarding.profile import Baseline, SignalDirection

DEFAULT_BASELINE_WINDOW_S = 1_800
"""Thirty minutes: the same window the slow burn rate uses, so "what normal
looks like" and "how fast the budget is going" are talking about the same past."""

MIN_ROLLING_SAMPLES = 6
"""Six five-second buckets is half a minute of observation. Fewer than that and
the standard deviation is an artefact of the sample size rather than of the
application, and the detector falls back to the onboarding baseline or reports
insufficient data rather than inventing a threshold."""

BASELINE_LAG_BUCKETS = 4
"""How many buckets the reference window stops short of the bucket being judged.

This is the rule that makes a *sustained* breach detectable at all. An anomaly
has to last three evaluations, so by the third one the first two breaching
samples are already history - and a baseline that included them would have
widened its own standard deviation by exactly the size of the event it is
supposed to notice, and the third evaluation would read as normal. Four buckets
is the three evaluations plus one, so the reference window never contains a
sample from the breach it is being compared against."""

ANOMALY_SIGMA = 3.0
MIN_RELATIVE_DEVIATION = 0.10
"""A floor under the threshold, as a fraction of the mean. A metric that has
been perfectly flat has a standard deviation of zero, and 3-sigma of zero would
make the next digit of noise an anomaly. Ten percent of the level is the
smallest move worth waking a detector for."""

MIN_ABSOLUTE_DEVIATION = 1e-9
"""And a floor under *that*, for a signal whose mean is zero. An error counter
that has never moved has no spread and no level to take a percentage of, so any
movement at all is a breach - which is the right answer for an error counter and
is why the operator's direction is stored rather than guessed."""


class Basis(str, Enum):
    """What number the detector actually compared."""

    LEVEL = "level"
    RATE = "rate"
    NONE = "none"


@dataclass(frozen=True)
class Observation:
    """One point of the series the detector reasons over, after the level/rate
    decision. `at` is the bucket it came from, so evidence can point at it."""

    at: datetime
    value: float


@dataclass(frozen=True)
class RollingBaseline:
    signal_key: str
    region: str
    basis: Basis
    samples: int
    window_s: float
    mean: float
    stddev: float
    minimum: float
    maximum: float
    p95: float
    source: str = "rolling"

    def __post_init__(self) -> None:
        if int(self.samples) < 1:
            raise DetectionValidationError("a baseline needs at least one sample")
        if float(self.window_s) < 0:
            raise DetectionValidationError("a baseline window cannot be negative")

    @property
    def rolling(self) -> bool:
        return self.source == "rolling"


MIN_COUNTER_INCREASE_FRACTION = 0.5
"""How much of the window a counter has to actually be counting in.

"Non-decreasing and not flat" is not enough, and getting this wrong is the
difference between detecting an incident and hiding one: a *gauge* that sits at
0.01 for ten buckets and then steps to 0.55 is also non-decreasing and also not
flat, and reading it as a counter turns a five-thousand-percent jump in the
error ratio into a one-off rate of 0.108 against a baseline rate of zero. A real
counter increments in most of its buckets; a gauge that stepped once increments
in one. Half the window is the line."""


def series_basis(rollups: Sequence[MetricRollup]) -> Basis:
    """LEVEL or RATE, from the shape of the window.

    Pure and total: a window that never decreases and increases in at least half
    its buckets is an accumulating counter, and is evaluated as a per-second
    rate. Anything else - including a gauge that made one large step - is a
    level.
    """
    values = [rollup.mean for rollup in rollups]
    if len(values) < 3:
        return Basis.LEVEL
    pairs = list(zip(values, values[1:]))
    if any(b < a for a, b in pairs):
        return Basis.LEVEL
    increases = sum(1 for a, b in pairs if b > a)
    if increases >= max(2, MIN_COUNTER_INCREASE_FRACTION * len(pairs)):
        return Basis.RATE
    return Basis.LEVEL


def observations(rollups: Sequence[MetricRollup]) -> tuple[Basis, tuple[Observation, ...]]:
    """The series the detector compares, plus which basis produced it.

    On the RATE basis the first bucket has no predecessor and produces no
    observation, so a window of *n* buckets yields *n-1* points. That is stated
    rather than padded: a rate needs two readings and pretending otherwise would
    put a fabricated point at the start of every baseline.
    """
    ordered = sorted(rollups, key=lambda rollup: rollup.bucket_start)
    if not ordered:
        return Basis.NONE, ()
    basis = series_basis(ordered)
    if basis is Basis.LEVEL:
        return basis, tuple(
            Observation(at=rollup.bucket_start, value=rollup.mean) for rollup in ordered
        )
    points: list[Observation] = []
    for previous, current in zip(ordered, ordered[1:]):
        seconds = (current.bucket_start - previous.bucket_start).total_seconds()
        if seconds <= 0:
            continue
        points.append(
            Observation(
                at=current.bucket_start,
                value=(current.mean - previous.mean) / seconds,
            )
        )
    return basis, tuple(points)


def rolling_baseline(
    signal_key: str,
    region: str,
    reference: Sequence[Observation],
    *,
    basis: Basis,
    window_s: float,
) -> Optional[RollingBaseline]:
    """A baseline over the reference points, or None if there are too few.

    None rather than a baseline built from two samples: a detector that fires
    against a threshold derived from a sample of two has learned nothing, and
    "not enough history yet" is a state an operator can understand.
    """
    values = [point.value for point in reference]
    if len(values) < MIN_ROLLING_SAMPLES:
        return None
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int(round(0.95 * len(ordered) + 0.5)) - 1))
    return RollingBaseline(
        signal_key=signal_key,
        region=region,
        basis=basis,
        samples=len(values),
        window_s=float(window_s),
        mean=statistics.fmean(ordered),
        stddev=statistics.pstdev(ordered) if len(ordered) > 1 else 0.0,
        minimum=ordered[0],
        maximum=ordered[-1],
        p95=ordered[index],
        source="rolling",
    )


def from_onboarding(baseline: Baseline, region: str) -> RollingBaseline:
    """The onboarding-time baseline, adapted to the same shape.

    Marked `source="onboarding"` rather than silently substituted, because a
    proposal built on a baseline learned in five scrapes during a wizard step
    should say so. It is always a LEVEL basis: the wizard samples an instant
    repeatedly, so it never observed an accumulation.
    """
    return RollingBaseline(
        signal_key=baseline.signal_key,
        region=region,
        basis=Basis.LEVEL,
        samples=baseline.samples,
        window_s=baseline.window_s,
        mean=baseline.mean,
        stddev=baseline.stddev,
        minimum=baseline.minimum,
        maximum=baseline.maximum,
        p95=baseline.p95,
        source="onboarding",
    )


def deviation_allowance(baseline: RollingBaseline, sigma: float = ANOMALY_SIGMA) -> float:
    """How far from the mean is still normal. Always strictly positive."""
    return max(
        sigma * abs(baseline.stddev),
        MIN_RELATIVE_DEVIATION * abs(baseline.mean),
        MIN_ABSOLUTE_DEVIATION,
    )


def threshold_for(
    baseline: RollingBaseline,
    direction: SignalDirection,
    sigma: float = ANOMALY_SIGMA,
) -> float:
    """The value at which the signal starts breaching, in the bad direction."""
    allowance = deviation_allowance(baseline, sigma)
    if direction is SignalDirection.HIGHER_IS_WORSE:
        return baseline.mean + allowance
    return baseline.mean - allowance


def is_breaching(value: float, threshold: float, direction: SignalDirection) -> bool:
    if direction is SignalDirection.HIGHER_IS_WORSE:
        return value > threshold
    return value < threshold


def deviation_ratio(
    value: float, baseline: RollingBaseline, threshold: float, direction: SignalDirection
) -> float:
    """How far past normal, in units of "the distance to the threshold".

    0.0 is at the mean or better, 1.0 is exactly at the threshold, 3.0 is three
    times as far past normal as the threshold is. The health score reads this,
    so a signal that is slightly over the line and a signal that has fallen off
    a cliff do not score the same.
    """
    if direction is SignalDirection.HIGHER_IS_WORSE:
        excess = value - baseline.mean
    else:
        excess = baseline.mean - value
    span = abs(threshold - baseline.mean) or MIN_ABSOLUTE_DEVIATION
    return max(excess, 0.0) / span
