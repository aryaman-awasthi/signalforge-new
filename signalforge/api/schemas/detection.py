"""Response shapes for the detection surface (S4-T01).

Everything the detector concluded, plus what it concluded it *from*. The shapes
are wide on purpose: a Watch banner that says "something is wrong" and cannot
say which signal, against which baseline, over how many samples, is a banner
operators learn to close. Every conclusion here carries the threshold it was
measured against and the evidence ref that points at the bucket.

`declared` and `requires_human` are literal constants on the proposal shape.
They are not state - nothing in this sprint can declare - and publishing them as
fields means a client cannot render a proposal as though it were an incident.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field

from signalforge.domain.detection.engine import DetectionVerdict
from signalforge.domain.detection.rollups import MetricRollup


class SignalStateOut(BaseModel):
    signal_key: str
    region: str
    family: str
    status: str
    streak: int
    basis: str = Field(
        description="level or rate - whether the detector compared the value or "
                    "its per-second change, decided from the shape of the window"
    )
    value: Optional[float] = None
    threshold: Optional[float] = None
    baseline_mean: Optional[float] = None
    baseline_stddev: Optional[float] = None
    baseline_source: str = ""
    baseline_samples: int = 0
    deviation: float = 0.0
    first_breach_at: Optional[datetime] = None
    anomalous_since: Optional[datetime] = None
    last_value_at: Optional[datetime] = None
    evaluated_at: Optional[datetime] = None
    detail: str = ""


class FamilyScoreOut(BaseModel):
    family: str
    weight: int
    fraction: Optional[float] = None
    signals: list[str] = Field(default_factory=list)
    detail: str = ""


class RegionHealthOut(BaseModel):
    region: str
    score: Optional[float] = None
    band: str
    covered_weight: int
    families: list[FamilyScoreOut] = Field(default_factory=list)


class HealthScoreOut(BaseModel):
    score: Optional[float] = None
    band: str
    worst_region: Optional[str] = None
    covered_weight: int = 0
    regions: list[RegionHealthOut] = Field(default_factory=list)
    detail: str = ""


class BurnWindowOut(BaseModel):
    window: str
    window_s: int
    multiplier: float
    samples: int
    error_ratio: Optional[float] = None
    budget_ratio: float
    burn_rate: Optional[float] = None
    breached: bool
    region: str = ""
    detail: str = ""


class SloBurnOut(BaseModel):
    slo_id: str
    name: str
    indicator: str
    journey_id: Optional[str] = None
    objective_pct: float
    verdict: str
    fast: BurnWindowOut
    slow: BurnWindowOut
    detail: str = ""


class JourneyHealthOut(BaseModel):
    journey_id: Optional[str] = None
    region: str
    tier: Optional[int] = None
    passed: int
    failed: int
    unknown: int = Field(
        description="probe runs that could not be performed. Never counted as a "
                    "failure: 'we could not check' and 'the journey is broken' "
                    "lead to opposite next actions"
    )
    success_rate_pct: Optional[float] = None
    last_at: Optional[datetime] = None
    last_detail: str = ""


class EvidenceRefOut(BaseModel):
    kind: str
    ref: str
    detail: str = ""
    family: Optional[str] = None
    region: str = ""
    customer_facing: bool = False
    at: Optional[datetime] = None


class CorroborationOut(BaseModel):
    corroborated: bool
    families: list[str] = Field(default_factory=list)
    customer_facing_families: list[str] = Field(default_factory=list)
    regions: list[str] = Field(default_factory=list)
    reason: str = ""
    evidence: list[EvidenceRefOut] = Field(default_factory=list)


class ImpactOut(BaseModel):
    journey_tier: Optional[int] = None
    regions_affected: int
    regions_total: int
    success_rate_pct: Optional[float] = None
    customer_impacting: bool
    affected_regions: list[str] = Field(default_factory=list)
    affected_journeys: list[str] = Field(default_factory=list)
    detail: str = ""


class SeverityProposalOut(BaseModel):
    severity: str
    rule: str
    corroborated: bool
    proposed_at: datetime
    impact: ImpactOut
    evidence: list[EvidenceRefOut] = Field(default_factory=list)
    detail: str = ""
    requires_human: bool = Field(
        default=True,
        description="Always true. Only a person declares an incident (REQ-043).",
    )
    declared: bool = Field(
        default=False,
        description="Always false in this API. Detection proposes; it never declares.",
    )


class AlertClusterOut(BaseModel):
    cluster_id: str
    root_alert_id: str
    root_node_id: Optional[str] = None
    alert_ids: list[str] = Field(default_factory=list)
    node_ids: list[str] = Field(default_factory=list)
    names: list[str] = Field(default_factory=list)
    severity: str
    size: int
    suppressed_count: int
    started_at: datetime
    latest_at: datetime
    detail: str = ""


class StormOut(BaseModel):
    alerts: int
    clusters: list[AlertClusterOut] = Field(default_factory=list)
    suppressed: int
    window_s: int
    storming: bool


class DetectionOut(BaseModel):
    app_id: str
    stance: str
    evaluated_at: datetime
    reasons: list[str] = Field(default_factory=list)
    signals: list[SignalStateOut] = Field(default_factory=list)
    health: HealthScoreOut
    burns: list[SloBurnOut] = Field(default_factory=list)
    journeys: list[JourneyHealthOut] = Field(default_factory=list)
    corroboration: CorroborationOut
    severity: Optional[SeverityProposalOut] = None
    storm: StormOut


class RollupOut(BaseModel):
    signal_key: str
    region: str
    family: str
    bucket_start: datetime
    samples: int
    mean: float
    minimum: float
    maximum: float
    last: float
    series_count: int


class RollupsOut(BaseModel):
    app_id: str
    window_s: float
    interval_s: int
    count: int
    rollups: list[RollupOut] = Field(default_factory=list)


# --- mappers ------------------------------------------------------------------


def detection_out(verdict: DetectionVerdict) -> DetectionOut:
    return DetectionOut(
        app_id=verdict.app_id,
        stance=verdict.stance.value,
        evaluated_at=verdict.evaluated_at,
        reasons=list(verdict.reasons),
        signals=[
            SignalStateOut(
                signal_key=state.signal_key,
                region=state.region,
                family=state.family.value,
                status=state.status.value,
                streak=state.streak,
                basis=state.basis.value,
                value=state.value,
                threshold=state.threshold,
                baseline_mean=state.baseline_mean,
                baseline_stddev=state.baseline_stddev,
                baseline_source=state.baseline_source,
                baseline_samples=state.samples,
                deviation=state.deviation,
                first_breach_at=state.first_breach_at,
                anomalous_since=state.anomalous_since,
                last_value_at=state.last_value_at,
                evaluated_at=state.evaluated_at,
                detail=state.detail,
            )
            for state in verdict.states
        ],
        health=HealthScoreOut(
            score=verdict.health.score,
            band=verdict.health.band.value,
            worst_region=verdict.health.worst_region,
            covered_weight=verdict.health.covered_weight,
            regions=[
                RegionHealthOut(
                    region=region.region,
                    score=region.score,
                    band=region.band.value,
                    covered_weight=region.covered_weight,
                    families=[
                        FamilyScoreOut(
                            family=family.family.value,
                            weight=family.weight,
                            fraction=family.fraction,
                            signals=list(family.signals),
                            detail=family.detail,
                        )
                        for family in region.families
                    ],
                )
                for region in verdict.health.regions
            ],
            detail=verdict.health.detail,
        ),
        burns=[
            SloBurnOut(
                slo_id=burn.slo_id,
                name=burn.name,
                indicator=burn.indicator,
                journey_id=burn.journey_id,
                objective_pct=burn.objective_pct,
                verdict=burn.verdict.value,
                fast=_window_out(burn.fast),
                slow=_window_out(burn.slow),
                detail=burn.detail,
            )
            for burn in verdict.burns
        ],
        journeys=[
            JourneyHealthOut(
                journey_id=row.journey_id,
                region=row.region,
                tier=row.tier,
                passed=row.passed,
                failed=row.failed,
                unknown=row.unknown,
                success_rate_pct=row.success_rate_pct,
                last_at=row.last_at,
                last_detail=row.last_detail,
            )
            for row in verdict.journeys
        ],
        corroboration=CorroborationOut(
            corroborated=verdict.corroboration.corroborated,
            families=list(verdict.corroboration.families),
            customer_facing_families=list(
                verdict.corroboration.customer_facing_families
            ),
            regions=list(verdict.corroboration.regions),
            reason=verdict.corroboration.reason,
            evidence=[_evidence_out(ref) for ref in verdict.corroboration.evidence],
        ),
        severity=(
            None
            if verdict.severity is None
            else SeverityProposalOut(
                severity=verdict.severity.severity.value,
                rule=verdict.severity.rule,
                corroborated=verdict.severity.corroborated,
                proposed_at=verdict.severity.proposed_at,
                impact=ImpactOut(
                    journey_tier=verdict.severity.impact.journey_tier,
                    regions_affected=verdict.severity.impact.regions_affected,
                    regions_total=verdict.severity.impact.regions_total,
                    success_rate_pct=verdict.severity.impact.success_rate_pct,
                    customer_impacting=verdict.severity.impact.customer_impacting,
                    affected_regions=list(verdict.severity.impact.affected_regions),
                    affected_journeys=list(verdict.severity.impact.affected_journeys),
                    detail=verdict.severity.impact.detail,
                ),
                evidence=[_evidence_out(ref) for ref in verdict.severity.evidence],
                detail=verdict.severity.detail,
            )
        ),
        storm=StormOut(
            alerts=verdict.storm.alerts,
            suppressed=verdict.storm.suppressed,
            window_s=verdict.storm.window_s,
            storming=verdict.storm.storming,
            clusters=[
                AlertClusterOut(
                    cluster_id=cluster.cluster_id,
                    root_alert_id=cluster.root_alert_id,
                    root_node_id=cluster.root_node_id,
                    alert_ids=list(cluster.alert_ids),
                    node_ids=list(cluster.node_ids),
                    names=list(cluster.names),
                    severity=cluster.severity.value,
                    size=cluster.size,
                    suppressed_count=cluster.suppressed_count,
                    started_at=cluster.started_at,
                    latest_at=cluster.latest_at,
                    detail=cluster.detail,
                )
                for cluster in verdict.storm.clusters
            ],
        ),
    )


def rollups_out(
    app_id: str, window_s: float, interval_s: int, rollups: tuple[MetricRollup, ...]
) -> RollupsOut:
    return RollupsOut(
        app_id=app_id,
        window_s=window_s,
        interval_s=interval_s,
        count=len(rollups),
        rollups=[
            RollupOut(
                signal_key=rollup.signal_key,
                region=rollup.region,
                family=rollup.family.value,
                bucket_start=rollup.bucket_start,
                samples=rollup.samples,
                mean=rollup.mean,
                minimum=rollup.minimum,
                maximum=rollup.maximum,
                last=rollup.last,
                series_count=rollup.series_count,
            )
            for rollup in rollups
        ],
    )


def _window_out(observation) -> BurnWindowOut:
    return BurnWindowOut(
        window=observation.window,
        window_s=observation.window_s,
        multiplier=observation.multiplier,
        samples=observation.samples,
        error_ratio=observation.error_ratio,
        budget_ratio=observation.budget_ratio,
        burn_rate=observation.burn_rate,
        breached=observation.breached,
        region=observation.region,
        detail=observation.detail,
    )


def _evidence_out(ref) -> EvidenceRefOut:
    return EvidenceRefOut(
        kind=ref.kind,
        ref=ref.ref,
        detail=ref.detail,
        family=ref.family,
        region=ref.region,
        customer_facing=ref.customer_facing,
        at=ref.at,
    )
