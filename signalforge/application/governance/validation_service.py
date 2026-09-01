"""Layered validation and the closure gate (REQ-065).

Recovery is a claim about the world, so this service checks the world. Every
reading comes from the application through its own adapters and rollups; nothing
here touches the Incident Brain, and the domain refuses a check whose `source`
names it (`LayerCheck.__post_init__`). Asking the reasoning engine whether the
system is healthy would be asking the platform to mark its own homework - the
brain's beliefs came from the same telemetry.

The arrow between the two points one way and only one way: validation *writes*
its failures into the graph as evidence, so a failed customer check moves the
hypotheses. It never reads from it.

## What each layer reads

| Layer | Source | What it means |
|---|---|---|
| technical | health adapter, the action's declared verification checks | is it running and configured? |
| service | metric rollups: error rate, success rate, latency | is it serving? |
| customer | journey probes, customer-facing signals | are people getting what they came for? |
| business | business-family counters | did the money start moving again? |

## The gate

`customer` blocks closure, and that is the whole reason four layers exist rather
than one number. Technical green and service green while customers still cannot
pay is not "nearly recovered": it is the most expensive failure in incident
response, and it is what happens when a platform closes on infrastructure health.

`assert_closable` is wired into the incident lifecycle as a *guard hook* rather
than by the lifecycle importing this module, so the dependency keeps pointing one
way: a node with validation disabled runs exactly Sprint 4's closure.

## When validation fails

Five things happen, in one commit each and in this order:

1. the cycle is stored with its failing checks and their `result_ref`s;
2. the failure becomes an EVIDENCE node on the incident graph;
3. the incident goes back to `active` if it was `recovering` - the one machine
   transition the lifecycle permits, and the safe direction;
4. the failed-cycle count increases;
5. after N cycles (per-app policy) the incident escalates.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any, Callable, Mapping, Optional, Sequence

from signalforge.application.events.bus import EventBus
from signalforge.application.governance.common import (
    CLOSURE_BLOCKED_TOPIC,
    VALIDATION_COMPLETED_TOPIC,
    VALIDATION_ESCALATED_TOPIC,
    VALIDATION_FAILED_TOPIC,
    VALIDATION_STARTED_TOPIC,
    publish_system,
    record,
)
from signalforge.application.governance.policy_service import PolicyService
from signalforge.application.identity.principal import Principal
from signalforge.application.identity.uow import IdentityUnitOfWork
from signalforge.contracts.canonical import HealthStatus
from signalforge.contracts.envelope import ActorType, AdapterKind, Authority
from signalforge.contracts.errors import AdapterError
from signalforge.domain.brain.nodes import (
    GraphNode,
    NodeType,
    Provenance,
    derived_key,
)
from signalforge.domain.governance.errors import (
    ClosureBlockedError,
    ValidationNotFoundError,
)
from signalforge.domain.governance.validation import (
    LayerCheck,
    LayerResult,
    LayerVerdict,
    RecoveryVerdict,
    StabilityWindow,
    ValidationCycle,
    ValidationLayer,
    assert_closable,
    decide_verdict,
    verdict_of,
)
from signalforge.domain.identity.ports import Clock
from signalforge.domain.incidents.errors import IncidentNotFoundError
from signalforge.domain.detection.probes import ProbeVerdict
from signalforge.domain.incidents.lifecycle import LifecycleState
from signalforge.domain.onboarding.profile import GoldenSignal, OnboardingProfile

logger = logging.getLogger("signalforge.governance")

HEALTH_CAPABILITY = "check"
SOURCE_HEALTH = "observe-health"
SOURCE_ROLLUPS = "detection-rollups"
SOURCE_PROBES = "probe-runner"
SOURCE_ACTIONS = "execution-ledger"

CUSTOMER_SUCCESS_FLOOR_PCT = 99.0
SERVICE_SUCCESS_FLOOR_PCT = 99.0
BUSINESS_DROP_TOLERANCE = 0.5
"""A business counter at least half its baseline is treated as recovering
rather than failing. Business volume moves for reasons that have nothing to do
with an incident - a promotion ending, a batch finishing - and a business gate
tuned tightly would block closure on ordinary Tuesday afternoons."""


@dataclass(frozen=True)
class ValidationView:
    latest: Optional[ValidationCycle]
    history: tuple[ValidationCycle, ...]
    failed_cycles: int
    closure_blocked: bool
    closure_lock_reason: str


class ValidationService:
    def __init__(
        self,
        uow_factory: Callable[[], IdentityUnitOfWork],
        bus: EventBus,
        clock: Clock,
        policies: PolicyService,
        registry,
        invoker,
        detection=None,
        metrics=None,
    ):
        self._uow_factory = uow_factory
        self._bus = bus
        self._clock = clock
        self._policies = policies
        self._registry = registry
        self._invoker = invoker
        # Optional so a node with detection disabled still validates what it can
        # and says UNKNOWN for the rest, rather than refusing to validate at all.
        self._detection = detection
        self._metrics = metrics

    # -- reads ---------------------------------------------------------------

    async def view(self, principal: Principal, incident_id: str) -> ValidationView:
        async with self._uow_factory() as uow:
            await self._require_incident(uow, principal.tenant_id, incident_id)
            latest = await uow.validations.latest(principal.tenant_id, incident_id)
            history = list(
                await uow.validations.list(principal.tenant_id, incident_id, limit=25)
            )
            failed = await uow.validations.failed_cycles(
                principal.tenant_id, incident_id
            )
        return ValidationView(
            latest=latest,
            history=tuple(history),
            failed_cycles=failed,
            closure_blocked=bool(latest.blocking) if latest else False,
            closure_lock_reason=latest.closure_lock_reason() if latest else "",
        )

    async def get(
        self, principal: Principal, incident_id: str, validation_id: str
    ) -> ValidationCycle:
        async with self._uow_factory() as uow:
            cycle = await uow.validations.get(principal.tenant_id, validation_id)
        if cycle is None or cycle.incident_id != incident_id:
            raise ValidationNotFoundError(f"validation {validation_id} not found")
        return cycle

    # -- the closure gate ----------------------------------------------------

    async def assert_closable(self, incident) -> None:
        """The guard the lifecycle calls before it closes anything.

        Signature is `(incident)` because it is registered as a hook on
        `IncidentService`, exactly like `on_closed` - the lifecycle knows there
        is a list of guards and knows nothing about validation.
        """
        async with self._uow_factory() as uow:
            latest = await uow.validations.latest(incident.tenant_id, incident.id)
        if latest is None:
            return
        try:
            assert_closable(latest, now=self._clock.now())
        except ClosureBlockedError as exc:
            async with self._uow_factory() as uow:
                await publish_system(
                    uow,
                    self._bus,
                    self._clock,
                    tenant_id=incident.tenant_id,
                    app_id=incident.app_id,
                    incident_id=incident.id,
                    topic=CLOSURE_BLOCKED_TOPIC,
                    payload={
                        "incident_id": incident.id,
                        "reference": incident.reference,
                        "validation_id": latest.id,
                        "cycle": latest.cycle,
                        "verdict": latest.verdict.value,
                        "blocking_layers": [
                            result.layer.value for result in latest.blocking
                        ],
                        "failing_checks": [
                            {
                                "check_id": check.check_id,
                                "label": check.label,
                                "source": check.source,
                                "result_ref": check.result_ref,
                            }
                            for check in latest.failing_checks
                        ],
                        "reason": str(exc),
                    },
                )
                await uow.commit()
            if self._metrics is not None:
                self._metrics.increment(
                    "signalforge_closure_blocked_total",
                    {
                        "layer": (
                            latest.blocking[0].layer.value
                            if latest.blocking
                            else "unknown"
                        )
                    },
                )
            raise

    # -- running a cycle -----------------------------------------------------

    async def run(
        self, principal: Principal, incident_id: str, *, note: str = ""
    ) -> ValidationCycle:
        """One pass over the four layers, against the application itself."""
        now = self._clock.now()
        async with self._uow_factory() as uow:
            incident = await self._require_incident(
                uow, principal.tenant_id, incident_id
            )
            profile = await uow.onboarding.get(principal.tenant_id, incident.app_id)
            previous = await uow.validations.latest(principal.tenant_id, incident_id)
            executions = list(
                await uow.executions.list(principal.tenant_id, incident_id, limit=50)
            )
        policy = await self._policies.current(principal.tenant_id, incident.app_id)
        cycle_number = (previous.cycle + 1) if previous else 1

        async with self._uow_factory() as uow:
            await record(
                uow,
                self._bus,
                self._clock,
                principal=principal,
                app_id=incident.app_id,
                incident_id=incident_id,
                topic=VALIDATION_STARTED_TOPIC,
                payload={
                    "incident_id": incident_id,
                    "cycle": cycle_number,
                    "layers": [layer.value for layer in ValidationLayer],
                    "independent_of_brain": True,
                },
            )
            await uow.commit()

        layers = (
            await self._technical(principal, incident, executions),
            await self._service(principal, incident, profile),
            await self._customer(principal, incident, profile),
            await self._business(principal, incident, profile),
        )
        window = _window(previous, policy)
        gating_ok = all(
            result.satisfied for result in layers if result.layer.gates_closure
        )
        window = window.observed(
            at=now,
            healthy=gating_ok,
            reason=(
                "; ".join(
                    f"{r.layer.value} {r.verdict.value}"
                    for r in layers
                    if r.layer.gates_closure and not r.satisfied
                )
                or ""
            ),
        )
        cycle = ValidationCycle(
            id=str(uuid.uuid4()),
            tenant_id=principal.tenant_id,
            incident_id=incident_id,
            app_id=incident.app_id,
            cycle=cycle_number,
            started_at=now,
            requested_by=principal.user_id,
            layers=layers,
            window=window,
            note=note,
        )
        cycle = replace(
            cycle, verdict=decide_verdict(cycle, now=now), finished_at=now
        )

        async with self._uow_factory() as uow:
            await uow.validations.add(cycle)
            await record(
                uow,
                self._bus,
                self._clock,
                principal=principal,
                app_id=incident.app_id,
                incident_id=incident_id,
                topic=VALIDATION_COMPLETED_TOPIC,
                payload=cycle.as_dict(now),
            )
            await uow.commit()
        self._count(cycle)

        if cycle.verdict is RecoveryVerdict.NOT_RECOVERED:
            cycle = await self._handle_failure(principal, incident, cycle, policy)
        return cycle

    # -- the failure path ----------------------------------------------------

    async def _handle_failure(
        self, principal: Principal, incident, cycle: ValidationCycle, policy
    ) -> ValidationCycle:
        """Everything that follows NOT RECOVERED, in one place.

        Evidence into the graph, the incident back to Active, the count, and the
        escalation. Each step survives its own failure: an incident that could
        not be moved back to Active must still have its failing validation on the
        record.
        """
        now = self._clock.now()
        evidence_ids: list[str] = []
        async with self._uow_factory() as uow:
            failed_cycles = await uow.validations.failed_cycles(
                principal.tenant_id, incident.id
            )
            node = _evidence_node(cycle, incident, at=now)
            stored, _ = await uow.incident_graph.upsert(node, at=now)
            evidence_ids.append(stored.id)
            returned = False
            if incident.state is LifecycleState.RECOVERING:
                # The one machine transition the lifecycle permits, and the safe
                # direction: escalation, never relaxation.
                moved = incident.moved_to(
                    LifecycleState.ACTIVE,
                    at=now,
                    by=ActorType.SYSTEM,
                    actor_id="validation",
                    reason=(
                        f"validation cycle {cycle.cycle} did not confirm recovery: "
                        + cycle.closure_lock_reason()
                    ),
                )
                await uow.incidents.update(moved)
                returned = True
            updated = replace(
                cycle,
                evidence_node_ids=tuple(evidence_ids),
                returned_to_active=returned,
                escalated=failed_cycles
                >= policy.revalidation_cycles_before_escalation,
            )
            await uow.validations.update(updated)
            await publish_system(
                uow,
                self._bus,
                self._clock,
                tenant_id=principal.tenant_id,
                app_id=incident.app_id,
                incident_id=incident.id,
                topic=VALIDATION_FAILED_TOPIC,
                payload={
                    **updated.as_dict(now),
                    "verdict": RecoveryVerdict.NOT_RECOVERED.value,
                    "failed_cycles": failed_cycles,
                    "returned_to_active": returned,
                    "evidence_node_ids": evidence_ids,
                    "closure_blocked": True,
                },
            )
            if updated.escalated:
                await publish_system(
                    uow,
                    self._bus,
                    self._clock,
                    tenant_id=principal.tenant_id,
                    app_id=incident.app_id,
                    incident_id=incident.id,
                    topic=VALIDATION_ESCALATED_TOPIC,
                    payload={
                        "incident_id": incident.id,
                        "reference": incident.reference,
                        "failed_cycles": failed_cycles,
                        "threshold": policy.revalidation_cycles_before_escalation,
                        "reason": "recovery has been claimed and refused "
                        f"{failed_cycles} times; this needs a different approach "
                        "or more people",
                        "suggested": "consider the declared rollback for the last "
                        "applied action, or hand over to a second responder",
                    },
                )
            await uow.commit()
        if self._metrics is not None and updated.escalated:
            self._metrics.increment("signalforge_validation_escalations_total", {})
        return updated

    # -- the four layers -----------------------------------------------------

    async def _technical(
        self, principal: Principal, incident, executions
    ) -> LayerResult:
        """Is it running, and did the change we made actually apply?

        Two independent sources: the application's health endpoint through its
        own adapter, and the execution ledger's record of what reached the
        target. The ledger is a legitimate technical source - it is a record of
        what the *application* told us about an action, not a belief the platform
        formed.
        """
        checks: list[LayerCheck] = []
        report = await self._health(principal.tenant_id, incident.app_id)
        if report is None:
            checks.append(
                LayerCheck(
                    check_id="health",
                    label="application health endpoint",
                    verdict=LayerVerdict.UNKNOWN,
                    source=SOURCE_HEALTH,
                    detail="no health connector is bound, or it did not answer",
                )
            )
        else:
            healthy = report.status is HealthStatus.UP
            checks.append(
                LayerCheck(
                    check_id="health",
                    label="application health endpoint",
                    verdict=LayerVerdict.PASS if healthy else LayerVerdict.FAIL,
                    source=SOURCE_HEALTH,
                    observed=report.status.value,
                    expected=HealthStatus.UP.value,
                    result_ref=f"health:{incident.app_id}",
                )
            )
            for component in report.components[:10]:
                component_ok = component.status is HealthStatus.UP
                checks.append(
                    LayerCheck(
                        check_id=f"component:{component.name}",
                        label=f"{component.name} dependency",
                        verdict=(
                            LayerVerdict.PASS if component_ok else LayerVerdict.FAIL
                        ),
                        source=SOURCE_HEALTH,
                        observed=component.status.value,
                        expected=HealthStatus.UP.value,
                        result_ref=f"health:{incident.app_id}:{component.name}",
                        detail=component.detail or "",
                    )
                )
        applied = [e for e in executions if e.state.applied]
        for execution in applied[:5]:
            checks.append(
                LayerCheck(
                    check_id=f"applied:{execution.action_id}",
                    label=f"{execution.action_id} applied at the target",
                    verdict=LayerVerdict.PASS,
                    source=SOURCE_ACTIONS,
                    observed=execution.state.value,
                    expected="applied",
                    result_ref=execution.result_ref
                    or f"execution:{execution.id}",
                )
            )
        unresolved = [e for e in executions if e.state.unresolved]
        for execution in unresolved[:5]:
            checks.append(
                LayerCheck(
                    check_id=f"unresolved:{execution.action_id}",
                    label=f"{execution.action_id} outcome is not established",
                    verdict=LayerVerdict.FAIL,
                    source=SOURCE_ACTIONS,
                    observed=execution.state.value,
                    expected="a resolved outcome",
                    result_ref=f"execution:{execution.id}",
                    detail="reconcile this execution before claiming recovery",
                )
            )
        return LayerResult(
            layer=ValidationLayer.TECHNICAL,
            verdict=verdict_of(checks),
            checks=tuple(checks),
            summary=_summary(checks, "instance health and applied changes"),
        )

    async def _service(self, principal: Principal, incident, profile) -> LayerResult:
        """Is it serving? Error rate and success rate from the rollups."""
        checks = await self._signal_checks(
            principal,
            incident,
            profile,
            families=(GoldenSignal.ERRORS, GoldenSignal.AVAILABILITY, GoldenSignal.LATENCY),
            floor_pct=SERVICE_SUCCESS_FLOOR_PCT,
            layer=ValidationLayer.SERVICE,
        )
        return LayerResult(
            layer=ValidationLayer.SERVICE,
            verdict=verdict_of(checks),
            checks=tuple(checks),
            summary=_summary(checks, "error rate, availability and latency"),
        )

    async def _customer(self, principal: Principal, incident, profile) -> LayerResult:
        """Are people getting what they came for?

        Journey probes first, because a probe is the closest thing this platform
        has to a customer. This is the layer that blocks closure, so it is the
        one that must never be satisfied by an absence of evidence: a probe that
        did not run is UNKNOWN, and UNKNOWN does not pass.
        """
        checks: list[LayerCheck] = []
        probes = await self._probe_results(principal.tenant_id, incident.app_id)
        if probes is None:
            checks.append(
                LayerCheck(
                    check_id="journeys",
                    label="customer journey probes",
                    verdict=LayerVerdict.UNKNOWN,
                    source=SOURCE_PROBES,
                    detail="no probe results are available for this application",
                )
            )
        else:
            for probe in probes[:12]:
                # A probe that did not run is UNKNOWN, never a pass. This is the
                # layer that blocks closure, so an absence of evidence must never
                # be able to satisfy it.
                verdict = {
                    ProbeVerdict.PASSED: LayerVerdict.PASS,
                    ProbeVerdict.FAILED: LayerVerdict.FAIL,
                    ProbeVerdict.UNKNOWN: LayerVerdict.UNKNOWN,
                }[probe.verdict]
                region = f" ({probe.region})" if probe.region else ""
                checks.append(
                    LayerCheck(
                        check_id=f"journey:{probe.probe_id}:{probe.region}",
                        label=f"journey {probe.journey_id or probe.probe_id}{region}",
                        verdict=verdict,
                        source=SOURCE_PROBES,
                        observed=(
                            f"{probe.verdict.value}, "
                            f"{probe.status_code or '-'} in "
                            f"{probe.latency_ms:.0f}ms"
                        ),
                        expected="a successful journey",
                        result_ref=f"probe:{probe.probe_id}:{probe.at.isoformat()}",
                        detail=probe.detail or "",
                    )
                )
        checks.extend(
            await self._signal_checks(
                principal,
                incident,
                profile,
                families=(GoldenSignal.AVAILABILITY,),
                floor_pct=CUSTOMER_SUCCESS_FLOOR_PCT,
                layer=ValidationLayer.CUSTOMER,
                customer_facing_only=True,
            )
        )
        return LayerResult(
            layer=ValidationLayer.CUSTOMER,
            verdict=verdict_of(checks),
            checks=tuple(checks),
            summary=_summary(checks, "customer journeys and externally visible behaviour"),
        )

    async def _business(self, principal: Principal, incident, profile) -> LayerResult:
        """Did the money start moving again?

        Advisory: an application that publishes no business counters gets
        NOT_APPLICABLE, which does not gate closure. A gate nobody can satisfy is
        a gate people route around.
        """
        checks: list[LayerCheck] = []
        counters = list(profile.business_signals) if profile is not None else []
        if not counters:
            checks.append(
                LayerCheck(
                    check_id="business",
                    label="business counters",
                    verdict=LayerVerdict.NOT_APPLICABLE,
                    source=SOURCE_ROLLUPS,
                    detail="this application publishes no business-family signals",
                )
            )
        else:
            for signal in counters[:6]:
                reading = await self._reading(
                    principal.tenant_id, incident.app_id, signal.key
                )
                if reading is None:
                    checks.append(
                        LayerCheck(
                            check_id=f"business:{signal.key}",
                            label=signal.description or signal.key,
                            verdict=LayerVerdict.UNKNOWN,
                            source=SOURCE_ROLLUPS,
                            detail="no rollup for this counter in the window",
                        )
                    )
                    continue
                current, baseline = reading
                ok = baseline <= 0 or current >= baseline * BUSINESS_DROP_TOLERANCE
                checks.append(
                    LayerCheck(
                        check_id=f"business:{signal.key}",
                        label=signal.description or signal.key,
                        verdict=LayerVerdict.PASS if ok else LayerVerdict.FAIL,
                        source=SOURCE_ROLLUPS,
                        observed=f"{current:.2f}",
                        expected=f"at least {baseline * BUSINESS_DROP_TOLERANCE:.2f}",
                        result_ref=f"rollup:{signal.key}",
                    )
                )
        return LayerResult(
            layer=ValidationLayer.BUSINESS,
            verdict=verdict_of(checks),
            checks=tuple(checks),
            summary=_summary(checks, "business volume against its baseline"),
        )

    # -- the independent reads -----------------------------------------------

    async def _health(self, tenant_id: str, app_id: str):
        binding = await self._binding(tenant_id, app_id, AdapterKind.HEALTH)
        if binding is None:
            return None
        try:
            return await self._invoker.invoke(
                tenant_id, binding.id, HEALTH_CAPABILITY
            )
        except AdapterError as exc:
            logger.info(
                "the health adapter did not answer during validation",
                extra={"app_id": app_id, "error": str(exc)[:200]},
            )
            return None

    async def _probe_results(self, tenant_id: str, app_id: str):
        """The most recent probe run per journey, from the detection store.

        Read through the detection service rather than re-run here: a validation
        that fired its own probes would measure a different instant from the one
        the war room is watching, and two different answers to "is the journey
        working" is worse than one.
        """
        if self._detection is None:
            return None
        try:
            return await self._detection.latest_probe_results(tenant_id, app_id)
        except Exception:  # pragma: no cover - defensive, detection is optional
            logger.warning("probe results were not available during validation")
            return None

    async def _signal_checks(
        self,
        principal: Principal,
        incident,
        profile: Optional[OnboardingProfile],
        *,
        families: Sequence[GoldenSignal],
        floor_pct: float,
        layer: ValidationLayer,
        customer_facing_only: bool = False,
    ) -> list[LayerCheck]:
        if profile is None:
            return [
                LayerCheck(
                    check_id=f"{layer.value}:signals",
                    label="mapped signals",
                    verdict=LayerVerdict.UNKNOWN,
                    source=SOURCE_ROLLUPS,
                    detail="this application has no onboarding profile",
                )
            ]
        wanted = [
            signal
            for signal in profile.signals
            if signal.signal in families
            and (not customer_facing_only or signal.customer_facing)
        ]
        if not wanted:
            return [
                LayerCheck(
                    check_id=f"{layer.value}:signals",
                    label="mapped signals",
                    verdict=LayerVerdict.NOT_APPLICABLE,
                    source=SOURCE_ROLLUPS,
                    detail=f"no {layer.value}-family signals are mapped",
                )
            ]
        checks: list[LayerCheck] = []
        for signal in wanted[:8]:
            reading = await self._reading(
                principal.tenant_id, incident.app_id, signal.key
            )
            if reading is None:
                checks.append(
                    LayerCheck(
                        check_id=f"{layer.value}:{signal.key}",
                        label=signal.description or signal.key,
                        verdict=LayerVerdict.UNKNOWN,
                        source=SOURCE_ROLLUPS,
                        detail="no rollup for this signal in the window",
                    )
                )
                continue
            current, baseline = reading
            ok = _within(signal, current, baseline, floor_pct)
            checks.append(
                LayerCheck(
                    check_id=f"{layer.value}:{signal.key}",
                    label=signal.description or signal.key,
                    verdict=LayerVerdict.PASS if ok else LayerVerdict.FAIL,
                    source=SOURCE_ROLLUPS,
                    observed=f"{current:.3f}",
                    expected=f"baseline {baseline:.3f}",
                    result_ref=f"rollup:{incident.app_id}:{signal.key}",
                )
            )
        return checks

    async def _reading(
        self, tenant_id: str, app_id: str, signal_key: str
    ) -> Optional[tuple[float, float]]:
        if self._detection is None:
            return None
        try:
            return await self._detection.latest_against_baseline(
                tenant_id, app_id, signal_key
            )
        except Exception:  # pragma: no cover - defensive
            return None

    async def _binding(self, tenant_id: str, app_id: str, kind: AdapterKind):
        async with self._uow_factory() as uow:
            bindings = await uow.connectors.list(
                tenant_id, app_id=app_id, enabled_only=True
            )
        for binding in bindings:
            manifest = self._registry.find(binding.adapter_id)
            if manifest is not None and manifest.kind is kind:
                return binding
        return None

    def _count(self, cycle: ValidationCycle) -> None:
        if self._metrics is None:
            return
        self._metrics.increment(
            "signalforge_validation_cycles_total", {"verdict": cycle.verdict.value}
        )
        for result in cycle.layers:
            self._metrics.set_gauge(
                "signalforge_validation_layer",
                1.0 if result.satisfied else 0.0,
                {"layer": result.layer.value},
            )

    @staticmethod
    async def _require_incident(
        uow: IdentityUnitOfWork, tenant_id: str, incident_id: str
    ):
        incident = await uow.incidents.get(tenant_id, incident_id)
        if incident is None:
            raise IncidentNotFoundError(f"incident {incident_id} not found")
        return incident


# --- pure helpers --------------------------------------------------------------


def _window(previous: Optional[ValidationCycle], policy) -> StabilityWindow:
    """Carry the window forward across cycles, or start one.

    Carried forward so that a run of good samples accumulates across cycles -
    and so that a regression in cycle four resets what cycles one to three
    built, which is the behaviour that makes the window mean anything.
    """
    if previous is not None and previous.window is not None:
        return previous.window
    return StabilityWindow(
        required_s=policy.stability_window_s,
        required_samples=policy.stability_samples,
    )


def _within(signal, current: float, baseline: float, floor_pct: float) -> bool:
    """Whether one reading is acceptable, in the signal's own direction.

    `direction` is why this is not a comparison: `errors_total` rising and
    `success_ratio` falling are the same event, and nothing in the numbers says
    which one this is.
    """
    from signalforge.domain.onboarding.profile import SignalDirection

    if signal.direction is SignalDirection.HIGHER_IS_WORSE:
        # Back to baseline, with a small tolerance for noise.
        return baseline <= 0 or current <= baseline * 1.1
    if baseline > 0:
        return current >= baseline * (floor_pct / 100.0)
    return current > 0


def _summary(checks: Sequence[LayerCheck], what: str) -> str:
    failing = [c for c in checks if c.verdict is LayerVerdict.FAIL]
    if failing:
        return f"{len(failing)} of {len(checks)} checks failing on {what}"
    unknown = [c for c in checks if c.verdict is LayerVerdict.UNKNOWN]
    if unknown:
        return f"{len(unknown)} of {len(checks)} checks could not be read ({what})"
    return f"all {len(checks)} checks passing on {what}"


def _evidence_node(cycle: ValidationCycle, incident, *, at: datetime) -> GraphNode:
    """The failed validation, written back into the reasoning as evidence.

    Authority is OBSERVED, not INFERRED: this is a measurement of the
    application, taken by this platform, and it should weigh what a measurement
    weighs. `result_ref` points at the cycle, so the ledger's drill-down opens
    the checks that failed.
    """
    failing = cycle.failing_checks
    label = (
        f"Validation cycle {cycle.cycle} did not confirm recovery: "
        + (
            ", ".join(check.label for check in failing[:3])
            or cycle.closure_lock_reason()
        )
    )
    return GraphNode(
        id=str(uuid.uuid4()),
        tenant_id=cycle.tenant_id,
        incident_id=cycle.incident_id,
        node_type=NodeType.EVIDENCE,
        derived_key=derived_key(
            NodeType.EVIDENCE, "validation", str(cycle.cycle)
        ),
        label=label,
        created_at=at,
        content="\n".join(
            f"{check.source}: {check.label} - observed {check.observed or '?'}, "
            f"expected {check.expected or '?'}"
            for check in failing[:10]
        ),
        provenance=Provenance(
            source="layered-validation",
            actor_type=ActorType.SYSTEM,
            actor_id="validation",
            authority=Authority.OBSERVED,
            result_ref=f"validation:{cycle.id}",
            detail={
                "cycle": cycle.cycle,
                "blocking_layers": [r.layer.value for r in cycle.blocking],
            },
        ),
        tags=("validation", "not-recovered"),
        detail={"validation_id": cycle.id, "cycle": cycle.cycle},
    )
