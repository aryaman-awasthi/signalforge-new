"""The detection service: evaluate, remember, and announce what changed.

Three entry points, and the difference between them is the whole design.

- `evaluate` is the loop. It advances the breach streaks, writes the states and
  the snapshot, and publishes **transitions** - a stance change, an anomaly
  opening or clearing, a signal going stale, a health band moving, a burn
  verdict changing, a storm forming. A tick where nothing changed writes state
  and publishes nothing, which is why a timeline stays readable.
- `snapshot` is the read. It summarises the states that are already stored and
  advances nothing: refreshing a screen must not be able to age a breach into an
  anomaly, and a GET that mutated the detector would make every proposal
  unreproducible.
- `force` is a person overriding the cadence - "evaluate now, I am looking at
  it". It runs a real evaluation and is audited as a human action, because
  "somebody forced an evaluation at 03:04" is an act.

None of them declares anything. `evaluate` can publish a severity *proposal*;
turning it into an incident is a human act (REQ-043), and there is no code path
from here to one.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable, Optional, Sequence

from signalforge.application.detection.common import (
    ALERT_TOPICS,
    ANOMALY_CLEARED_TOPIC,
    ANOMALY_OPENED_TOPIC,
    BURN_TOPIC,
    EVALUATION_FORCED_TOPIC,
    HEALTH_TOPIC,
    PROPOSAL_TOPIC,
    SIGNAL_STALE_TOPIC,
    STANCE_TOPIC,
    STORM_TOPIC,
    publish,
    record_action,
)
from signalforge.application.events.bus import EventBus
from signalforge.application.identity.principal import Principal
from signalforge.application.identity.uow import IdentityUnitOfWork
from signalforge.application.onboarding.common import load_profile, require_application
from signalforge.contracts.canonical import Alert, TopologySnapshot
from signalforge.domain.detection.anomaly import SignalState, SignalStatus
from signalforge.domain.detection.baselines import DEFAULT_BASELINE_WINDOW_S
from signalforge.domain.detection.burn import BurnVerdict
from signalforge.domain.detection.engine import (
    PROBE_WINDOW_S,
    DetectionInputs,
    DetectionStance,
    DetectionVerdict,
    advance_states,
    summarize,
)
from signalforge.domain.detection.errors import DetectionValidationError
from signalforge.domain.detection.ports import DetectionUnitOfWork
from signalforge.domain.detection.probes import ProbeRecord
from signalforge.domain.detection.rollups import (
    MAX_ROLLUP_WINDOW_S,
    ROLLUP_RETENTION_S,
    MetricRollup,
    window_bounds,
)
from signalforge.domain.detection.snapshot import DetectionSnapshot, storm_fingerprint
from signalforge.domain.identity.ports import Clock
from signalforge.domain.onboarding.errors import ApplicationNotFoundError

logger = logging.getLogger("signalforge.detection")

ALERT_LOOKBACK_EVENTS = 2_000
"""How far back down the log the alert reader walks. Storm clustering only cares
about the last few minutes; this is a bound on the scan, not on the window."""

MAX_ALERTS = 500


@dataclass(frozen=True)
class EvaluationSweep:
    applications: int = 0
    evaluated: int = 0
    watching: int = 0
    proposing: int = 0
    failed: int = 0

    def __str__(self) -> str:
        return (
            f"{self.evaluated}/{self.applications} evaluated, {self.watching} watching, "
            f"{self.proposing} proposing, {self.failed} failure(s)"
        )


class DetectionService:
    def __init__(
        self,
        uow_factory: Callable[[], IdentityUnitOfWork],
        detection_uow_factory: Callable[[], DetectionUnitOfWork],
        bus: EventBus,
        clock: Clock,
        metrics=None,
        baseline_window_s: float = DEFAULT_BASELINE_WINDOW_S,
    ):
        self._uow_factory = uow_factory
        self._detection_uow_factory = detection_uow_factory
        self._bus = bus
        self._clock = clock
        self._metrics = metrics
        self._baseline_window_s = baseline_window_s

    # -- loading -----------------------------------------------------------

    async def _inputs(self, tenant_id: str, app_id: str) -> DetectionInputs:
        at = self._clock.now()
        start, end = window_bounds(at, self._baseline_window_s)
        async with self._uow_factory() as uow:
            await require_application(uow, tenant_id, app_id)
            profile = await load_profile(uow, tenant_id, app_id)
            topology = await self._topology(uow, tenant_id, app_id)
            alerts = await self._alerts(uow, tenant_id, app_id, at)
        async with self._detection_uow_factory() as detection:
            rollups = list(
                await detection.rollups.window(tenant_id, app_id, start, end)
            )
            states = list(await detection.states.list(tenant_id, app_id))
            probes = list(
                await detection.probes.window(
                    tenant_id,
                    app_id,
                    at - timedelta(seconds=PROBE_WINDOW_S),
                    at + timedelta(seconds=1),
                )
            )
        return DetectionInputs(
            tenant_id=tenant_id,
            app_id=app_id,
            profile=profile,
            at=at,
            rollups=rollups,
            previous=states,
            probes=probes,
            alerts=alerts,
            topology=topology,
            baseline_window_s=self._baseline_window_s,
        )

    @staticmethod
    async def _topology(
        uow: IdentityUnitOfWork, tenant_id: str, app_id: str
    ) -> Optional[TopologySnapshot]:
        version = await uow.topologies.latest(tenant_id, app_id)
        return version.snapshot if version is not None else None

    @staticmethod
    async def _alerts(
        uow: IdentityUnitOfWork, tenant_id: str, app_id: str, at: datetime
    ) -> tuple[Alert, ...]:
        """Alerts arrive by push and land on the timeline, so the timeline is
        where the clusterer reads them from - there is no second store of alerts
        to fall out of step with the events an auditor replays."""
        head = await uow.events.max_seq()
        rows = await uow.events.read(
            tenant_id=tenant_id,
            after_seq=max(0, head - ALERT_LOOKBACK_EVENTS),
            topics=ALERT_TOPICS,
            limit=MAX_ALERTS,
        )
        alerts: list[Alert] = []
        for envelope in rows:
            if envelope.app_id not in (None, app_id):
                continue
            try:
                alerts.append(Alert.model_validate(envelope.payload))
            except Exception:
                # An alert payload that will not parse is a producer problem and
                # is already recorded as an event. Clustering skips it rather
                # than failing the whole evaluation over one bad row.
                continue
        return tuple(alerts)

    # -- the three entry points --------------------------------------------

    async def evaluate(self, tenant_id: str, app_id: str) -> DetectionVerdict:
        inputs = await self._inputs(tenant_id, app_id)
        previous_states = {state.key: state for state in inputs.previous}
        states, observed = advance_states(inputs)
        verdict = summarize(inputs, states, observed)

        async with self._detection_uow_factory() as detection:
            previous_snapshot = await detection.snapshots.get(tenant_id, app_id)
            await detection.states.put_many(states)
            await detection.snapshots.put(_snapshot_of(verdict))
            await detection.commit()

        await self._announce(verdict, previous_states, previous_snapshot)
        self._gauge(verdict)
        return verdict

    async def evaluate_all(self) -> EvaluationSweep:
        """One sweep over every application that has a detection profile.

        Failures are per application: an evaluation that raises is logged and
        counted, and the sweep continues - a broken profile on one application
        must not stop the detector watching the other nineteen.
        """
        async with self._uow_factory() as uow:
            targets = [
                (binding.tenant_id, binding.app_id)
                for binding in await uow.connectors.list_enabled_for_health()
            ]
        seen: list[tuple[str, str]] = []
        for target in targets:
            if target not in seen:
                seen.append(target)
        evaluated = watching = proposing = failed = 0
        for tenant_id, app_id in seen:
            try:
                verdict = await self.evaluate(tenant_id, app_id)
            except ApplicationNotFoundError:
                # A binding whose application was removed. Not an error worth
                # counting: the next connector sweep will drop the binding too.
                continue
            except Exception as exc:
                failed += 1
                logger.warning(
                    "detection evaluation failed",
                    extra={"app_id": app_id, "error": f"{type(exc).__name__}: {exc}"},
                )
                continue
            evaluated += 1
            if verdict.stance is DetectionStance.WATCH:
                watching += 1
            if verdict.stance is DetectionStance.PROPOSED:
                proposing += 1
        return EvaluationSweep(
            applications=len(seen),
            evaluated=evaluated,
            watching=watching,
            proposing=proposing,
            failed=failed,
        )

    async def snapshot(self, principal: Principal, app_id: str) -> DetectionVerdict:
        """What the detector currently believes, without changing it."""
        inputs = await self._inputs(principal.tenant_id, app_id)
        return summarize(inputs, inputs.previous)

    async def force(self, principal: Principal, app_id: str) -> DetectionVerdict:
        """A human overriding the five-second cadence. Audited as an act."""
        async with self._uow_factory() as uow:
            await require_application(uow, principal.tenant_id, app_id)
        verdict = await self.evaluate(principal.tenant_id, app_id)
        async with self._uow_factory() as uow:
            await record_action(
                uow,
                self._bus,
                self._clock,
                principal=principal,
                app_id=app_id,
                topic=EVALUATION_FORCED_TOPIC,
                payload={
                    "app_id": app_id,
                    "stance": verdict.stance.value,
                    "health_score": verdict.health.score,
                    "corroborated": verdict.corroboration.corroborated,
                    "severity": (
                        verdict.severity.severity.value if verdict.severity else None
                    ),
                    "note": "a forced evaluation proposes; it never declares",
                },
            )
            await uow.commit()
        return verdict

    # -- rollup reads -------------------------------------------------------

    async def rollups(
        self,
        principal: Principal,
        app_id: str,
        *,
        signal_key: Optional[str] = None,
        region: Optional[str] = None,
        window_s: float = 900.0,
    ) -> tuple[MetricRollup, ...]:
        if not (0 < float(window_s) <= MAX_ROLLUP_WINDOW_S):
            raise DetectionValidationError(
                f"window_s must be between 0 and {MAX_ROLLUP_WINDOW_S} seconds"
            )
        async with self._uow_factory() as uow:
            await require_application(uow, principal.tenant_id, app_id)
        start, end = window_bounds(self._clock.now(), window_s)
        async with self._detection_uow_factory() as detection:
            rows = await detection.rollups.window(
                principal.tenant_id,
                app_id,
                start,
                end,
                signal_key=signal_key,
                region=region,
            )
        return tuple(rows)

    async def latest_probe_results(
        self, tenant_id: str, app_id: str, *, window_s: float = 900.0
    ) -> tuple[ProbeRecord, ...]:
        """The most recent result per probe, newest first (S6-T05).

        Read rather than re-run, deliberately: layered validation must measure
        the same instant the war room is looking at, and a validator that fired
        its own probes would produce a second answer to "is the journey working"
        that could disagree with the screen.

        Tenant-scoped like every other read here, and carrying no permission of
        its own - the caller has already resolved the incident inside the
        tenant.
        """
        start, end = window_bounds(self._clock.now(), window_s)
        async with self._detection_uow_factory() as detection:
            rows = await detection.probes.window(tenant_id, app_id, start, end)
        newest: dict[tuple[str, str], ProbeRecord] = {}
        for row in rows:
            key = (row.probe_id, row.region)
            held = newest.get(key)
            if held is None or row.at >= held.at:
                newest[key] = row
        return tuple(sorted(newest.values(), key=lambda r: (r.probe_id, r.region)))

    async def latest_against_baseline(
        self, tenant_id: str, app_id: str, signal_key: str
    ) -> Optional[tuple[float, float]]:
        """`(current value, baseline mean)` for one mapped signal, or None.

        Read from the durable signal states the detector already writes every
        evaluation, so validation costs one indexed read and cannot disagree with
        what the detection screen is showing. The worst region wins: a signal
        that recovered in one region and not another has not recovered.
        """
        async with self._detection_uow_factory() as detection:
            states = await detection.states.list(tenant_id, app_id)
        readings = [
            state
            for state in states
            if state.signal_key == signal_key
            and state.value is not None
            and state.baseline_mean is not None
        ]
        if not readings:
            return None
        worst = max(readings, key=lambda state: state.deviation)
        return float(worst.value), float(worst.baseline_mean)

    async def prune(self, retention_s: float = ROLLUP_RETENTION_S) -> int:
        """Retention for the derived store. Rollups and probe results age out by
        wall time; the signal states and the snapshot do not, because they are
        one row per signal and one per application rather than a time series."""
        before = self._clock.now() - timedelta(seconds=max(60.0, float(retention_s)))
        async with self._detection_uow_factory() as detection:
            removed = await detection.rollups.prune(before)
            removed += await detection.probes.prune(before)
            await detection.commit()
        return removed

    async def forget(self, tenant_id: str, app_id: str) -> int:
        """Everything the detector holds about one application. Called when the
        application is removed."""
        async with self._detection_uow_factory() as detection:
            removed = await detection.states.delete_for(tenant_id, app_id)
            removed += await detection.snapshots.delete_for(tenant_id, app_id)
            await detection.commit()
        return removed

    # -- announcing ---------------------------------------------------------

    async def _announce(
        self,
        verdict: DetectionVerdict,
        previous_states: dict[tuple[str, str], SignalState],
        previous: Optional[DetectionSnapshot],
    ) -> None:
        current = _snapshot_of(verdict)
        events: list[tuple[str, dict]] = []

        for state in verdict.states:
            before = previous_states.get(state.key)
            was = before.status if before else SignalStatus.UNKNOWN
            if was is state.status:
                continue
            base = {
                "app_id": verdict.app_id,
                "signal_key": state.signal_key,
                "region": state.region,
                "family": state.family.value,
                "status": state.status.value,
                "previous_status": was.value,
                "value": state.value,
                "threshold": state.threshold,
                "baseline_mean": state.baseline_mean,
                "baseline_source": state.baseline_source,
                "basis": state.basis.value,
                "streak": state.streak,
                "detail": state.detail,
            }
            if state.status is SignalStatus.ANOMALOUS:
                events.append((ANOMALY_OPENED_TOPIC, base))
            elif was is SignalStatus.ANOMALOUS and state.status is SignalStatus.HEALTHY:
                events.append((ANOMALY_CLEARED_TOPIC, base))
            elif state.status is SignalStatus.STALE:
                events.append((SIGNAL_STALE_TOPIC, base))

        if previous is None or previous.stance != current.stance:
            events.append(
                (
                    STANCE_TOPIC,
                    {
                        "app_id": verdict.app_id,
                        "stance": current.stance,
                        "previous_stance": previous.stance if previous else None,
                        "reasons": list(verdict.reasons),
                        "health_score": verdict.health.score,
                        "health_band": verdict.health.band.value,
                        "corroborated": verdict.corroboration.corroborated,
                        "corroboration": verdict.corroboration.reason,
                        "families": list(verdict.corroboration.families),
                    },
                )
            )

        if previous is None or previous.health_band != current.health_band:
            events.append(
                (
                    HEALTH_TOPIC,
                    {
                        "app_id": verdict.app_id,
                        "score": verdict.health.score,
                        "band": verdict.health.band.value,
                        "previous_band": previous.health_band if previous else None,
                        "worst_region": verdict.health.worst_region,
                        "covered_weight": verdict.health.covered_weight,
                        "detail": verdict.health.detail,
                    },
                )
            )

        if previous is None or previous.burn_verdict != current.burn_verdict:
            worst = _worst_burn(verdict)
            if worst is not None:
                events.append(
                    (
                        BURN_TOPIC,
                        {
                            "app_id": verdict.app_id,
                            "slo_id": worst.slo_id,
                            "verdict": worst.verdict.value,
                            "previous_verdict": (
                                previous.burn_verdict if previous else None
                            ),
                            "fast_burn": worst.fast.burn_rate,
                            "fast_multiplier": worst.fast.multiplier,
                            "slow_burn": worst.slow.burn_rate,
                            "slow_multiplier": worst.slow.multiplier,
                            "region": worst.fast.region or worst.slow.region,
                            "detail": worst.detail,
                        },
                    )
                )

        if current.suppressed and (
            previous is None or previous.storm_fingerprint != current.storm_fingerprint
        ):
            events.append(
                (
                    STORM_TOPIC,
                    {
                        "app_id": verdict.app_id,
                        "alerts": verdict.storm.alerts,
                        "clusters": len(verdict.storm.clusters),
                        "suppressed": verdict.storm.suppressed,
                        "window_s": verdict.storm.window_s,
                        "roots": [
                            {
                                "cluster_id": cluster.cluster_id,
                                "root_alert_id": cluster.root_alert_id,
                                "root_node_id": cluster.root_node_id,
                                "severity": cluster.severity.value,
                                "size": cluster.size,
                                "suppressed": cluster.suppressed_count,
                                "detail": cluster.detail,
                            }
                            for cluster in verdict.storm.clusters
                        ],
                    },
                )
            )

        proposal = verdict.severity
        if proposal is not None and verdict.stance is DetectionStance.PROPOSED:
            changed = previous is None or (
                previous.stance != current.stance
                or previous.severity != current.severity
            )
            if changed:
                events.append((PROPOSAL_TOPIC, _proposal_payload(verdict)))

        if not events:
            return
        async with self._uow_factory() as uow:
            for topic, payload in events:
                await publish(
                    uow,
                    self._bus,
                    self._clock,
                    tenant_id=verdict.tenant_id,
                    app_id=verdict.app_id,
                    topic=topic,
                    payload=payload,
                )
            await uow.commit()

    def _gauge(self, verdict: DetectionVerdict) -> None:
        if self._metrics is None:
            return
        for status in SignalStatus:
            self._metrics.set_gauge(
                "signalforge_detection_signals",
                sum(1 for state in verdict.states if state.status is status),
                {"status": status.value},
            )
        for stance in DetectionStance:
            self._metrics.set_gauge(
                "signalforge_detection_stance",
                1.0 if verdict.stance is stance else 0.0,
                {"stance": stance.value},
            )


def _worst_burn(verdict: DetectionVerdict):
    burns = [burn for burn in verdict.burns if burn.verdict is not BurnVerdict.NONE]
    if not burns:
        return None
    return max(burns, key=lambda burn: (burn.verdict.rank, burn.slo_id))


def _proposal_payload(verdict: DetectionVerdict) -> dict:
    proposal = verdict.severity
    assert proposal is not None
    return {
        "app_id": verdict.app_id,
        "severity": proposal.severity.value,
        "rule": proposal.rule,
        "corroborated": proposal.corroborated,
        "requires_human": True,
        "declared": False,
        "impact": {
            "journey_tier": proposal.impact.journey_tier,
            "regions_affected": proposal.impact.regions_affected,
            "regions_total": proposal.impact.regions_total,
            "success_rate_pct": proposal.impact.success_rate_pct,
            "customer_impacting": proposal.impact.customer_impacting,
            "affected_regions": list(proposal.impact.affected_regions),
            "affected_journeys": list(proposal.impact.affected_journeys),
        },
        "health_score": verdict.health.score,
        "health_band": verdict.health.band.value,
        "families": list(verdict.corroboration.families),
        "evidence": [
            {
                "kind": ref.kind,
                "ref": ref.ref,
                "detail": ref.detail,
                "family": ref.family,
                "region": ref.region,
                "customer_facing": ref.customer_facing,
                "at": ref.at.isoformat() if ref.at else None,
            }
            for ref in proposal.evidence
        ],
        "reasons": list(verdict.reasons),
    }


def _snapshot_of(verdict: DetectionVerdict) -> DetectionSnapshot:
    worst = _worst_burn(verdict)
    return DetectionSnapshot(
        tenant_id=verdict.tenant_id,
        app_id=verdict.app_id,
        stance=verdict.stance.value,
        evaluated_at=verdict.evaluated_at,
        severity=(
            verdict.severity.severity.value
            if verdict.severity is not None and verdict.stance is DetectionStance.PROPOSED
            else None
        ),
        health_score=verdict.health.score,
        health_band=verdict.health.band.value,
        corroborated=verdict.corroboration.corroborated,
        burn_verdict=worst.verdict.value if worst else BurnVerdict.NONE.value,
        storm_fingerprint=storm_fingerprint(
            [cluster.cluster_id for cluster in verdict.storm.clusters]
        ),
        suppressed=verdict.storm.suppressed,
        anomalous_signals=len(verdict.anomalous),
        detail="; ".join(verdict.reasons)[:600],
    )


def alert_topics() -> Sequence[str]:
    return ALERT_TOPICS
