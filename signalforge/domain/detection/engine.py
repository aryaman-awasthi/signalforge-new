"""The detection evaluation, as one pure function (REQ-040, REQ-041, REQ-042).

`evaluate` takes what was measured and returns what the platform believes. It
does no I/O, so the same inputs always produce the same verdict - which is what
makes a proposal reproducible from the rollups an auditor can still read six
months later, and what lets the whole pipeline be tested without a network.

The stance is the one number an operator reads first:

| Stance | When | What it means to a person |
|---|---|---|
| Steady | nothing is breaching | nobody is being told anything |
| Watch | a breach is being observed, a slow burn is running, or a probe is failing | "AI is checking" - a quiet banner, no page |
| Proposed | corroboration holds **and** either a fast burn crossed or a customer-facing family is anomalous | a proposal sheet with evidence, and a Declare button a human presses |

Watch opens on a *breach*, not on a sustained anomaly: the first evaluation past
the threshold is exactly when a quiet "we are looking at this" is useful, and it
is fifteen seconds before anything could be proposed. Proposed needs
corroboration, so a single noisy family can reach Watch and can never reach a
proposal.

Nothing here declares. The verdict carries a `SeverityProposal`; turning that
into an incident is a human act (REQ-043) and there is no function in this
package that performs it.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from enum import Enum
from typing import Mapping, Optional, Sequence

from signalforge.contracts.canonical import Alert, TopologySnapshot
from signalforge.domain.detection.anomaly import (
    SUSTAINED_EVALUATIONS,
    SignalObservation,
    SignalState,
    SignalStatus,
    advance,
    initial_state,
)
from signalforge.domain.detection.baselines import (
    BASELINE_LAG_BUCKETS,
    DEFAULT_BASELINE_WINDOW_S,
    MIN_ROLLING_SAMPLES,
    Basis,
    Observation,
    RollingBaseline,
    deviation_ratio,
    from_onboarding,
    is_breaching,
    observations,
    rolling_baseline,
    threshold_for,
)
from signalforge.domain.detection.burn import (
    FAST_BURN_WINDOW_S,
    SLOW_BURN_WINDOW_S,
    SloBurn,
    evaluate_slo,
)
from signalforge.domain.detection.corroboration import Corroboration, corroborate
from signalforge.domain.detection.health import HealthScore, score_health
from signalforge.domain.detection.probes import (
    JourneyHealth,
    ProbeRecord,
    journey_health,
)
from signalforge.domain.detection.rollups import (
    NO_REGION,
    ROLLUP_INTERVAL_S,
    MetricRollup,
    group_by_key,
)
from signalforge.domain.detection.severity import (
    SeverityProposal,
    assess_impact,
    propose_severity,
)
from signalforge.domain.detection.storms import (
    STORM_WINDOW_S,
    StormSummary,
    cluster_alerts,
)
from signalforge.domain.onboarding.profile import (
    CUSTOMER_FACING_SIGNALS,
    OnboardingProfile,
    SignalDirection,
    SignalMapping,
)

STALE_AFTER_S = 60
"""No rollup for a minute - twelve missed polls at the five-second cadence - and
the signal is stale rather than healthy. A detector that reads silence as health
is worse than no detector, because it is confidently wrong at exactly the moment
the thing it was watching stopped answering."""

PROBE_WINDOW_S = 300
"""How far back probe results count toward the current verdict. Probes run every
thirty seconds, so five minutes is roughly the last ten runs per region."""


class DetectionStance(str, Enum):
    STEADY = "steady"
    WATCH = "watch"
    PROPOSED = "proposed"

    @property
    def rank(self) -> int:
        return {"steady": 0, "watch": 1, "proposed": 2}[self.value]


@dataclass(frozen=True)
class DetectionInputs:
    tenant_id: str
    app_id: str
    profile: OnboardingProfile
    at: datetime
    rollups: Sequence[MetricRollup] = ()
    previous: Sequence[SignalState] = ()
    probes: Sequence[ProbeRecord] = ()
    alerts: Sequence[Alert] = ()
    topology: Optional[TopologySnapshot] = None
    baseline_window_s: float = DEFAULT_BASELINE_WINDOW_S


@dataclass(frozen=True)
class DetectionVerdict:
    tenant_id: str
    app_id: str
    evaluated_at: datetime
    stance: DetectionStance
    states: tuple[SignalState, ...]
    observations: tuple[SignalObservation, ...]
    health: HealthScore
    burns: tuple[SloBurn, ...]
    journeys: tuple[JourneyHealth, ...]
    corroboration: Corroboration
    severity: Optional[SeverityProposal]
    storm: StormSummary
    reasons: tuple[str, ...] = ()

    @property
    def anomalous(self) -> tuple[SignalState, ...]:
        return tuple(
            state for state in self.states if state.status is SignalStatus.ANOMALOUS
        )

    @property
    def proposing(self) -> bool:
        return self.stance is DetectionStance.PROPOSED


def _frozen_baseline(previous: SignalState) -> Optional[RollingBaseline]:
    """The threshold that was in force when the breach began.

    While a signal is breaching, the baseline is **held** rather than
    recomputed. Without this, a breach that lasts long enough teaches the
    rolling window that it is normal: the mean moves toward the fault, the
    standard deviation widens by exactly the size of the event, and after a
    minute or two the detector reports that everything is fine while the
    application is still on fire. Holding the baseline means an anomaly ends
    when the *signal* recovers, not when the detector gets used to it.
    """
    if previous.status not in (SignalStatus.BREACHING, SignalStatus.ANOMALOUS):
        return None
    if previous.threshold is None or previous.baseline_mean is None:
        return None
    return RollingBaseline(
        signal_key=previous.signal_key,
        region=previous.region,
        basis=previous.basis,
        samples=max(1, previous.samples),
        window_s=0.0,
        mean=previous.baseline_mean,
        stddev=previous.baseline_stddev or 0.0,
        minimum=previous.baseline_mean,
        maximum=previous.baseline_mean,
        p95=previous.baseline_mean,
        source="frozen",
    )


def _observe(
    signal: SignalMapping,
    region: str,
    rollups: Sequence[MetricRollup],
    profile: OnboardingProfile,
    *,
    at: datetime,
    window_s: float,
    previous: Optional[SignalState] = None,
) -> SignalObservation:
    """One (signal, region), from its window of rollups to a comparison."""
    base = SignalObservation(
        signal_key=signal.key,
        region=region,
        family=signal.signal,
        direction=signal.direction,
        customer_facing=signal.signal in CUSTOMER_FACING_SIGNALS,
        basis=Basis.NONE,
    )
    if not rollups:
        return replace(
            base,
            reason=f"no rollup for {signal.key!r} in the last {int(window_s)}s",
        )

    basis, points = observations(rollups)
    if not points:
        return replace(
            base,
            basis=basis,
            reason="a rate needs two consecutive buckets and only one arrived",
        )

    current = points[-1]
    age = (at - current.at).total_seconds()
    if age > STALE_AFTER_S:
        return replace(
            base,
            basis=basis,
            at=current.at,
            reason=(
                f"last measurement is {age:.0f}s old, past the {STALE_AFTER_S}s "
                "staleness limit; silence is not health"
            ),
        )

    # The reference window stops four buckets short of the bucket being judged,
    # so a breach that has been running for three evaluations cannot have taught
    # the baseline to accept it (baselines.BASELINE_LAG_BUCKETS). And once a
    # breach is under way the baseline is held outright, which is what keeps a
    # long incident from being averaged into normality.
    held = _frozen_baseline(previous) if previous is not None else None
    lag = timedelta(seconds=BASELINE_LAG_BUCKETS * ROLLUP_INTERVAL_S)
    cutoff = current.at - lag
    reference = [point for point in points[:-1] if point.at <= cutoff]
    baseline: Optional[RollingBaseline] = held or rolling_baseline(
        signal.key, region, reference, basis=basis, window_s=window_s
    )
    learned = profile.baseline(signal.key)
    if baseline is None and learned is not None and basis is Basis.LEVEL:
        # Fall back to what the wizard learned. Only on the LEVEL basis: the
        # onboarding run sampled an instant repeatedly, so it never measured a
        # rate and comparing a rate against it would compare two different units.
        baseline = from_onboarding(learned, region)
    if baseline is None:
        return replace(
            base,
            basis=basis,
            value=current.value,
            at=current.at,
            reason=(
                f"learning: {len(reference)} of {MIN_ROLLING_SAMPLES} reference "
                "samples, and no onboarding baseline to fall back on"
            ),
        )

    threshold = (
        previous.threshold
        if held is not None and previous is not None and previous.threshold is not None
        else threshold_for(baseline, signal.direction)
    )
    breaching = is_breaching(current.value, threshold, signal.direction)
    side = "above" if signal.direction is SignalDirection.HIGHER_IS_WORSE else "below"
    return replace(
        base,
        basis=basis,
        value=current.value,
        at=current.at,
        baseline=baseline,
        threshold=threshold,
        deviation=deviation_ratio(current.value, baseline, threshold, signal.direction),
        breaching=breaching,
        reason=(
            f"{current.value:.6g} {side} the {threshold:.6g} threshold "
            f"({baseline.source} baseline, mean {baseline.mean:.6g}, "
            f"{baseline.samples} samples)"
            if breaching
            else (
                f"{current.value:.6g} within the {threshold:.6g} threshold "
                f"({baseline.source} baseline, {baseline.samples} samples)"
            )
        ),
    )


def _keys(
    profile: OnboardingProfile,
    rollups: Sequence[MetricRollup],
    previous: Sequence[SignalState],
) -> list[tuple[SignalMapping, str]]:
    """Which (signal, region) pairs this evaluation is about.

    The union of what has data, what the detector already believed, and - for a
    signal with neither - one unlabelled key, so a mapped signal that has never
    reported still appears and still reads UNKNOWN rather than being absent.

    The unlabelled key is a bootstrap state, not a permanent one: a signal that
    has acquired real regions loses it.
    """
    mapped = {signal.key: signal for signal in profile.signals}
    pairs: set[tuple[str, str]] = {
        (rollup.signal_key, rollup.region)
        for rollup in rollups
        if rollup.signal_key in mapped
    }
    pairs |= {
        (state.signal_key, state.region)
        for state in previous
        if state.signal_key in mapped
    }
    # The unlabelled row is what a signal looks like between being mapped and
    # being scraped for the first time, and it is carried forward like any other
    # state. Once the same signal has real regions it must go: a region that
    # reports nothing outranks a region reporting well - rightly, because a
    # silent region is the more important fact - so keeping the bootstrap row
    # made every freshly onboarded application read "no data" on a healthy
    # golden tile for the rest of its life. This drops only the unlabelled row
    # of a signal that now has labelled ones; a signal genuinely reporting
    # without a region keeps it, and so does one that has never reported.
    labelled = {key for key, region in pairs if region != NO_REGION}
    pairs -= {(key, NO_REGION) for key in labelled}
    covered = {key for key, _ in pairs}
    pairs |= {(key, NO_REGION) for key in mapped if key not in covered}
    return [(mapped[key], region) for key, region in sorted(pairs)]


def _points_for(
    grouped: Mapping[tuple[str, str], Sequence[MetricRollup]],
    signal_key: str,
    *,
    since: datetime,
) -> dict[str, tuple[Observation, ...]]:
    """Observations per region for one signal, inside a window."""
    per_region: dict[str, tuple[Observation, ...]] = {}
    for (key, region), rollups in grouped.items():
        if key != signal_key:
            continue
        recent = [rollup for rollup in rollups if rollup.bucket_start >= since]
        if not recent:
            continue
        _basis, points = observations(recent)
        if points:
            per_region[region] = points
    return per_region


def advance_states(
    inputs: DetectionInputs,
) -> tuple[tuple[SignalState, ...], tuple[SignalObservation, ...]]:
    """The half that moves. Every mapped (signal, region) is observed against
    its rolling baseline and its breach streak is advanced.

    Split from `summarize` because a *read* of the detector's current belief
    must not advance anything: an operator refreshing a screen cannot be allowed
    to age a breach into an anomaly.
    """
    profile = inputs.profile
    at = inputs.at
    grouped = group_by_key(inputs.rollups)
    previous_by_key = {state.key: state for state in inputs.previous}

    observed: list[SignalObservation] = []
    states: list[SignalState] = []
    for signal, region in _keys(profile, inputs.rollups, inputs.previous):
        previous = previous_by_key.get((signal.key, region)) or initial_state(
            inputs.tenant_id, inputs.app_id, signal.key, region, signal.signal
        )
        observation = _observe(
            signal,
            region,
            grouped.get((signal.key, region), ()),
            profile,
            at=at,
            window_s=inputs.baseline_window_s,
            previous=previous,
        )
        observed.append(observation)
        states.append(advance(previous, observation, at=at))
    return tuple(states), tuple(observed)


def summarize(
    inputs: DetectionInputs,
    states: Sequence[SignalState],
    observed: Sequence[SignalObservation] = (),
) -> DetectionVerdict:
    """The half that only reads. Burn, corroboration, health, impact, severity,
    storms and the stance, all derived from states that are already settled."""
    profile = inputs.profile
    at = inputs.at
    grouped = group_by_key(inputs.rollups)

    # -- SLO burn ----------------------------------------------------------
    signals_by_key = {signal.key: signal for signal in profile.signals}
    burns: list[SloBurn] = []
    for slo in profile.slos:
        indicator = signals_by_key.get((slo.indicator or "").strip())
        fast = (
            _points_for(
                grouped,
                indicator.key,
                since=at - timedelta(seconds=FAST_BURN_WINDOW_S),
            )
            if indicator
            else {}
        )
        slow = (
            _points_for(
                grouped,
                indicator.key,
                since=at - timedelta(seconds=SLOW_BURN_WINDOW_S),
            )
            if indicator
            else {}
        )
        burns.append(evaluate_slo(slo, indicator, fast, slow))

    # -- probes ------------------------------------------------------------
    probe_window = at - timedelta(seconds=PROBE_WINDOW_S)
    journeys = journey_health(
        [record for record in inputs.probes if record.at >= probe_window],
        tiers={journey.journey_id: journey.tier for journey in profile.journeys},
    )

    # -- corroboration, health, impact -------------------------------------
    corroboration = corroborate(states, burns, journeys, at=at)
    health = score_health(states)
    impact = assess_impact(
        states,
        journeys,
        burns,
        declared_journeys=profile.journeys,
        declared_regions=profile.regions,
        signal_units={
            signal.key: (signal.unit, signal.direction) for signal in profile.signals
        },
    )
    severity = propose_severity(profile.severity_matrix, impact, corroboration, at=at)

    storm = cluster_alerts(
        [
            alert
            for alert in inputs.alerts
            if (at - alert.starts_at).total_seconds() <= STORM_WINDOW_S * 5
        ],
        inputs.topology,
    )

    # -- stance ------------------------------------------------------------
    reasons: list[str] = []
    breaching = [
        state
        for state in states
        if state.status in (SignalStatus.BREACHING, SignalStatus.ANOMALOUS)
    ]
    anomalous_customer = [
        state
        for state in states
        if state.status is SignalStatus.ANOMALOUS
        and state.family in CUSTOMER_FACING_SIGNALS
    ]
    fast_burning = [burn for burn in burns if burn.proposes]
    slow_burning = [burn for burn in burns if burn.opens_watch]
    failing_probes = [row for row in journeys if row.failing]

    triggers: list[str] = []
    for burn in fast_burning:
        triggers.append(f"SLO {burn.slo_id}: {burn.detail}")
    for state in anomalous_customer:
        triggers.append(
            f"{state.family.value} signal {state.signal_key!r} anomalous in "
            f"{state.region or 'the only region'} for "
            f"{state.streak}/{SUSTAINED_EVALUATIONS} evaluations"
        )
    for row in journeys:
        # A journey the platform checked itself and watched fail three times is
        # the same evidence as a sustained metric anomaly, and the same rule
        # applies to it: three, not one.
        if row.failed >= SUSTAINED_EVALUATIONS:
            triggers.append(
                f"journey {row.journey_id or '-'} failed {row.failed} of "
                f"{row.measured} probe run(s) in {row.region or 'the only region'}"
            )

    if triggers and corroboration.corroborated:
        stance = DetectionStance.PROPOSED
        reasons = triggers + [corroboration.reason]
    elif breaching or slow_burning or failing_probes:
        stance = DetectionStance.WATCH
        reasons = (
            [f"{state.signal_key}/{state.region or '-'}: {state.detail}" for state in breaching]
            + [f"SLO {burn.slo_id}: {burn.detail}" for burn in slow_burning]
            + [
                f"journey {row.journey_id or '-'} failing in {row.region or '-'}"
                for row in failing_probes
            ]
        )
        if triggers and not corroboration.corroborated:
            reasons.append(
                "held at Watch rather than proposed: " + corroboration.reason
            )
    else:
        stance = DetectionStance.STEADY
        reasons = [
            f"every measured signal is within its threshold "
            f"({sum(1 for s in states if s.status.measured)} of {len(states)} measured)"
        ]

    return DetectionVerdict(
        tenant_id=inputs.tenant_id,
        app_id=inputs.app_id,
        evaluated_at=at,
        stance=stance,
        states=tuple(states),
        observations=tuple(observed),
        health=health,
        burns=tuple(burns),
        journeys=journeys,
        corroboration=corroboration,
        # A severity rides only on a proposal. Watch means something is being
        # observed and nothing has been decided; attaching a P1 to it put a
        # "Declare P1 as proposed" button on a single failed probe, which is the
        # opposite of what the corroboration rule exists to prevent.
        severity=severity if stance is DetectionStance.PROPOSED else None,
        storm=storm,
        reasons=tuple(reasons),
    )


def evaluate(inputs: DetectionInputs) -> DetectionVerdict:
    """One full evaluation: advance the streaks, then read the consequences."""
    states, observed = advance_states(inputs)
    return summarize(inputs, states, observed)


def evaluation_window_s() -> int:
    """The cadence the whole design is calibrated to. Named so a reader of the
    Watch banner can be told "three evaluations" and know how long that is."""
    return ROLLUP_INTERVAL_S
