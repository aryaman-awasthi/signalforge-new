"""Corroboration: the gate a proposal has to pass (REQ-041).

One family of signals going wrong is a story with one witness. Latency alone is
a slow disk, an error counter alone is a noisy client retrying, saturation alone
is a backup job. The rule this platform proposes under is that **two independent
signal families must agree and at least one of them must be customer-facing**,
because that is the smallest amount of evidence that distinguishes "something is
happening to the infrastructure" from "something is happening to customers".

Independence is by *family*, not by signal. Two error signals on two services
are one family and corroborate nothing: they are the same measurement taken
twice, and treating them as two witnesses is how alert storms turn into
incidents nobody declared.

Customer-facing is `CUSTOMER_FACING_SIGNALS` from the onboarding profile -
availability, business and errors - plus a failing synthetic probe, which is the
platform's own first-hand evidence that a journey does not work.

Every conclusion carries its evidence refs. A proposal an operator cannot drill
into is an assertion, and the whole design of this product is that a human
decides.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Sequence

from signalforge.domain.detection.anomaly import SignalState, SignalStatus
from signalforge.domain.detection.burn import SloBurn
from signalforge.domain.detection.probes import JourneyHealth
from signalforge.domain.onboarding.profile import CUSTOMER_FACING_SIGNALS, GoldenSignal

MIN_INDEPENDENT_FAMILIES = 2
PROBE_FAMILY = GoldenSignal.AVAILABILITY
"""A failing synthetic probe is availability evidence. It is not a family of its
own: an application whose only two witnesses are "the probe failed" and "the
availability metric fell" has one witness twice."""


@dataclass(frozen=True)
class EvidenceRef:
    """A pointer at the observation a conclusion was drawn from.

    `ref` is stable and re-readable - a rollup bucket, an SLO window, a probe
    run, an alert id - so the proposal sheet can link to what it saw rather than
    paraphrasing it.
    """

    kind: str
    ref: str
    detail: str = ""
    family: Optional[str] = None
    region: str = ""
    customer_facing: bool = False
    at: Optional[datetime] = None


@dataclass(frozen=True)
class Corroboration:
    families: tuple[str, ...]
    customer_facing_families: tuple[str, ...]
    regions: tuple[str, ...]
    corroborated: bool
    reason: str
    evidence: tuple[EvidenceRef, ...] = ()

    @property
    def independent_families(self) -> int:
        return len(self.families)


def corroborate(
    states: Sequence[SignalState],
    burns: Sequence[SloBurn],
    journeys: Sequence[JourneyHealth],
    *,
    at: Optional[datetime] = None,
) -> Corroboration:
    """Which families are anomalous right now, and does that clear the gate."""
    evidence: list[EvidenceRef] = []
    families: set[GoldenSignal] = set()
    regions: set[str] = set()

    for state in sorted(states, key=lambda s: (s.signal_key, s.region)):
        if state.status is not SignalStatus.ANOMALOUS:
            continue
        families.add(state.family)
        if state.region:
            regions.add(state.region)
        evidence.append(
            EvidenceRef(
                kind="signal",
                ref=f"rollup:{state.signal_key}/{state.region or '-'}",
                detail=state.detail,
                family=state.family.value,
                region=state.region,
                customer_facing=state.family in CUSTOMER_FACING_SIGNALS,
                at=state.last_value_at or state.evaluated_at,
            )
        )

    for row in journeys:
        if not row.failing:
            continue
        families.add(PROBE_FAMILY)
        if row.region:
            regions.add(row.region)
        evidence.append(
            EvidenceRef(
                kind="probe",
                ref=f"journey:{row.journey_id or '-'}/{row.region or '-'}",
                detail=(
                    f"{row.failed} of {row.measured} probe run(s) failed"
                    + (f": {row.last_detail}" if row.last_detail else "")
                ),
                family=PROBE_FAMILY.value,
                region=row.region,
                customer_facing=True,
                at=row.last_at,
            )
        )

    for burn in burns:
        if burn.verdict.rank == 0:
            continue
        window = burn.fast if burn.fast.breached else burn.slow
        evidence.append(
            EvidenceRef(
                kind="slo",
                ref=window.evidence_ref,
                detail=burn.detail,
                family=None,
                customer_facing=True,
                at=at,
            )
        )

    customer_facing = sorted(
        family.value for family in families if family in CUSTOMER_FACING_SIGNALS
    )
    ordered = tuple(sorted(family.value for family in families))
    enough = len(ordered) >= MIN_INDEPENDENT_FAMILIES
    corroborated = enough and bool(customer_facing)

    if corroborated:
        reason = (
            f"{len(ordered)} independent families agree ({', '.join(ordered)}) and "
            f"{customer_facing[0]} is customer-facing"
        )
    elif not ordered:
        reason = "nothing is anomalous"
    elif not enough:
        reason = (
            f"only the {ordered[0]} family is anomalous; corroboration needs "
            f"{MIN_INDEPENDENT_FAMILIES} independent families"
        )
    else:
        reason = (
            f"{len(ordered)} families are anomalous ({', '.join(ordered)}) but none "
            "of them is customer-facing, so nothing yet says customers are affected"
        )

    return Corroboration(
        families=ordered,
        customer_facing_families=tuple(customer_facing),
        regions=tuple(sorted(regions)),
        corroborated=corroborated,
        reason=reason,
        evidence=tuple(evidence),
    )
