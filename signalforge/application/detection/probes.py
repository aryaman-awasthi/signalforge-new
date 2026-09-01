"""The synthetic probe runner (REQ-040).

Every declared probe template, in every declared region, through the
application's own health connector. The probe carries the synthetic header the
onboarding profile names, so the application can exclude the platform's own
traffic from the business counters the platform is watching - otherwise the
health check inflates the KPI it exists to measure.

The verdict is where the care is. Three values, and the third one is the point:

| What happened | Verdict |
|---|---|
| the probe ran and the target answered as expected | PASSED |
| the probe ran and the target did not | FAILED |
| the probe could not be run at all | UNKNOWN |

UNKNOWN covers the health connector being unreachable, the adapter timing out,
no runtime on this node, and a relative probe path with no base URL to resolve
it against. None of those is evidence that a journey is broken, and none of them
counts toward a success rate or a severity proposal. A platform that reported
"checkout is failing in both regions" because its own connector was down would
be worse than one that reported nothing.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable, Optional, Sequence

from signalforge.application.adapters.invoker import ConnectorInvoker
from signalforge.application.identity.uow import IdentityUnitOfWork
from signalforge.application.onboarding.common import load_profile
from signalforge.contracts.canonical import ProbeResult
from signalforge.contracts.envelope import AdapterKind
from signalforge.contracts.errors import AdapterError
from signalforge.domain.adapters.bindings import ConnectorBinding
from signalforge.domain.adapters.ports import AdapterRegistry
from signalforge.domain.detection.ports import DetectionUnitOfWork
from signalforge.domain.detection.probes import ProbeRecord, ProbeVerdict
from signalforge.domain.detection.rollups import NO_REGION
from signalforge.domain.identity.ports import Clock
from signalforge.domain.onboarding.errors import OnboardingValidationError
from signalforge.domain.shared.errors import DomainValidationError, NotFoundError

logger = logging.getLogger("signalforge.detection")

PROBE_CAPABILITY = "run_probe"


@dataclass(frozen=True)
class ProbeSweep:
    applications: int = 0
    runs: int = 0
    passed: int = 0
    failed: int = 0
    unknown: int = 0

    def __str__(self) -> str:
        return (
            f"{self.runs} probe run(s) across {self.applications} application(s): "
            f"{self.passed} passed, {self.failed} failed, {self.unknown} unknown"
        )


class ProbeRunner:
    def __init__(
        self,
        uow_factory: Callable[[], IdentityUnitOfWork],
        detection_uow_factory: Callable[[], DetectionUnitOfWork],
        registry: AdapterRegistry,
        invoker: ConnectorInvoker,
        clock: Clock,
        metrics=None,
    ):
        self._uow_factory = uow_factory
        self._detection_uow_factory = detection_uow_factory
        self._registry = registry
        self._invoker = invoker
        self._clock = clock
        self._metrics = metrics

    async def health_bindings(self) -> list[ConnectorBinding]:
        async with self._uow_factory() as uow:
            bindings = list(await uow.connectors.list_enabled_for_health())
        chosen: dict[tuple[str, str], ConnectorBinding] = {}
        for binding in bindings:
            manifest = self._registry.find(binding.adapter_id)
            if manifest is None or manifest.kind is not AdapterKind.HEALTH:
                continue
            if not manifest.supports(PROBE_CAPABILITY):
                continue
            chosen.setdefault((binding.tenant_id, binding.app_id), binding)
        return [chosen[key] for key in sorted(chosen)]

    async def run_all(self) -> ProbeSweep:
        bindings = await self.health_bindings()
        runs: list[ProbeRecord] = []
        for binding in bindings:
            runs.extend(
                await self.run(binding.tenant_id, binding.app_id, binding=binding)
            )
        return ProbeSweep(
            applications=len(bindings),
            runs=len(runs),
            passed=sum(1 for r in runs if r.verdict is ProbeVerdict.PASSED),
            failed=sum(1 for r in runs if r.verdict is ProbeVerdict.FAILED),
            unknown=sum(1 for r in runs if r.verdict is ProbeVerdict.UNKNOWN),
        )

    async def run(
        self,
        tenant_id: str,
        app_id: str,
        *,
        binding: Optional[ConnectorBinding] = None,
    ) -> tuple[ProbeRecord, ...]:
        async with self._uow_factory() as uow:
            application = await uow.applications.get(tenant_id, app_id)
            profile = await load_profile(uow, tenant_id, app_id)
        if application is None or not profile.probes:
            return ()
        if binding is None:
            binding = await self._binding_for(tenant_id, app_id)

        records: list[ProbeRecord] = []
        for template in profile.probes:
            regions: Sequence[Optional[str]] = (
                profile.regions
                if (template.per_region and profile.regions)
                else (None,)
            )
            for region in regions:
                records.append(
                    await self._run_one(
                        tenant_id, app_id, application.base_url, template, region, binding
                    )
                )

        if records:
            async with self._detection_uow_factory() as detection:
                await detection.probes.append(records)
                await detection.commit()
            self._count(records)
        return tuple(records)

    async def _run_one(
        self,
        tenant_id: str,
        app_id: str,
        base_url: Optional[str],
        template,
        region: Optional[str],
        binding: Optional[ConnectorBinding],
    ) -> ProbeRecord:
        at = self._clock.now()
        common = {
            "tenant_id": tenant_id,
            "app_id": app_id,
            "probe_id": template.probe_id,
            "journey_id": template.journey_id,
            "region": region or NO_REGION,
            "at": at,
        }
        if binding is None:
            return ProbeRecord(
                verdict=ProbeVerdict.UNKNOWN,
                detail=(
                    "no enabled health connector runs synthetic probes; nothing "
                    "checked this journey"
                ),
                **common,
            )
        try:
            spec = template.to_probe_spec(base_url, region)
        except OnboardingValidationError as exc:
            return ProbeRecord(
                verdict=ProbeVerdict.UNKNOWN, detail=str(exc)[:300], **common
            )
        try:
            result: ProbeResult = await self._invoker.invoke(
                tenant_id, binding.id, PROBE_CAPABILITY, spec
            )
        except (AdapterError, DomainValidationError, NotFoundError) as exc:
            # The platform could not run the probe: the connector was disabled
            # between the sweep and the call, the adapter was uninstalled under
            # a live binding, the credential scope refused it. None of those is
            # evidence about the journey, so none of them is a failure.
            return ProbeRecord(
                verdict=ProbeVerdict.UNKNOWN,
                detail=f"{type(exc).__name__}: {exc}"[:300],
                **common,
            )
        except Exception as exc:  # pragma: no cover - an adapter outside the taxonomy
            logger.warning(
                "probe adapter raised outside the error taxonomy",
                extra={"adapter": binding.adapter_id, "app_id": app_id},
            )
            return ProbeRecord(
                verdict=ProbeVerdict.UNKNOWN,
                detail=f"{type(exc).__name__}: {exc}"[:300],
                **common,
            )
        return ProbeRecord(
            verdict=ProbeVerdict.PASSED if result.ok else ProbeVerdict.FAILED,
            status_code=result.status_code,
            latency_ms=result.latency_ms,
            detail=(result.detail or ("ok" if result.ok else "probe did not pass"))[:300],
            **common,
        )

    async def _binding_for(
        self, tenant_id: str, app_id: str
    ) -> Optional[ConnectorBinding]:
        async with self._uow_factory() as uow:
            bindings = await uow.connectors.list(
                tenant_id, app_id=app_id, enabled_only=True
            )
        for binding in bindings:
            manifest = self._registry.find(binding.adapter_id)
            if (
                manifest is not None
                and manifest.kind is AdapterKind.HEALTH
                and manifest.supports(PROBE_CAPABILITY)
            ):
                return binding
        return None

    def _count(self, records: Sequence[ProbeRecord]) -> None:
        if self._metrics is None:
            return
        for record in records:
            self._metrics.increment(
                "signalforge_detection_probes_total", {"verdict": record.verdict.value}
            )
