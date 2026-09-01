"""Auto-discovery (REQ-036).

Two questions, asked without a credential and without writing anything to the
integrated system:

1. **Which parts of the observe contract does this application serve?** Every
   path in the frozen contract is probed under the application's own base URL.
   A 2xx means yes; a 401 or 403 also means yes, and that it wants a credential
   - which is the normal answer from an application configured correctly, so
   treating it as absence would make discovery useless against exactly those.
2. **What monitoring is already running on this machine?** Prometheus on 9090
   and Grafana on 3000, matched to adapters by the *vendor* their manifests
   declare rather than by an adapter id compiled into the core.

The answer is a set of suggestions, never a binding: discovery proposes, the
operator decides, and no credential is guessed at. Each suggestion arrives with
its adapter's own config schema already pre-filled from the Basics base URL,
which is all "the base URL prefills the observe adapters" means.

A discovery run writes exactly one thing to SignalForge: an audit entry and an
event saying what was probed and what answered. It never writes a binding.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Callable, Optional, Sequence

from signalforge.application.events.bus import EventBus
from signalforge.application.identity.principal import Principal
from signalforge.application.identity.uow import IdentityUnitOfWork
from signalforge.application.onboarding.common import (
    DISCOVERY_TOPIC,
    record,
    require_application,
)
from signalforge.contracts.envelope import AdapterKind
from signalforge.domain.adapters.bindings import direction as binding_direction
from signalforge.domain.adapters.manifest import AdapterManifest, AdapterMode
from signalforge.domain.adapters.ports import AdapterRegistry, AdapterRuntimes
from signalforge.domain.identity.ports import Clock
from signalforge.domain.ingest.streams import ADAPTER_KINDS, IngestStream
from signalforge.domain.onboarding.discovery import (
    LOCAL_SERVICES,
    BASE_URL_FIELD,
    DiscoverySource,
    DiscoveryTarget,
    LocalService,
    local_targets,
    observe_targets,
    prefill_config,
)
from signalforge.domain.onboarding.ports import EndpointProbe, ProbeOutcome

PROBE_TIMEOUT_S = 3.0

STREAM_BY_KIND: dict[AdapterKind, IngestStream] = {
    kind: stream for stream, kind in ADAPTER_KINDS.items()
}
"""Reverse of the frozen stream table, so a push-mode suggestion can name the
ingest path the sender will post to."""

ACTION_BIND = "bind"
ACTION_ISSUE_KEY = "issue_ingest_key"


@dataclass(frozen=True)
class DiscoveryCandidate:
    adapter_id: str
    adapter_name: str
    kind: str
    mode: str
    direction: str
    availability: str
    runnable: bool
    simulated: bool
    credential_scope: str
    credential_fields: tuple[str, ...]
    suggested_config: dict
    action: str
    already_bound: bool
    reason: str
    stream: Optional[str] = None
    ingest_path: Optional[str] = None


@dataclass(frozen=True)
class DiscoveryFinding:
    key: str
    source: str
    label: str
    url: str
    present: bool
    guarded: bool
    status_code: Optional[int]
    latency_ms: float
    detail: str
    candidates: tuple[DiscoveryCandidate, ...] = ()


@dataclass(frozen=True)
class DiscoveryReport:
    app_id: str
    base_url: Optional[str]
    findings: tuple[DiscoveryFinding, ...]

    @property
    def endpoints_found(self) -> int:
        return sum(1 for finding in self.findings if finding.present)

    @property
    def candidates(self) -> tuple[DiscoveryCandidate, ...]:
        return tuple(
            candidate for finding in self.findings for candidate in finding.candidates
        )


class DiscoveryService:
    def __init__(
        self,
        uow_factory: Callable[[], IdentityUnitOfWork],
        registry: AdapterRegistry,
        runtimes: AdapterRuntimes,
        probe: EndpointProbe,
        bus: EventBus,
        clock: Clock,
        local_services: Sequence[LocalService] = LOCAL_SERVICES,
    ):
        self._uow_factory = uow_factory
        self._registry = registry
        self._runtimes = runtimes
        self._probe = probe
        self._bus = bus
        self._clock = clock
        self.local_services = tuple(local_services)
        """Mutable so a deployment that runs Prometheus somewhere other than the
        documented localhost port can point discovery at it without a code
        change. The defaults are the environment this platform ships for."""

    async def discover(self, principal: Principal, app_id: str) -> DiscoveryReport:
        async with self._uow_factory() as uow:
            application = await require_application(uow, principal.tenant_id, app_id)
            bound = {
                binding.adapter_id
                for binding in await uow.connectors.list(
                    principal.tenant_id, app_id=app_id
                )
            }
            keyed = {
                key.stream.value
                for key in await uow.ingest_keys.list(principal.tenant_id, app_id=app_id)
                if key.active
            }

        targets: list[DiscoveryTarget] = []
        if application.base_url:
            targets.extend(observe_targets(application.base_url))
        targets.extend(local_targets(self.local_services))

        outcomes = await asyncio.gather(
            *(self._probe.probe(target.url, timeout_s=PROBE_TIMEOUT_S) for target in targets)
        )
        findings = tuple(
            self._finding(target, outcome, bound, keyed)
            for target, outcome in zip(targets, outcomes)
        )
        report = DiscoveryReport(
            app_id=app_id, base_url=application.base_url, findings=findings
        )

        async with self._uow_factory() as uow:
            await record(
                uow,
                self._bus,
                self._clock,
                principal=principal,
                app_id=app_id,
                topic=DISCOVERY_TOPIC,
                payload={
                    "app_id": app_id,
                    "base_url": application.base_url,
                    "probed": len(findings),
                    "found": report.endpoints_found,
                    "endpoints": [
                        {
                            "key": finding.key,
                            "url": finding.url,
                            "status": finding.status_code,
                            "present": finding.present,
                        }
                        for finding in findings
                    ],
                    "suggested_adapters": sorted(
                        {candidate.adapter_id for candidate in report.candidates}
                    ),
                },
            )
            await uow.commit()
        return report

    # -- internals ---------------------------------------------------------

    def _finding(
        self,
        target: DiscoveryTarget,
        outcome: ProbeOutcome,
        bound: set[str],
        keyed: set[str],
    ) -> DiscoveryFinding:
        candidates: tuple[DiscoveryCandidate, ...] = ()
        if outcome.present:
            candidates = tuple(
                self._candidate(manifest, target, bound, keyed)
                for manifest in self._manifests_for(target)
            )
        return DiscoveryFinding(
            key=target.key,
            source=target.source.value,
            label=target.label,
            url=target.url,
            present=outcome.present,
            guarded=outcome.guarded,
            status_code=outcome.status_code,
            latency_ms=round(outcome.latency_ms, 2),
            detail=outcome.detail or self._describe(outcome),
            candidates=candidates,
        )

    @staticmethod
    def _describe(outcome: ProbeOutcome) -> str:
        if outcome.guarded:
            return "served, and it wants a credential"
        if outcome.served:
            return "served"
        if outcome.reachable:
            return f"answered {outcome.status_code}; not served here"
        return "unreachable"

    def _manifests_for(self, target: DiscoveryTarget) -> list[AdapterManifest]:
        """Which installed adapters could read what was found.

        Ordered so the wizard's first suggestion is the one that will actually
        work here: runnable before interface-only, then by id for stability.
        """
        if target.source is DiscoverySource.OBSERVE:
            matches = [
                manifest
                for manifest in self._registry.all()
                if manifest.kind is target.kind
                and BASE_URL_FIELD
                in ((manifest.config_schema or {}).get("properties") or {})
            ]
        else:
            matches = [
                manifest
                for manifest in self._registry.all()
                if manifest.vendor == target.vendor
            ]
        return sorted(
            matches,
            key=lambda manifest: (not self._runtimes.has(manifest.id), manifest.id),
        )

    def _candidate(
        self,
        manifest: AdapterManifest,
        target: DiscoveryTarget,
        bound: set[str],
        keyed: set[str],
    ) -> DiscoveryCandidate:
        stream = STREAM_BY_KIND.get(manifest.kind)
        pushes = manifest.mode is AdapterMode.PUSH
        already = (
            (stream.value in keyed)
            if (pushes and stream is not None)
            else manifest.id in bound
        )
        return DiscoveryCandidate(
            adapter_id=manifest.id,
            adapter_name=manifest.name,
            kind=manifest.kind.value,
            mode=manifest.mode.value,
            direction=binding_direction(manifest),
            availability=manifest.availability.value,
            runnable=self._runtimes.has(manifest.id),
            simulated=manifest.simulated,
            credential_scope=manifest.credential_scopes[0].value,
            credential_fields=tuple(manifest.auth.credential_fields),
            suggested_config=prefill_config(manifest.config_schema, target.base_url),
            # A push adapter is not bound and then polled: the sender posts to
            # the ingest API, so what discovery suggests is a key, not a
            # connector. Suggesting a binding would be direction-wrong.
            action=ACTION_ISSUE_KEY if pushes and stream else ACTION_BIND,
            stream=stream.value if (pushes and stream) else None,
            ingest_path=stream.path if (pushes and stream) else None,
            already_bound=already,
            reason=(
                f"{target.label} answered at {target.url}"
                if target.source is DiscoverySource.OBSERVE
                else f"{target.label} is running at {target.base_url}"
            ),
        )
