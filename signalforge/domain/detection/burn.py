"""SLO error-budget burn rates (REQ-041).

A burn rate is "how many times faster than planned is the error budget being
spent". At 1x the budget lasts exactly the window it was sized for; at 14x a
thirty-day budget is gone in about two days, which is why the fast burn is the
signal that something needs a human now, and at 6x it is gone in five, which is
worth watching but not worth waking anyone.

    burn = observed error ratio / (1 - objective)

Two windows, both from the frozen onboarding profile's own multipliers so an
application can be tuned without touching this code:

| Window | Multiplier | What it does |
|---|---|---|
| 5 minutes | `fast_burn_multiplier`, default 14.4 | **proposes** an incident |
| 30 minutes | `slow_burn_multiplier`, default 6.0 | opens **Watch** |

The indicator is the mapped signal named by `SloTarget.indicator`. It must be a
ratio, because that is the only thing an error budget can be computed from: a
request *count* says nothing about what fraction of requests failed. A signal
that is not a ratio produces a burn observation that says exactly that and no
verdict, rather than a number derived from the wrong units - a burn rate that is
silently wrong is worse than one that is missing, because the missing one gets
fixed.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Mapping, Optional, Sequence

from signalforge.domain.detection.baselines import Observation
from signalforge.domain.onboarding.profile import (
    SignalDirection,
    SignalMapping,
    SloTarget,
)

FAST_BURN_WINDOW_S = 300
SLOW_BURN_WINDOW_S = 1_800

FAST_WINDOW = "fast"
SLOW_WINDOW = "slow"

MIN_BURN_SAMPLES = 3
"""Three buckets - fifteen seconds - before a burn rate is reported at all. A
burn computed from one sample is that sample with a division applied to it."""

PERCENT_UNITS = frozenset({"percent", "pct", "%"})
"""Units that mean the number is already scaled to 0-100. Anything else claiming
to be a ratio must be inside 0-1, and a value outside that range is reported as
"this indicator is not a ratio" rather than divided by a guess."""


class BurnVerdict(str, Enum):
    NONE = "none"
    WATCH = "watch"
    PROPOSE = "propose"

    @property
    def rank(self) -> int:
        return {"none": 0, "watch": 1, "propose": 2}[self.value]


@dataclass(frozen=True)
class BurnObservation:
    """One SLO, one window."""

    slo_id: str
    window: str
    window_s: int
    multiplier: float
    samples: int = 0
    error_ratio: Optional[float] = None
    budget_ratio: float = 0.0
    burn_rate: Optional[float] = None
    breached: bool = False
    region: str = ""
    detail: str = ""

    @property
    def measured(self) -> bool:
        return self.burn_rate is not None

    @property
    def evidence_ref(self) -> str:
        region = self.region or "-"
        return f"slo:{self.slo_id}/{self.window}/{region}"


@dataclass(frozen=True)
class SloBurn:
    """Both windows of one SLO, and what they add up to."""

    slo_id: str
    name: str
    indicator: str
    journey_id: Optional[str]
    objective_pct: float
    fast: BurnObservation
    slow: BurnObservation
    verdict: BurnVerdict
    detail: str = ""

    @property
    def proposes(self) -> bool:
        return self.verdict is BurnVerdict.PROPOSE

    @property
    def opens_watch(self) -> bool:
        return self.verdict is not BurnVerdict.NONE


def error_ratio(
    signal: SignalMapping, points: Sequence[Observation]
) -> tuple[Optional[float], str]:
    """The mean error fraction over the window, or None with the reason.

    A success ratio (`lower_is_worse`) is inverted; an error ratio
    (`higher_is_worse`) is taken as it stands. Which of the two an operator
    mapped is exactly what `SignalDirection` records, which is why detection can
    read a budget off a signal called `payment_success_ratio` without a naming
    convention.
    """
    values = [point.value for point in points]
    if len(values) < MIN_BURN_SAMPLES:
        return None, (
            f"{len(values)} sample(s) in the window; at least {MIN_BURN_SAMPLES} "
            "are needed before a burn rate means anything"
        )
    scale = 100.0 if (signal.unit or "").strip().lower() in PERCENT_UNITS else 1.0
    scaled = [value / scale for value in values]
    if any(value < 0.0 or value > 1.0 for value in scaled):
        return None, (
            f"indicator {signal.key!r} is not a ratio - it ranges "
            f"{min(scaled):.4g} to {max(scaled):.4g}, and an error budget can only "
            "be computed from a fraction of good events"
        )
    mean = sum(scaled) / len(scaled)
    if signal.direction is SignalDirection.LOWER_IS_WORSE:
        return max(0.0, 1.0 - mean), "success ratio inverted into an error ratio"
    return mean, "error ratio read directly"


def observe_window(
    slo: SloTarget,
    signal: Optional[SignalMapping],
    points: Sequence[Observation],
    *,
    window: str,
    window_s: int,
    multiplier: float,
    region: str = "",
) -> BurnObservation:
    budget = slo.error_budget_pct / 100.0
    if signal is None:
        return BurnObservation(
            slo_id=slo.slo_id,
            window=window,
            window_s=window_s,
            multiplier=multiplier,
            budget_ratio=budget,
            region=region,
            detail=(
                f"SLO {slo.slo_id!r} names indicator {slo.indicator!r}, which is not "
                "a mapped golden signal; map it or point the SLO at one that is"
            ),
        )
    ratio, reason = error_ratio(signal, points)
    if ratio is None:
        return BurnObservation(
            slo_id=slo.slo_id,
            window=window,
            window_s=window_s,
            multiplier=multiplier,
            samples=len(points),
            budget_ratio=budget,
            region=region,
            detail=reason,
        )
    burn = ratio / budget if budget > 0 else 0.0
    return BurnObservation(
        slo_id=slo.slo_id,
        window=window,
        window_s=window_s,
        multiplier=multiplier,
        samples=len(points),
        error_ratio=ratio,
        budget_ratio=budget,
        burn_rate=burn,
        breached=burn >= multiplier,
        region=region,
        detail=(
            f"{ratio:.4%} of events failed against a {budget:.4%} budget: "
            f"{burn:.2f}x ({reason})"
        ),
    )


def worst_window(
    slo: SloTarget,
    signal: Optional[SignalMapping],
    by_region: Mapping[str, Sequence[Observation]],
    *,
    window: str,
    window_s: int,
    multiplier: float,
) -> BurnObservation:
    """The worst region's burn, not the average of every region's.

    A two-region deployment exists so one region can fail while the other keeps
    serving; averaging the burn across both is how a total failure in one region
    reads as half a problem. The observation names the region it came from.
    """
    if not by_region:
        return observe_window(
            slo, signal, (), window=window, window_s=window_s, multiplier=multiplier
        )
    observations = [
        observe_window(
            slo,
            signal,
            points,
            window=window,
            window_s=window_s,
            multiplier=multiplier,
            region=region,
        )
        for region, points in sorted(by_region.items())
    ]
    measured = [one for one in observations if one.measured]
    if not measured:
        return observations[0]
    return max(measured, key=lambda one: (one.burn_rate or 0.0, one.region))


def evaluate_slo(
    slo: SloTarget,
    signal: Optional[SignalMapping],
    fast_points: Mapping[str, Sequence[Observation]],
    slow_points: Mapping[str, Sequence[Observation]],
) -> SloBurn:
    """Fast wins. A fast burn is a slow burn that has not been going long enough
    to be a slow burn yet, so a proposal must not be downgraded to Watch by the
    thirty-minute window still looking calm."""
    fast = worst_window(
        slo,
        signal,
        fast_points,
        window=FAST_WINDOW,
        window_s=FAST_BURN_WINDOW_S,
        multiplier=slo.fast_burn_multiplier,
    )
    slow = worst_window(
        slo,
        signal,
        slow_points,
        window=SLOW_WINDOW,
        window_s=SLOW_BURN_WINDOW_S,
        multiplier=slo.slow_burn_multiplier,
    )
    if fast.breached:
        verdict = BurnVerdict.PROPOSE
        detail = (
            f"fast burn {fast.burn_rate:.2f}x is at or past "
            f"{slo.fast_burn_multiplier:.1f}x over {FAST_BURN_WINDOW_S}s"
        )
    elif slow.breached:
        verdict = BurnVerdict.WATCH
        detail = (
            f"slow burn {slow.burn_rate:.2f}x is at or past "
            f"{slo.slow_burn_multiplier:.1f}x over {SLOW_BURN_WINDOW_S}s"
        )
    elif not fast.measured and not slow.measured:
        verdict = BurnVerdict.NONE
        detail = fast.detail
    else:
        verdict = BurnVerdict.NONE
        detail = "error budget is being spent no faster than planned"
    return SloBurn(
        slo_id=slo.slo_id,
        name=slo.name,
        indicator=slo.indicator,
        journey_id=slo.journey_id,
        objective_pct=slo.objective_pct,
        fast=fast,
        slow=slow,
        verdict=verdict,
        detail=detail,
    )
