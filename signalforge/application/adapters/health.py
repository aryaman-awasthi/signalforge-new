"""The connector health loop (REQ-030, REQ-025).

Every enabled binding becomes a HealthSource on every poll, so a connector's
health is produced by the same loop, published on the same topic and rendered by
the same screen as the platform's own database and bus. That is the whole point
of the HealthSource port: the integration health screen does not need to know
whether it is looking at SQLite or a vendor API.

The verdicts are deliberately not all the same shade of red, because the
operator's next action differs:

| Observation | Verdict | Why |
|---|---|---|
| ping succeeded | UP | measured |
| ping returned false | DEGRADED, then DOWN on the third | the adapter reached it and it said no |
| timeout | DEGRADED, then DOWN on the third | uncertain: the call may still be running |
| credentials rejected | DOWN at once | it will not heal by waiting |
| credential missing from the store | DOWN at once | the binding is broken, not the target |
| adapter folder gone, binding remains | DOWN at once | orphaned binding, needs a human |
| no runtime installed on this node | UNKNOWN | nothing was measured; a green tick here would be a lie |

Nothing here decides policy and nothing retries: a poll is one attempt, and the
next poll is thirty seconds away.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Callable, Optional, Sequence

from signalforge.application.adapters.connector_service import ConnectorService
from signalforge.application.identity.uow import IdentityUnitOfWork
from signalforge.contracts.canonical import ComponentHealth, HealthStatus
from signalforge.contracts.errors import (
    AdapterAuthError,
    AdapterError,
    AdapterTimeoutError,
)
from signalforge.domain.adapters.bindings import ConnectorBinding
from signalforge.domain.adapters.manifest import AdapterManifest
from signalforge.domain.adapters.ports import (
    AdapterContext,
    AdapterRegistry,
    AdapterRuntimes,
)
from signalforge.domain.identity.ports import Clock
from signalforge.domain.observability.health import HealthSource

logger = logging.getLogger("signalforge.adapters")


class ConnectorBindingHealthSource(HealthSource):
    def __init__(
        self,
        binding: ConnectorBinding,
        manifest: Optional[AdapterManifest],
        *,
        runtimes: AdapterRuntimes,
        connectors: ConnectorService,
        uow_factory: Callable[[], IdentityUnitOfWork],
        clock: Clock,
    ):
        self._binding = binding
        self._manifest = manifest
        self._runtimes = runtimes
        self._connectors = connectors
        self._uow_factory = uow_factory
        self._clock = clock

        self.source_id = f"connector:{binding.adapter_id}:{binding.id}"
        self.tenant_id = binding.tenant_id
        self.app_id = binding.app_id
        self.kind = manifest.kind if manifest else None

    @property
    def metric_label(self) -> str:
        """The adapter, not the binding. /metrics is unauthenticated, so the
        gauge says "the prometheus adapter is down somewhere" and the
        authenticated API says whose."""
        return f"adapter:{self._binding.adapter_id}"

    async def check(self) -> ComponentHealth:
        observed = await self._observe()
        await self._persist(observed)
        return ComponentHealth(
            component=self.source_id,
            status=observed.status,
            detail=observed.status_detail,
        )

    async def _observe(self) -> ConnectorBinding:
        now = self._clock.now()
        binding = self._binding

        if self._manifest is None:
            return binding.observed_failure(
                now,
                f"adapter {binding.adapter_id!r} is no longer installed; "
                "this binding is orphaned",
                terminal=True,
            )
        if not self._runtimes.has(binding.adapter_id):
            return binding.observed_unknown(
                now,
                f"no runtime for {binding.adapter_id!r} on this node "
                f"(availability: {self._manifest.availability.value})",
            )

        try:
            credentials = await self._connectors.credentials_for(binding)
        except Exception as exc:
            return binding.observed_failure(
                now, f"could not read credentials: {type(exc).__name__}", terminal=True
            )
        missing = [
            name for name in self._manifest.auth.credential_fields if name not in credentials
        ]
        if missing:
            return binding.observed_failure(
                now, f"credentials {sorted(missing)} are missing from the store", terminal=True
            )

        try:
            adapter = self._runtimes.build(
                AdapterContext(
                    manifest=self._manifest, binding=binding, credentials=credentials
                )
            )
        except Exception as exc:
            return binding.observed_failure(
                now, f"runtime could not be built: {type(exc).__name__}: {exc}"[:300],
                terminal=True,
            )

        try:
            alive = await asyncio.wait_for(
                adapter.ping(), timeout=self._manifest.limits.timeout_s
            )
        except (asyncio.TimeoutError, AdapterTimeoutError):
            return binding.observed_failure(
                now, f"ping timed out after {self._manifest.limits.timeout_s}s"
            )
        except AdapterAuthError as exc:
            return binding.observed_failure(
                now, f"credentials rejected: {exc}"[:300], terminal=True
            )
        except AdapterError as exc:
            return binding.observed_failure(
                now, f"{type(exc).__name__}: {exc}"[:300]
            )
        except Exception as exc:
            # An adapter that raises something outside the taxonomy is a bug in
            # that adapter, not a reason to lose the poll.
            logger.warning(
                "adapter ping raised outside the error taxonomy",
                extra={
                    "connector": self.source_id,
                    "error": f"{type(exc).__name__}: {exc}",
                },
            )
            return binding.observed_failure(now, f"{type(exc).__name__}: {exc}"[:300])

        if not alive:
            return binding.observed_failure(now, "ping reported not ready")
        detail = "reachable"
        if self._manifest.simulated:
            detail = "reachable (SIMULATED)"
        return binding.observed_ok(now, detail)

    async def _persist(self, binding: ConnectorBinding) -> None:
        """The binding row is the durable record of what the loop saw, so the
        integration health screen shows the last error and the last success
        across a restart rather than an empty table until the next poll."""
        async with self._uow_factory() as uow:
            await uow.connectors.update(binding)
            await uow.commit()


class ConnectorHealthSources:
    """Re-reads the enabled bindings on every poll and turns each into a source.

    Registered on ConnectorHealthService as a provider rather than as fixed
    sources: bindings are created and disabled while the process runs, and a
    disabled binding must leave the loop immediately - that is what "an operator
    turned it off" has to mean.
    """

    def __init__(
        self,
        uow_factory: Callable[[], IdentityUnitOfWork],
        registry: AdapterRegistry,
        runtimes: AdapterRuntimes,
        connectors: ConnectorService,
        clock: Clock,
    ):
        self._uow_factory = uow_factory
        self._registry = registry
        self._runtimes = runtimes
        self._connectors = connectors
        self._clock = clock

    async def __call__(self) -> Sequence[HealthSource]:
        async with self._uow_factory() as uow:
            bindings = list(await uow.connectors.list_enabled_for_health())
        return [
            ConnectorBindingHealthSource(
                binding,
                self._registry.find(binding.adapter_id),
                runtimes=self._runtimes,
                connectors=self._connectors,
                uow_factory=self._uow_factory,
                clock=self._clock,
            )
            for binding in bindings
        ]
