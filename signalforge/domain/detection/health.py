"""The weighted health score, worst region wins (REQ-042).

The weights are the architecture's, and they are an opinion worth stating: a
service that is up but wrong matters more than a service that is slow.

| Family | Weight | Why it is worth that much |
|---|---|---|
| availability | 30 | not being there is the worst thing that can happen |
| errors | 25 | being there and failing is the second worst |
| business | 20 | payments not settling is what the business actually notices |
| latency | 15 | slow is bad, but slow is still working |
| saturation | 10 | a leading indicator, not an outcome |

Traffic carries **no** weight. It is context, not health: traffic falling can be
an outage or a Sunday, and a score that could not tell those apart would drift
every weekend. It is still mapped, still rolled up, and still available as
corroborating evidence - it just does not score.

Two rules make the number readable:

- **Only families that are mapped and measured count**, and the score is
  renormalised over the weight actually covered. An application with no
  saturation metric is not permanently capped at 90 for a signal it does not
  emit. `covered_weight` is reported beside the score so a 100 out of a covered
  weight of 45 cannot be mistaken for a 100 out of 100.
- **Worst region wins.** An application at 100 in one region and 20 in another
  is a 20. Averaging would let a healthy region hide a broken one, which is
  precisely the situation a two-region deployment exists to survive - and
  precisely the situation detection has to notice.

A family that is STALE scores as unmeasured rather than as healthy, and drops
out of the covered weight. Silence is not health.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional, Sequence

from signalforge.domain.detection.anomaly import SignalState, SignalStatus
from signalforge.domain.onboarding.profile import GoldenSignal

FAMILY_WEIGHTS: dict[GoldenSignal, int] = {
    GoldenSignal.AVAILABILITY: 30,
    GoldenSignal.ERRORS: 25,
    GoldenSignal.BUSINESS: 20,
    GoldenSignal.LATENCY: 15,
    GoldenSignal.SATURATION: 10,
}

TOTAL_WEIGHT = sum(FAMILY_WEIGHTS.values())
if TOTAL_WEIGHT != 100:  # pragma: no cover - a drift guard, not a branch
    raise RuntimeError(
        f"the health weights must sum to 100, not {TOTAL_WEIGHT}: a score out of "
        "something else is not the score the architecture specifies"
    )

HEALTHY_AT = 90.0
DEGRADED_AT = 70.0

THRESHOLD_FRACTION = 0.5
"""What a family scores when it is sitting exactly on its threshold. Half: the
threshold is where "unusual" starts, not where "broken" starts."""

SEVERE_DEVIATION = 3.0
"""How far past the threshold, in units of the threshold's own distance from
normal, scores zero. Three: past that the number stops discriminating, and a
family that is ten times past its threshold is not usefully worse than one that
is three times past it - both are broken."""


class HealthBand(str, Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"
    UNKNOWN = "unknown"


def band_for(score: Optional[float]) -> HealthBand:
    if score is None:
        return HealthBand.UNKNOWN
    if score >= HEALTHY_AT:
        return HealthBand.HEALTHY
    if score >= DEGRADED_AT:
        return HealthBand.DEGRADED
    return HealthBand.UNHEALTHY


def family_fraction(state: SignalState) -> Optional[float]:
    """0.0 to 1.0 for one signal, or None when nothing was measured.

    A piecewise-linear ramp rather than a step, so the score moves before the
    anomaly fires and an operator watching a tile sees it degrade rather than
    flip. At the mean it is 1.0, at the threshold 0.5, at `SEVERE_DEVIATION`
    times past the threshold 0.0.
    """
    if not state.status.measured:
        return None
    ratio = max(0.0, float(state.deviation))
    if ratio <= 1.0:
        return 1.0 - THRESHOLD_FRACTION * ratio
    beyond = min((ratio - 1.0) / (SEVERE_DEVIATION - 1.0), 1.0)
    return max(0.0, THRESHOLD_FRACTION * (1.0 - beyond))


@dataclass(frozen=True)
class FamilyScore:
    family: GoldenSignal
    weight: int
    fraction: Optional[float]
    signals: tuple[str, ...]
    detail: str = ""

    @property
    def measured(self) -> bool:
        return self.fraction is not None

    @property
    def points(self) -> float:
        return 0.0 if self.fraction is None else self.weight * self.fraction


@dataclass(frozen=True)
class RegionHealth:
    region: str
    score: Optional[float]
    band: HealthBand
    covered_weight: int
    families: tuple[FamilyScore, ...]

    @property
    def measured(self) -> bool:
        return self.score is not None


@dataclass(frozen=True)
class HealthScore:
    score: Optional[float]
    band: HealthBand
    worst_region: Optional[str]
    covered_weight: int
    regions: tuple[RegionHealth, ...]
    detail: str = ""


def score_region(region: str, states: Sequence[SignalState]) -> RegionHealth:
    """One region's score, from every mapped family that was measured in it."""
    families: list[FamilyScore] = []
    for family, weight in sorted(
        FAMILY_WEIGHTS.items(), key=lambda item: (-item[1], item[0].value)
    ):
        members = [state for state in states if state.family is family]
        if not members:
            continue
        fractions = [
            fraction
            for fraction in (family_fraction(state) for state in members)
            if fraction is not None
        ]
        # The worst signal in a family speaks for the family. Averaging would let
        # four healthy error metrics bury the one that is on fire.
        fraction = min(fractions) if fractions else None
        stale = [s.signal_key for s in members if s.status is SignalStatus.STALE]
        families.append(
            FamilyScore(
                family=family,
                weight=weight,
                fraction=fraction,
                signals=tuple(sorted(state.signal_key for state in members)),
                detail=(
                    f"no measurement; {sorted(stale)} stale"
                    if fraction is None and stale
                    else ("no measurement yet" if fraction is None else "")
                ),
            )
        )
    covered = sum(family.weight for family in families if family.measured)
    if covered == 0:
        return RegionHealth(
            region=region,
            score=None,
            band=HealthBand.UNKNOWN,
            covered_weight=0,
            families=tuple(families),
        )
    earned = sum(family.points for family in families if family.measured)
    score = round(100.0 * earned / covered, 2)
    return RegionHealth(
        region=region,
        score=score,
        band=band_for(score),
        covered_weight=covered,
        families=tuple(families),
    )


def score_health(states: Sequence[SignalState]) -> HealthScore:
    """Every region scored, and the worst one is the application's score."""
    regions = sorted({state.region for state in states})
    scored = tuple(
        score_region(region, [state for state in states if state.region == region])
        for region in regions
    )
    measured = [region for region in scored if region.measured]
    if not measured:
        return HealthScore(
            score=None,
            band=HealthBand.UNKNOWN,
            worst_region=None,
            covered_weight=0,
            regions=scored,
            detail=(
                "nothing has been measured yet; a health score of 100 for an "
                "application nobody has read would be a lie"
            ),
        )
    worst = min(measured, key=lambda region: (region.score, region.region))
    detail = f"worst of {len(measured)} region(s): {worst.region or 'unlabelled'}"
    if len(scored) != len(measured):
        unmeasured = sorted(r.region or "unlabelled" for r in scored if not r.measured)
        detail += f"; no measurement in {unmeasured}"
    return HealthScore(
        score=worst.score,
        band=worst.band,
        worst_region=worst.region,
        covered_weight=worst.covered_weight,
        regions=scored,
        detail=detail,
    )
