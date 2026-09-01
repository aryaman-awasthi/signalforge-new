"""Connection tests, the test page and the readiness score (REQ-036).

A connection test has to be **direction-correct** or it is theatre. The three
adapter modes are three different questions:

| Mode | What "working" means | What the test does |
|---|---|---|
| pull | SignalForge can read the integrated system | pings, then performs one read-only capability and reports what came back |
| invoke | SignalForge can ask it to change something | reads its published manifest and stops - it never executes anything to prove it can |
| push | the integrated system is posting to SignalForge | there is nothing to call: it reports whether a key exists and when something last arrived |

A push binding tested by calling out would always pass, because SignalForge is
the server: it is always reachable, even if the sender died a week ago. That is
why the push row reads PENDING until real traffic arrives rather than a green
tick, and why freshness - not reachability - is its measure.

Every failure mode lands somewhere honest: an unreachable target and a rejected
credential are FAILED with the reason, an adapter with no runtime on this node is
SKIPPED rather than red, and a timed-out read is FAILED naming the timeout rather
than being silently retried. Nothing here retries: a test that retries is
measuring the retry, not the integration.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Callable, Optional, Sequence

from signalforge.application.adapters.invoker import ConnectorInvoker
from signalforge.application.events.bus import EventBus
from signalforge.application.identity.principal import Principal
from signalforge.application.identity.uow import IdentityUnitOfWork
from signalforge.application.onboarding.common import (
    CONNECTION_TEST_TOPIC,
    READINESS_TOPIC,
    load_profile,
    record,
    require_application,
)
from signalforge.application.onboarding.discovery import STREAM_BY_KIND
from signalforge.contracts.canonical import HealthReport, ProbeResult
from signalforge.contracts.envelope import AdapterKind
from signalforge.contracts.errors import (
    AdapterAuthError,
    AdapterError,
    AdapterTimeoutError,
    AdapterUnavailableError,
    NotSupportedError,
)
from signalforge.domain.adapters.bindings import ConnectorBinding
from signalforge.domain.adapters.bindings import direction as binding_direction
from signalforge.domain.adapters.manifest import AdapterManifest, AdapterMode
from signalforge.domain.adapters.ports import AdapterRegistry, AdapterRuntimes
from signalforge.domain.identity.ports import Clock
from signalforge.domain.onboarding.applications import Application
from signalforge.domain.onboarding.profile import (
    CheckOutcome,
    OnboardingProfile,
    TestSummary,
)
from signalforge.domain.onboarding.readiness import Readiness, ReadinessInputs, evaluate

READ_WINDOW_S = 300
PROBE_CAPABILITY = "run_probe"
PING_CAPABILITY = "ping"


@dataclass(frozen=True)
class ConnectionCheck:
    name: str
    outcome: CheckOutcome
    detail: str
    latency_ms: float = 0.0


@dataclass(frozen=True)
class ConnectionTestResult:
    binding_id: str
    adapter_id: str
    adapter_name: str
    kind: Optional[str]
    mode: Optional[str]
    direction: Optional[str]
    credential_scope: str
    simulated: bool
    outcome: CheckOutcome
    detail: str
    checks: tuple[ConnectionCheck, ...]

    @property
    def latency_ms(self) -> float:
        return round(sum(check.latency_ms for check in self.checks), 2)


@dataclass(frozen=True)
class ProbeRun:
    probe_id: str
    journey_id: Optional[str]
    target: str
    outcome: CheckOutcome
    status_code: Optional[int]
    latency_ms: float
    detail: str


@dataclass(frozen=True)
class TestPageReport:
    app_id: str
    connectors: tuple[ConnectionTestResult, ...]
    probes: tuple[ProbeRun, ...]
    summary: TestSummary
    readiness: Readiness


class ConnectionTestService:
    def __init__(
        self,
        uow_factory: Callable[[], IdentityUnitOfWork],
        registry: AdapterRegistry,
        runtimes: AdapterRuntimes,
        invoker: ConnectorInvoker,
        bus: EventBus,
        clock: Clock,
    ):
        self._uow_factory = uow_factory
        self._registry = registry
        self._runtimes = runtimes
        self._invoker = invoker
        self._bus = bus
        self._clock = clock

    async def test(
        self,
        principal: Principal,
        app_id: str,
        *,
        binding_id: Optional[str] = None,
        publish: bool = True,
    ) -> tuple[ConnectionTestResult, ...]:
        async with self._uow_factory() as uow:
            await require_application(uow, principal.tenant_id, app_id)
            bindings = [
                binding
                for binding in await uow.connectors.list(
                    principal.tenant_id, app_id=app_id
                )
                if binding_id is None or binding.id == binding_id
            ]
            keys = list(
                await uow.ingest_keys.list(principal.tenant_id, app_id=app_id)
            )

        results = tuple(
            [await self._test_one(principal, binding, keys) for binding in bindings]
        )
        if results:
            await self._persist(principal, app_id, results, publish=publish)
        return results

    async def _persist(
        self,
        principal: Principal,
        app_id: str,
        results: Sequence[ConnectionTestResult],
        *,
        publish: bool,
    ) -> None:
        """A test is a real observation, so it is written back to the binding.

        This is what closes the gap S3-T01 recorded: a binding created between
        two health polls used to read `unknown` with no detail for up to the
        poll interval. An explicit test answers immediately and the integration
        health screen shows that answer, from the same columns the loop writes.
        """
        now = self._clock.now()
        async with self._uow_factory() as uow:
            for result in results:
                binding = await uow.connectors.get(
                    principal.tenant_id, result.binding_id
                )
                if binding is not None:
                    detail = result.detail[:300]
                    if result.outcome is CheckOutcome.PASSED:
                        observed = binding.observed_ok(now, detail)
                    elif result.outcome is CheckOutcome.FAILED:
                        observed = binding.observed_failure(now, detail)
                    else:
                        observed = binding.observed_unknown(now, detail)
                    await uow.connectors.update(observed)
                if not publish:
                    continue
                await record(
                    uow,
                    self._bus,
                    self._clock,
                    principal=principal,
                    app_id=app_id,
                    topic=CONNECTION_TEST_TOPIC,
                    payload={
                        "app_id": app_id,
                        "binding_id": result.binding_id,
                        "adapter_id": result.adapter_id,
                        "mode": result.mode,
                        "direction": result.direction,
                        "outcome": result.outcome.value,
                        "detail": result.detail,
                        "latency_ms": result.latency_ms,
                        "checks": [
                            {
                                "name": check.name,
                                "outcome": check.outcome.value,
                                "detail": check.detail,
                            }
                            for check in result.checks
                        ],
                    },
                )
            await uow.commit()

    async def _test_one(
        self,
        principal: Principal,
        binding: ConnectorBinding,
        keys: Sequence[Any],
    ) -> ConnectionTestResult:
        manifest = self._registry.find(binding.adapter_id)
        if manifest is None:
            return self._result(
                binding,
                None,
                CheckOutcome.FAILED,
                f"adapter {binding.adapter_id!r} is no longer installed; this "
                "binding is orphaned",
                (),
            )
        if not binding.enabled:
            return self._result(
                binding,
                manifest,
                CheckOutcome.SKIPPED,
                "connector is disabled; enable it to test it",
                (),
            )
        if manifest.mode is AdapterMode.PUSH:
            return self._test_push(binding, manifest, keys)
        if not self._runtimes.has(binding.adapter_id):
            return self._result(
                binding,
                manifest,
                CheckOutcome.SKIPPED,
                f"no runtime for {binding.adapter_id!r} on this node "
                f"(availability: {manifest.availability.value})",
                (),
            )
        return await self._test_outbound(principal, binding, manifest)

    # -- push: nothing to call ---------------------------------------------

    def _test_push(
        self,
        binding: ConnectorBinding,
        manifest: AdapterManifest,
        keys: Sequence[Any],
    ) -> ConnectionTestResult:
        stream = STREAM_BY_KIND.get(manifest.kind)
        live = [
            key
            for key in keys
            if key.active and (stream is None or key.stream is stream)
        ]
        if not live:
            return self._result(
                binding,
                manifest,
                CheckOutcome.PENDING,
                "no ingest key has been issued for this stream; issue one and "
                "configure the sender to post to it",
                (
                    ConnectionCheck(
                        name="ingest key",
                        outcome=CheckOutcome.PENDING,
                        detail="none issued",
                    ),
                ),
            )
        now = self._clock.now()
        freshest = min(
            (key.freshness_s(now) for key in live if key.last_event_at is not None),
            default=None,
        )
        received = sum(key.accepted_count for key in live)
        if freshest is None:
            return self._result(
                binding,
                manifest,
                CheckOutcome.PENDING,
                "key issued; nothing has been posted yet. SignalForge is the "
                "server here, so this reads pending until real traffic arrives",
                (
                    ConnectionCheck(
                        name="ingest key",
                        outcome=CheckOutcome.PASSED,
                        detail=f"{len(live)} active key(s)",
                    ),
                    ConnectionCheck(
                        name="freshness",
                        outcome=CheckOutcome.PENDING,
                        detail="no events received",
                    ),
                ),
            )
        return self._result(
            binding,
            manifest,
            CheckOutcome.PASSED,
            f"{received} item(s) accepted; last one {freshest:.0f}s ago",
            (
                ConnectionCheck(
                    name="ingest key",
                    outcome=CheckOutcome.PASSED,
                    detail=f"{len(live)} active key(s)",
                ),
                ConnectionCheck(
                    name="freshness",
                    outcome=CheckOutcome.PASSED,
                    detail=f"last event {freshest:.0f}s ago",
                ),
            ),
        )

    # -- pull and invoke: call it -------------------------------------------

    async def _test_outbound(
        self,
        principal: Principal,
        binding: ConnectorBinding,
        manifest: AdapterManifest,
    ) -> ConnectionTestResult:
        checks: list[ConnectionCheck] = []
        reachable, detail = await self._run(
            principal, binding, "reachable", PING_CAPABILITY, checks
        )
        if reachable is None:
            return self._result(
                binding, manifest, CheckOutcome.FAILED, detail, tuple(checks)
            )
        if reachable is False:
            checks[-1] = ConnectionCheck(
                name="reachable",
                outcome=CheckOutcome.FAILED,
                detail="the adapter reached it and it reported not ready",
                latency_ms=checks[-1].latency_ms,
            )
            return self._result(
                binding,
                manifest,
                CheckOutcome.FAILED,
                "reached, but the integrated system reports it is not ready",
                tuple(checks),
            )

        capability, args, describe = self._read_capability(manifest)
        if capability is None:
            return self._result(
                binding,
                manifest,
                CheckOutcome.PASSED,
                "reachable; this adapter has no read-only capability to "
                "demonstrate without changing something",
                tuple(checks),
            )
        value, detail = await self._run(
            principal, binding, capability, capability, checks, *args
        )
        if value is None and checks[-1].outcome is CheckOutcome.FAILED:
            return self._result(
                binding, manifest, CheckOutcome.FAILED, detail, tuple(checks)
            )
        if checks[-1].outcome is CheckOutcome.SKIPPED:
            return self._result(
                binding,
                manifest,
                CheckOutcome.PASSED,
                f"reachable; {checks[-1].detail}",
                tuple(checks),
            )
        summary = describe(value)
        checks[-1] = ConnectionCheck(
            name=capability,
            outcome=CheckOutcome.PASSED,
            detail=summary,
            latency_ms=checks[-1].latency_ms,
        )
        return self._result(
            binding, manifest, CheckOutcome.PASSED, summary, tuple(checks)
        )

    async def _run(
        self,
        principal: Principal,
        binding: ConnectorBinding,
        name: str,
        capability: str,
        checks: list[ConnectionCheck],
        *args: Any,
    ) -> tuple[Any, str]:
        started = time.perf_counter()
        try:
            value = await self._invoker.invoke(
                principal.tenant_id, binding.id, capability, *args
            )
        except NotSupportedError as exc:
            checks.append(
                ConnectionCheck(
                    name=name,
                    outcome=CheckOutcome.SKIPPED,
                    detail=str(exc)[:200],
                    latency_ms=_ms(started),
                )
            )
            return None, str(exc)[:200]
        except AdapterAuthError as exc:
            detail = f"credentials rejected: {exc}"[:300]
            checks.append(
                ConnectionCheck(
                    name=name,
                    outcome=CheckOutcome.FAILED,
                    detail=detail,
                    latency_ms=_ms(started),
                )
            )
            return None, detail
        except AdapterTimeoutError as exc:
            detail = f"timed out: {exc}"[:300]
            checks.append(
                ConnectionCheck(
                    name=name,
                    outcome=CheckOutcome.FAILED,
                    detail=detail,
                    latency_ms=_ms(started),
                )
            )
            return None, detail
        except AdapterUnavailableError as exc:
            detail = f"unreachable: {exc}"[:300]
            checks.append(
                ConnectionCheck(
                    name=name,
                    outcome=CheckOutcome.FAILED,
                    detail=detail,
                    latency_ms=_ms(started),
                )
            )
            return None, detail
        except AdapterError as exc:
            detail = f"{type(exc).__name__}: {exc}"[:300]
            checks.append(
                ConnectionCheck(
                    name=name,
                    outcome=CheckOutcome.FAILED,
                    detail=detail,
                    latency_ms=_ms(started),
                )
            )
            return None, detail
        checks.append(
            ConnectionCheck(
                name=name,
                outcome=CheckOutcome.PASSED,
                detail="ok",
                latency_ms=_ms(started),
            )
        )
        return value, "ok"

    def _read_capability(
        self, manifest: AdapterManifest
    ) -> tuple[Optional[str], tuple[Any, ...], Callable[[Any], str]]:
        """One read-only capability per kind, chosen from the frozen SPI.

        Never a mutating one: an invoke-mode adapter is proven by reading the
        manifest it publishes, because executing an action to prove an action
        can be executed is a change nobody asked for. The credential-scope floor
        would refuse it anyway; this is the layer that never asks.
        """
        now = self._clock.now()
        start = now - timedelta(seconds=READ_WINDOW_S)
        table: dict[AdapterKind, tuple[str, tuple[Any, ...], Callable[[Any], str]]] = {
            AdapterKind.METRICS: (
                "query_instant",
                ("", now),
                lambda value: f"read {len(value or [])} metric series",
            ),
            AdapterKind.LOGS: (
                "search",
                ("", start, now, 1),
                lambda value: f"read {len(value or [])} log record(s)",
            ),
            AdapterKind.TRACES: (
                "search",
                (start, now, 1),
                lambda value: f"read {len(value or [])} span(s)",
            ),
            AdapterKind.HEALTH: (
                "get_health",
                (),
                _describe_health,
            ),
            AdapterKind.TOPOLOGY: (
                "fetch",
                (),
                lambda value: (
                    f"fetched topology {value.version} with {len(value.nodes)} "
                    f"node(s) and {len(value.edges)} edge(s)"
                ),
            ),
            AdapterKind.ACTIONS: (
                "manifest",
                (),
                lambda value: f"published {len(value or [])} action(s)",
            ),
            AdapterKind.CHAOS: (
                "manifest",
                (),
                lambda value: f"published {len(value or [])} chaos scenario(s)",
            ),
            AdapterKind.KNOWLEDGE: (
                "search",
                ("signalforge onboarding connection test", 1),
                lambda value: f"searchable, {len(value or [])} hit(s)",
            ),
        }
        entry = table.get(manifest.kind)
        if entry is None or not manifest.supports(entry[0]):
            return None, (), lambda value: "reachable"
        return entry

    @staticmethod
    def _result(
        binding: ConnectorBinding,
        manifest: Optional[AdapterManifest],
        outcome: CheckOutcome,
        detail: str,
        checks: tuple[ConnectionCheck, ...],
    ) -> ConnectionTestResult:
        return ConnectionTestResult(
            binding_id=binding.id,
            adapter_id=binding.adapter_id,
            adapter_name=manifest.name if manifest else binding.adapter_id,
            kind=manifest.kind.value if manifest else None,
            mode=manifest.mode.value if manifest else None,
            direction=binding_direction(manifest) if manifest else None,
            credential_scope=binding.credential_scope.value,
            simulated=bool(manifest and manifest.simulated),
            outcome=outcome,
            detail=detail,
            checks=checks,
        )


class ReadinessService:
    """The readiness score and the wizard's test page.

    The two belong together: the test page is what turns the last two readiness
    checks green, and it is the only step that exercises the whole chain -
    connectors, journey probes, topology and baselines - in one action.
    """

    def __init__(
        self,
        uow_factory: Callable[[], IdentityUnitOfWork],
        registry: AdapterRegistry,
        invoker: ConnectorInvoker,
        connection_tests: ConnectionTestService,
        bus: EventBus,
        clock: Clock,
    ):
        self._uow_factory = uow_factory
        self._registry = registry
        self._invoker = invoker
        self._tests = connection_tests
        self._bus = bus
        self._clock = clock

    async def readiness(
        self, principal: Principal, app_id: str
    ) -> tuple[Application, OnboardingProfile, Readiness]:
        async with self._uow_factory() as uow:
            application = await require_application(uow, principal.tenant_id, app_id)
            profile = await load_profile(uow, principal.tenant_id, app_id)
            bindings = await uow.connectors.list(principal.tenant_id, app_id=app_id)
            keys = [
                key
                for key in await uow.ingest_keys.list(principal.tenant_id, app_id=app_id)
                if key.active
            ]
        return application, profile, evaluate(
            self._inputs(application, profile, bindings, len(keys))
        )

    @staticmethod
    def _inputs(
        application: Application,
        profile: OnboardingProfile,
        bindings: Sequence[ConnectorBinding],
        ingest_keys: int,
    ) -> ReadinessInputs:
        enabled = [binding for binding in bindings if binding.enabled]
        return ReadinessInputs(
            base_url_set=bool(application.base_url),
            connectors_enabled=len(enabled),
            connectors_healthy=sum(
                1 for binding in enabled if binding.status.value == "up"
            ),
            connectors_failing=sum(
                1 for binding in enabled if binding.status.value in ("down", "degraded")
            ),
            ingest_keys=ingest_keys,
            signals=len(profile.signals),
            business_signals=len(profile.business_signals),
            can_corroborate=profile.can_corroborate,
            region_label_set=bool(profile.region_label),
            journeys=len(profile.journeys),
            probes=len(profile.probes),
            actions_imported=len(profile.actions),
            topology_version=profile.topology.version if profile.topology else None,
            slos=len(profile.slos),
            severity_configured=profile.severity_matrix.configured,
            baselines=len(profile.baselines),
            last_test_all_green=bool(
                profile.last_test and profile.last_test.all_green
            ),
        )

    async def run_test_page(
        self, principal: Principal, app_id: str
    ) -> TestPageReport:
        connectors = await self._tests.test(principal, app_id, publish=False)
        probes = await self._run_probes(principal, app_id)

        outcomes = [result.outcome for result in connectors] + [
            probe.outcome for probe in probes
        ]
        now = self._clock.now()
        summary = TestSummary(
            ran_at=now,
            passed=sum(1 for o in outcomes if o is CheckOutcome.PASSED),
            failed=sum(1 for o in outcomes if o is CheckOutcome.FAILED),
            pending=sum(1 for o in outcomes if o is CheckOutcome.PENDING),
            skipped=sum(1 for o in outcomes if o is CheckOutcome.SKIPPED),
        )

        async with self._uow_factory() as uow:
            await require_application(uow, principal.tenant_id, app_id)
            profile = await load_profile(uow, principal.tenant_id, app_id)
            updated = profile.with_test(summary, at=now)
            await uow.onboarding.put(updated)
            await record(
                uow,
                self._bus,
                self._clock,
                principal=principal,
                app_id=app_id,
                topic=READINESS_TOPIC,
                payload={
                    "app_id": app_id,
                    "passed": summary.passed,
                    "failed": summary.failed,
                    "pending": summary.pending,
                    "skipped": summary.skipped,
                    "connectors": [
                        {
                            "binding_id": result.binding_id,
                            "adapter_id": result.adapter_id,
                            "outcome": result.outcome.value,
                        }
                        for result in connectors
                    ],
                    "probes": [
                        {"probe_id": probe.probe_id, "outcome": probe.outcome.value}
                        for probe in probes
                    ],
                },
            )
            await uow.commit()

        application, profile, readiness = await self.readiness(principal, app_id)
        return TestPageReport(
            app_id=app_id,
            connectors=connectors,
            probes=probes,
            summary=summary,
            readiness=readiness,
        )

    async def _run_probes(
        self, principal: Principal, app_id: str
    ) -> tuple[ProbeRun, ...]:
        async with self._uow_factory() as uow:
            application = await require_application(uow, principal.tenant_id, app_id)
            profile = await load_profile(uow, principal.tenant_id, app_id)
            binding = None
            for candidate in await uow.connectors.list(
                principal.tenant_id, app_id=app_id, enabled_only=True
            ):
                manifest = self._registry.find(candidate.adapter_id)
                if (
                    manifest is not None
                    and manifest.kind is AdapterKind.HEALTH
                    and manifest.supports(PROBE_CAPABILITY)
                ):
                    binding = candidate
                    break

        specs = profile.probe_specs(application.base_url)
        if not specs:
            return ()
        if binding is None:
            return tuple(
                ProbeRun(
                    probe_id=spec.probe_id,
                    journey_id=spec.journey,
                    target=spec.target,
                    outcome=CheckOutcome.SKIPPED,
                    status_code=None,
                    latency_ms=0.0,
                    detail=(
                        "no enabled health connector runs synthetic probes; "
                        "connect one in the Connect step"
                    ),
                )
                for spec in specs
            )

        runs: list[ProbeRun] = []
        for spec in specs:
            try:
                result: ProbeResult = await self._invoker.invoke(
                    principal.tenant_id, binding.id, PROBE_CAPABILITY, spec
                )
            except AdapterError as exc:
                runs.append(
                    ProbeRun(
                        probe_id=spec.probe_id,
                        journey_id=spec.journey,
                        target=spec.target,
                        outcome=CheckOutcome.FAILED,
                        status_code=None,
                        latency_ms=0.0,
                        detail=f"{type(exc).__name__}: {exc}"[:300],
                    )
                )
                continue
            runs.append(
                ProbeRun(
                    probe_id=result.probe_id,
                    journey_id=spec.journey,
                    target=result.target,
                    outcome=(
                        CheckOutcome.PASSED if result.ok else CheckOutcome.FAILED
                    ),
                    status_code=result.status_code,
                    latency_ms=round(result.latency_ms, 2),
                    detail=result.detail or "ok",
                )
            )
        return tuple(runs)


def _ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000.0, 2)


def _describe_health(report: HealthReport) -> str:
    components = len(report.components)
    down = [
        component.component
        for component in report.components
        if component.status.value in ("down", "degraded")
    ]
    detail = f"health is {report.status.value} across {components} component(s)"
    return detail if not down else f"{detail}; {', '.join(down[:3])} not healthy"
