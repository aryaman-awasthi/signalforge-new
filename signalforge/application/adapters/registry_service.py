"""Reading the adapter registry (REQ-030).

Thin over the registry port on purpose: the registry is loaded once, is the same
for every tenant, and holds no tenant data - what varies per tenant is the
bindings, which live in the connector service. Keeping the two apart is what
lets the wizard's "choose an integration" step be a cached, tenant-independent
list while "configure it" is tenant-scoped and audited.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence

from signalforge.contracts.envelope import AdapterKind
from signalforge.domain.adapters.manifest import (
    AdapterManifest,
    AdapterMode,
    Availability,
    CredentialScope,
)
from signalforge.domain.adapters.matrix import CapabilityMatrix, capability_matrix
from signalforge.domain.adapters.ports import (
    AdapterRegistry,
    AdapterRuntimes,
    ManifestLoadError,
)


@dataclass(frozen=True)
class RegistryEntry:
    """A manifest plus what this node can do with it. `runnable` is the honest
    answer to "will this work if I bind it here": a manifest can be installed
    and shown while its runtime is not, and the registry says so rather than
    letting the first health poll be the discovery."""

    manifest: AdapterManifest
    runnable: bool

    @property
    def status(self) -> str:
        if self.runnable:
            return "installed"
        if self.manifest.availability is Availability.AVAILABLE:
            return "available"
        return "runtime missing"


class AdapterRegistryService:
    def __init__(self, registry: AdapterRegistry, runtimes: AdapterRuntimes):
        self._registry = registry
        self._runtimes = runtimes

    def entries(
        self,
        *,
        kind: Optional[AdapterKind] = None,
        mode: Optional[AdapterMode] = None,
        scope: Optional[CredentialScope] = None,
        simulated: Optional[bool] = None,
    ) -> Sequence[RegistryEntry]:
        return [
            RegistryEntry(manifest=manifest, runnable=self._runtimes.has(manifest.id))
            for manifest in self._registry.list(
                kind=kind, mode=mode, scope=scope, simulated=simulated
            )
        ]

    def entry(self, adapter_id: str) -> RegistryEntry:
        manifest = self._registry.get(adapter_id)
        return RegistryEntry(manifest=manifest, runnable=self._runtimes.has(adapter_id))

    def manifest(self, adapter_id: str) -> AdapterManifest:
        return self._registry.get(adapter_id)

    def matrix(self) -> CapabilityMatrix:
        return capability_matrix(self._registry.all())

    @property
    def errors(self) -> Sequence[ManifestLoadError]:
        """Manifests that failed to load. Surfaced rather than swallowed: a
        rejected manifest is invisible in every other view."""
        return self._registry.errors
