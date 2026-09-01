"""The five-second metric poller (REQ-040).

One scrape per application per tick, folded into one rollup per mapped signal
per region. The scrape is a single `query_instant("")` because the frozen
`/observe/metrics` contract is a point-in-time exposition: reading it once and
selecting locally costs one HTTP call instead of one per signal, and every
signal in the result is then measured at the same instant, which is what makes
"errors rose while the business counter fell" a statement about one moment.

Four failures are designed here, and each one lands somewhere a person can act
on (CLAUDE.md rule 6):

| Failure | What happens |
|---|---|
| the scrape times out or the target is down | no rollup is written, the poll is reported degraded once per outage, and the evaluator turns the affected signals STALE - never healthy |
| some selectors match nothing | the signals that *did* match are written and the ones that did not are named, with the selector and what it matched |
| the tick overlaps the previous one | the bucket already exists, so the second write is counted as a duplicate and no baseline moves |
| the adapter raises outside the taxonomy | counted separately, logged, and the sweep continues to the next application |

Nothing here retries. A poll is one attempt and the next one is five seconds
away; a retry inside a tick would only mean the tick after it starts late.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Optional, Sequence

from signalforge.application.adapters.invoker import ConnectorInvoker
from signalforge.application.detection.common import (
    POLL_DEGRADED_TOPIC,
    POLL_RECOVERED_TOPIC,
    publish,
)
from signalforge.application.events.bus import EventBus
from signalforge.application.identity.uow import IdentityUnitOfWork
from signalforge.application.onboarding.common import load_profile
from signalforge.contracts.canonical import MetricSeries
from signalforge.contracts.envelope import AdapterKind
from signalforge.contracts.errors import (
    AdapterAuthError,
    AdapterError,
    AdapterTimeoutError,
)
from signalforge.domain.adapters.bindings import ConnectorBinding
from signalforge.domain.adapters.ports import AdapterRegistry
from signalforge.domain.detection.ports import DetectionUnitOfWork
from signalforge.domain.detection.rollups import MetricRollup, fold_bucket
from signalforge.domain.identity.ports import Clock

logger = logging.getLogger("signalforge.detection")

QUERY_INSTANT_CAPABILITY = "query_instant"
ALL_SERIES_SELECTOR = ""


@dataclass(frozen=True)
class SignalMiss:
    signal_key: str
    reason: str


@dataclass(frozen=True)
class PollReport:
    tenant_id: str
    app_id: str
    at: datetime
    binding_id: Optional[str] = None
    adapter_id: Optional[str] = None
    signals: int = 0
    written: int = 0
    duplicates: int = 0
    missed: tuple[SignalMiss, ...] = ()
    error: Optional[str] = None
    degraded: bool = False
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.error is None

    @property
    def partial(self) -> bool:
        return self.ok and bool(self.missed) and self.written > 0


@dataclass(frozen=True)
class PollSweep:
    applications: int = 0
    polled: int = 0
    written: int = 0
    duplicates: int = 0
    failed: int = 0

    def __str__(self) -> str:
        return (
            f"{self.polled}/{self.applications} application(s) polled, "
            f"{self.written} rollup(s), {self.duplicates} duplicate(s), "
            f"{self.failed} failure(s)"
        )


class MetricRollupPoller:
    def __init__(
        self,
        uow_factory: Callable[[], IdentityUnitOfWork],
        detection_uow_factory: Callable[[], DetectionUnitOfWork],
        registry: AdapterRegistry,
        invoker: ConnectorInvoker,
        bus: EventBus,
        clock: Clock,
        metrics=None,
    ):
        self._uow_factory = uow_factory
        self._detection_uow_factory = detection_uow_factory
        self._registry = registry
        self._invoker = invoker
        self._bus = bus
        self._clock = clock
        self._metrics = metrics
        # Which applications are currently failing to poll. In memory and
        # per-node on purpose: it exists only to keep one outage from writing an
        # event every five seconds. The durable half - "this signal has no fresh
        # measurement" - is the STALE state the evaluator writes to rollups.db.
        self._degraded: set[tuple[str, str]] = set()

    # -- the work list -----------------------------------------------------

    async def metric_bindings(self) -> list[ConnectorBinding]:
        """One metrics binding per (tenant, application), across the install.

        Reads the same cross-tenant work list the connector health loop uses
        (`list_enabled_for_health`), for the same reason and with the same
        discipline: every binding carries its own tenant_id and everything
        written back goes through the tenant-scoped stores.
        """
        async with self._uow_factory() as uow:
            bindings = list(await uow.connectors.list_enabled_for_health())
        chosen: dict[tuple[str, str], ConnectorBinding] = {}
        for binding in bindings:
            manifest = self._registry.find(binding.adapter_id)
            if manifest is None or manifest.kind is not AdapterKind.METRICS:
                continue
            if not manifest.supports(QUERY_INSTANT_CAPABILITY):
                continue
            chosen.setdefault((binding.tenant_id, binding.app_id), binding)
        return [chosen[key] for key in sorted(chosen)]

    async def poll_all(self) -> PollSweep:
        bindings = await self.metric_bindings()
        polled = written = duplicates = failed = 0
        for binding in bindings:
            report = await self.poll(binding.tenant_id, binding.app_id, binding=binding)
            polled += 1
            written += report.written
            duplicates += report.duplicates
            if not report.ok:
                failed += 1
        return PollSweep(
            applications=len(bindings),
            polled=polled,
            written=written,
            duplicates=duplicates,
            failed=failed,
        )

    # -- one application ---------------------------------------------------

    async def poll(
        self,
        tenant_id: str,
        app_id: str,
        *,
        binding: Optional[ConnectorBinding] = None,
    ) -> PollReport:
        at = self._clock.now()
        if binding is None:
            binding = await self._binding_for(tenant_id, app_id)
        if binding is None:
            return PollReport(
                tenant_id=tenant_id,
                app_id=app_id,
                at=at,
                detail=(
                    f"{app_id} has no enabled metrics connector that supports "
                    f"{QUERY_INSTANT_CAPABILITY!r}; nothing to roll up"
                ),
            )

        async with self._uow_factory() as uow:
            profile = await load_profile(uow, tenant_id, app_id)
        if not profile.signals:
            return PollReport(
                tenant_id=tenant_id,
                app_id=app_id,
                at=at,
                binding_id=binding.id,
                adapter_id=binding.adapter_id,
                detail=(
                    f"{app_id} has no mapped golden signals; map at least one in "
                    "the wizard's Map step before detection can watch it"
                ),
            )

        try:
            series: Sequence[MetricSeries] = await self._invoker.invoke(
                tenant_id, binding.id, QUERY_INSTANT_CAPABILITY, ALL_SERIES_SELECTOR, at
            )
        except AdapterTimeoutError as exc:
            return await self._failed(
                tenant_id, app_id, binding, at, f"timed out: {exc}", outcome="timeout"
            )
        except AdapterAuthError as exc:
            return await self._failed(
                tenant_id, app_id, binding, at,
                f"credentials rejected: {exc}", outcome="auth",
            )
        except AdapterError as exc:
            return await self._failed(
                tenant_id, app_id, binding, at,
                f"{type(exc).__name__}: {exc}", outcome="error",
            )
        except Exception as exc:  # an adapter raising outside the taxonomy
            logger.warning(
                "metrics adapter raised outside the error taxonomy",
                extra={"adapter": binding.adapter_id, "app_id": app_id},
            )
            return await self._failed(
                tenant_id, app_id, binding, at,
                f"{type(exc).__name__}: {exc}", outcome="untyped_error",
            )

        rollups: list[MetricRollup] = []
        missed: list[SignalMiss] = []
        for signal in profile.signals:
            folded = fold_bucket(
                series,
                signal,
                tenant_id=tenant_id,
                app_id=app_id,
                region_label=profile.region_label,
                at=at,
            )
            if folded:
                rollups.extend(folded)
                continue
            missed.append(
                SignalMiss(
                    signal_key=signal.key,
                    reason=(
                        f"selector {signal.selector!r} matched no usable sample in "
                        f"a scrape of {len(series)} series"
                    ),
                )
            )

        async with self._detection_uow_factory() as detection:
            written, duplicates = await detection.rollups.append(rollups)
            await detection.commit()

        self._count("ok", written=written, duplicates=duplicates)
        await self._recovered(tenant_id, app_id, binding)
        return PollReport(
            tenant_id=tenant_id,
            app_id=app_id,
            at=at,
            binding_id=binding.id,
            adapter_id=binding.adapter_id,
            signals=len(profile.signals),
            written=written,
            duplicates=duplicates,
            missed=tuple(missed),
            detail=(
                f"{written} rollup(s) from {len(series)} series"
                + (f"; {len(missed)} signal(s) matched nothing" if missed else "")
            ),
        )

    # -- helpers -----------------------------------------------------------

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
                and manifest.kind is AdapterKind.METRICS
                and manifest.supports(QUERY_INSTANT_CAPABILITY)
            ):
                return binding
        return None

    async def _failed(
        self,
        tenant_id: str,
        app_id: str,
        binding: ConnectorBinding,
        at: datetime,
        reason: str,
        *,
        outcome: str,
    ) -> PollReport:
        """A failed poll writes no rollup, and says so once.

        Once, not every five seconds: a target that has been down for an hour
        would otherwise be seven hundred identical events on a timeline a person
        is supposed to read. The durable consequence is not this event - it is
        the STALE state the evaluator writes when the data stops arriving.
        """
        self._count(outcome)
        key = (tenant_id, app_id)
        first = key not in self._degraded
        self._degraded.add(key)
        if first:
            async with self._uow_factory() as uow:
                await publish(
                    uow,
                    self._bus,
                    self._clock,
                    tenant_id=tenant_id,
                    app_id=app_id,
                    topic=POLL_DEGRADED_TOPIC,
                    payload={
                        "app_id": app_id,
                        "binding_id": binding.id,
                        "adapter_id": binding.adapter_id,
                        "outcome": outcome,
                        "reason": reason[:400],
                        "consequence": (
                            "no rollup was written; the signals this connector "
                            "feeds go stale rather than healthy"
                        ),
                    },
                )
                await uow.commit()
        return PollReport(
            tenant_id=tenant_id,
            app_id=app_id,
            at=at,
            binding_id=binding.id,
            adapter_id=binding.adapter_id,
            error=reason[:400],
            degraded=True,
            detail=reason[:400],
        )

    async def _recovered(
        self, tenant_id: str, app_id: str, binding: ConnectorBinding
    ) -> None:
        key = (tenant_id, app_id)
        if key not in self._degraded:
            return
        self._degraded.discard(key)
        async with self._uow_factory() as uow:
            await publish(
                uow,
                self._bus,
                self._clock,
                tenant_id=tenant_id,
                app_id=app_id,
                topic=POLL_RECOVERED_TOPIC,
                payload={
                    "app_id": app_id,
                    "binding_id": binding.id,
                    "adapter_id": binding.adapter_id,
                    "detail": "metrics are arriving again",
                },
            )
            await uow.commit()

    def _count(self, outcome: str, *, written: int = 0, duplicates: int = 0) -> None:
        if self._metrics is None:
            return
        self._metrics.increment(
            "signalforge_detection_polls_total", {"outcome": outcome}
        )
        if written:
            self._metrics.increment(
                "signalforge_detection_rollups_total",
                {"outcome": "written"},
                value=float(written),
            )
        if duplicates:
            self._metrics.increment(
                "signalforge_detection_rollups_total",
                {"outcome": "duplicate"},
                value=float(duplicates),
            )
