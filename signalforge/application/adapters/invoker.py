"""The one path from a binding to a live adapter call (REQ-030, REQ-031).

Everything that ever asks an integrated system for anything goes through here -
the onboarding wizard's connection test, evidence gathering, the tool gateway,
a drill - because five checks have to happen on every single call and none of
them may be a caller's responsibility to remember:

1. The binding exists **in this tenant** and is enabled.
2. Its adapter is still installed and this node has a runtime for it.
3. The credential-scope floor allows this capability on this credential
   (`assert_can_invoke`): a read-only credential can never mutate, chaos runs
   only on a drill credential, and a drill credential runs only chaos. This is
   re-checked at *call* time, not at bind time, which is what makes a permission
   change mid-flow safe: the next call is refused even though the binding, the
   runtime and the credential are all still there.
4. Credentials are decrypted here and handed straight to the instance. Nothing
   caches an adapter, so nothing caches a credential.
5. The manifest's timeout bounds the call, and a timeout is reported as a
   timeout rather than as a failure - the outcome of a mutating call that timed
   out is unknown, and the two must never be conflated.

The invoker never retries and never decides. Policy is above it (S6) and
translation is below it.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import time
from dataclasses import dataclass
from typing import Any, Callable, Optional

from signalforge.application.adapters.connector_service import ConnectorService
from signalforge.application.identity.uow import IdentityUnitOfWork
from signalforge.contracts.errors import (
    AdapterError,
    AdapterTimeoutError,
    NotSupportedError,
)
from signalforge.contracts.spi import Adapter
from signalforge.domain.adapters.bindings import ConnectorBinding, assert_can_invoke
from signalforge.domain.adapters.errors import (
    AdapterNotFoundError,
    ConnectorBindingNotFoundError,
    ConnectorDisabledError,
)
from signalforge.domain.adapters.manifest import AdapterManifest
from signalforge.domain.adapters.ports import (
    AdapterContext,
    AdapterRegistry,
    AdapterRuntimes,
)

logger = logging.getLogger("signalforge.adapters")

CAPABILITY_WATCHDOG_FACTOR = 3
CAPABILITY_WATCHDOG_HEADROOM_S = 1.0


def capability_watchdog_s(call_timeout_s: float) -> float:
    """How long the invoker waits before it stops waiting.

    Deliberately larger than the manifest's per-*call* timeout, which the HTTP
    transport already enforces. One capability is not one call - `execute` asks
    whether the idempotency key already ran before it sends anything - and a
    watchdog set to a single call's timeout would fire during the second call
    every time. Worse, it would fire *before* the adapter could turn its own
    timeout into an UNCERTAIN outcome, so a timed-out remediation would be
    reported as a platform error and retried.

    This is the backstop for a runtime that hangs with no timeout of its own;
    the per-call bound is where the real limit lives.
    """
    return call_timeout_s * CAPABILITY_WATCHDOG_FACTOR + CAPABILITY_WATCHDOG_HEADROOM_S


ALWAYS_ALLOWED = frozenset({"ping"})
"""`ping` is on the base SPI class, is required of every adapter and is never a
declarable capability, so it is not subject to the capability check. It reads
nothing and changes nothing; the connector health loop depends on it being
unconditional."""


@dataclass(frozen=True)
class ResolvedAdapter:
    adapter: Adapter
    manifest: AdapterManifest
    binding: ConnectorBinding


class ConnectorInvoker:
    def __init__(
        self,
        uow_factory: Callable[[], IdentityUnitOfWork],
        registry: AdapterRegistry,
        runtimes: AdapterRuntimes,
        connectors: ConnectorService,
        metrics=None,
    ):
        self._uow_factory = uow_factory
        self._registry = registry
        self._runtimes = runtimes
        self._connectors = connectors
        self._metrics = metrics

    async def resolve(
        self, tenant_id: str, binding_id: str, capability: str
    ) -> ResolvedAdapter:
        async with self._uow_factory() as uow:
            binding = await uow.connectors.get(tenant_id, binding_id)
        if binding is None:
            # Same message whether it belongs to another tenant or to nobody
            # (REQ-013): an error must not confirm that an id exists elsewhere.
            raise ConnectorBindingNotFoundError(f"connector {binding_id} not found")
        if not binding.enabled:
            raise ConnectorDisabledError(
                f"connector {binding.name!r} is disabled; enable it before using it"
            )
        manifest = self._registry.find(binding.adapter_id)
        if manifest is None:
            raise AdapterNotFoundError(
                f"adapter {binding.adapter_id!r} is no longer installed; this "
                "binding is orphaned"
            )
        if capability not in ALWAYS_ALLOWED:
            # The floor, checked on every call rather than once at bind time.
            assert_can_invoke(manifest, binding, capability)
        if not self._runtimes.has(binding.adapter_id):
            raise AdapterNotFoundError(
                f"no runtime for adapter {binding.adapter_id!r} is installed on "
                f"this node (availability: {manifest.availability.value})"
            )
        credentials = await self._connectors.credentials_for(binding)
        adapter = self._runtimes.build(
            AdapterContext(manifest=manifest, binding=binding, credentials=credentials)
        )
        return ResolvedAdapter(adapter=adapter, manifest=manifest, binding=binding)

    async def invoke(
        self,
        tenant_id: str,
        binding_id: str,
        capability: str,
        *args: Any,
        timeout_s: Optional[float] = None,
        **kwargs: Any,
    ) -> Any:
        resolved = await self.resolve(tenant_id, binding_id, capability)
        method = getattr(resolved.adapter, capability, None)
        if method is None or not callable(method):
            raise NotSupportedError(
                f"adapter {resolved.manifest.id} has no capability {capability!r}"
            )
        watchdog = capability_watchdog_s(resolved.manifest.limits.timeout_s)
        # A caller may tighten the watchdog, never widen it.
        bound = min(timeout_s or watchdog, watchdog)
        started = time.perf_counter()
        outcome = "ok"
        try:
            result = method(*args, **kwargs)
            if inspect.isawaitable(result):
                result = await asyncio.wait_for(result, timeout=bound)
            return result
        except asyncio.TimeoutError as exc:
            outcome = "timeout"
            raise AdapterTimeoutError(
                f"{resolved.manifest.id}.{capability} did not answer within {bound}s"
            ) from exc
        except AdapterTimeoutError:
            outcome = "timeout"
            raise
        except AdapterError:
            outcome = "error"
            raise
        except Exception:
            # An adapter raising outside the taxonomy is a bug in that adapter.
            # It is counted separately so a broken integration is visible as
            # something other than "the target is down".
            outcome = "untyped_error"
            logger.warning(
                "adapter raised outside the error taxonomy",
                extra={"adapter": resolved.manifest.id, "capability": capability},
            )
            raise
        finally:
            self._record(resolved.manifest.id, capability, outcome, started)

    def _record(
        self, adapter_id: str, capability: str, outcome: str, started: float
    ) -> None:
        if self._metrics is None:
            return
        labels = {"adapter": adapter_id, "capability": capability}
        self._metrics.increment(
            "signalforge_adapter_calls_total", labels | {"outcome": outcome}
        )
        self._metrics.observe(
            "signalforge_adapter_call_duration_seconds",
            time.perf_counter() - started,
            labels,
        )
