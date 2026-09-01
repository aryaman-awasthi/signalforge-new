"""Severity proposed from observed impact (REQ-046).

The matrix that turns impact into a severity already exists: it is
`SeverityMatrix` on the onboarding profile, shipped configured with the P0/P1/P2
defaults and editable per application. What this module does is the other half -
turn what detection measured into the four numbers the matrix asks for:

| Matrix input | Where it comes from |
|---|---|
| journey tier | the most important journey with a failing probe, or with a breached SLO |
| regions affected | regions holding an anomalous customer-facing signal or a failing probe |
| success rate | the worst measured journey success rate, else the worst customer-facing ratio signal |
| customer impacting | any anomalous customer-facing family, or any failing probe |

Every one of those is a measurement, and each is reported beside the proposal so
an operator can see that "P0" came from "tier-1 journey below 80% in both
regions" rather than from a model.

**A proposal is not a declaration.** There is no state transition in this file
and no caller of it can produce one: `SeverityProposal` is a value object that
says what the matrix would say, and REQ-043 gives declaring, changing severity
and closing to humans alone. The proposal carries `requires_human` as a constant
true so that the fact survives into every serialisation of it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Sequence

from signalforge.domain.detection.anomaly import SignalState, SignalStatus
from signalforge.domain.detection.burn import SloBurn
from signalforge.domain.detection.corroboration import Corroboration, EvidenceRef
from signalforge.domain.detection.probes import JourneyHealth
from signalforge.domain.onboarding.profile import (
    CUSTOMER_FACING_SIGNALS,
    Journey,
    SeverityMatrix,
    Severity,
    SignalDirection,
)

RATIO_UNITS = frozenset({"ratio", "fraction", "rate"})
PERCENT_UNITS = frozenset({"percent", "pct", "%"})


@dataclass(frozen=True)
class Impact:
    """What was measured, in the four terms the severity matrix reasons in."""

    journey_tier: Optional[int]
    regions_affected: int
    regions_total: int
    success_rate_pct: Optional[float]
    customer_impacting: bool
    affected_regions: tuple[str, ...] = ()
    affected_journeys: tuple[str, ...] = ()
    detail: str = ""


@dataclass(frozen=True)
class SeverityProposal:
    """What the matrix says, and nothing more.

    `requires_human` is not a flag anybody sets: declaring is a human act, and a
    proposal that could be auto-accepted would make this the declaration.
    """

    severity: Severity
    impact: Impact
    rule: str
    corroborated: bool
    proposed_at: datetime
    evidence: tuple[EvidenceRef, ...] = ()
    detail: str = ""
    requires_human: bool = True

    def __post_init__(self) -> None:
        if self.requires_human is not True:  # pragma: no cover - a floor, not a branch
            raise ValueError(
                "a severity proposal always requires a human: only a person "
                "declares an incident (REQ-043)"
            )


def _signal_ratio(state: SignalState, unit: str, direction: SignalDirection) -> Optional[float]:
    """A customer-facing ratio signal read as a success percentage, if it is one."""
    if state.value is None:
        return None
    normalised = (unit or "").strip().lower()
    if normalised in PERCENT_UNITS:
        value = state.value
    elif normalised in RATIO_UNITS and 0.0 <= state.value <= 1.0:
        value = state.value * 100.0
    else:
        return None
    if direction is SignalDirection.HIGHER_IS_WORSE:
        # An error ratio: the success rate is what is left of it.
        return max(0.0, 100.0 - value)
    return max(0.0, min(100.0, value))


def assess_impact(
    states: Sequence[SignalState],
    journeys: Sequence[JourneyHealth],
    burns: Sequence[SloBurn],
    *,
    declared_journeys: Sequence[Journey] = (),
    declared_regions: Sequence[str] = (),
    signal_units: Optional[dict[str, tuple[str, SignalDirection]]] = None,
) -> Impact:
    units = signal_units or {}
    tiers = {journey.journey_id: journey.tier for journey in declared_journeys}

    failing_journeys = [row for row in journeys if row.failing]
    anomalous_customer = [
        state
        for state in states
        if state.status is SignalStatus.ANOMALOUS
        and state.family in CUSTOMER_FACING_SIGNALS
    ]

    affected_regions = sorted(
        {state.region for state in anomalous_customer if state.region}
        | {row.region for row in failing_journeys if row.region}
    )
    regions_total = len(
        {region for region in declared_regions if region}
        or {state.region for state in states if state.region}
    )
    # An application with no region label is one region: itself. Reporting zero
    # would make `min_regions_affected: 1` unsatisfiable for every single-region
    # deployment, which is most of them.
    regions_total = max(regions_total, 1)
    regions_affected = len(affected_regions)
    if regions_affected == 0 and (anomalous_customer or failing_journeys):
        regions_affected = 1

    journey_ids = sorted(
        {row.journey_id for row in failing_journeys if row.journey_id}
        | {burn.journey_id for burn in burns if burn.opens_watch and burn.journey_id}
    )
    candidate_tiers = [tiers[jid] for jid in journey_ids if jid in tiers]
    journey_tier = min(candidate_tiers) if candidate_tiers else None

    rates = [
        row.success_rate_pct
        for row in journeys
        if row.success_rate_pct is not None and row.journey_id in journey_ids
    ]
    for state in anomalous_customer:
        unit, direction = units.get(state.signal_key, ("", SignalDirection.LOWER_IS_WORSE))
        ratio = _signal_ratio(state, unit, direction)
        if ratio is not None:
            rates.append(ratio)
    success_rate = min(rates) if rates else None

    customer_impacting = bool(anomalous_customer or failing_journeys)
    detail_parts = []
    if journey_tier is not None:
        detail_parts.append(f"tier-{journey_tier} journey affected")
    if regions_affected:
        detail_parts.append(f"{regions_affected} of {regions_total} region(s)")
    if success_rate is not None:
        detail_parts.append(f"worst success rate {success_rate:.2f}%")
    if not customer_impacting:
        detail_parts.append("no customer-facing evidence")

    return Impact(
        journey_tier=journey_tier,
        regions_affected=regions_affected,
        regions_total=regions_total,
        success_rate_pct=success_rate,
        customer_impacting=customer_impacting,
        affected_regions=tuple(affected_regions),
        affected_journeys=tuple(journey_ids),
        detail="; ".join(detail_parts) or "nothing measured",
    )


def propose_severity(
    matrix: SeverityMatrix,
    impact: Impact,
    corroboration: Corroboration,
    *,
    at: datetime,
) -> SeverityProposal:
    """Run the application's own matrix over the measured impact.

    The matrix is deterministic and total, so the same impact always proposes
    the same severity and the proposal can name the rule that matched.
    """
    severity = matrix.classify(
        journey_tier=impact.journey_tier,
        regions_affected=impact.regions_affected,
        success_rate_pct=impact.success_rate_pct,
        customer_impacting=impact.customer_impacting,
    )
    matched = next(
        (
            rule
            for rule in matrix.rules
            if rule.severity is severity
            and rule.matches(
                journey_tier=impact.journey_tier,
                regions_affected=impact.regions_affected,
                success_rate_pct=impact.success_rate_pct,
                customer_impacting=impact.customer_impacting,
            )
        ),
        None,
    )
    rule = (
        matched.description or f"the {severity.value} rule"
        if matched is not None
        else f"no rule matched; the matrix default is {severity.value}"
    )
    return SeverityProposal(
        severity=severity,
        impact=impact,
        rule=rule,
        corroborated=corroboration.corroborated,
        proposed_at=at,
        evidence=corroboration.evidence,
        detail=f"{severity.value} proposed: {rule}. {impact.detail}",
    )
