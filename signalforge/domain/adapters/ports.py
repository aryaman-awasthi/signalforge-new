"""Adapter-registry ports (CLAUDE.md rule 1).

The domain says what it needs; infrastructure supplies it. The registry reads
files, the runtime factory constructs objects that speak HTTP, the repository
talks to SQL - none of which the domain knows about.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Mapping, Optional, Sequence

from signalforge.contracts.envelope import AdapterKind
from signalforge.contracts.spi import Adapter
from signalforge.domain.adapters.bindings import ConnectorBinding
from signalforge.domain.adapters.manifest import (
    AdapterManifest,
    AdapterMode,
    CredentialScope,
)


@dataclass(frozen=True)
class ManifestLoadError:
    """A manifest that could not be loaded. Kept rather than raised: one broken
    adapter folder must not stop the other twelve from registering, and an
    operator has to be able to see why theirs is missing."""

    path: str
    error: str


class AdapterRegistry(ABC):
    """Every adapter this deployment knows about, loaded from manifests."""

    @abstractmethod
    def all(self) -> Sequence[AdapterManifest]: ...

    @abstractmethod
    def get(self, adapter_id: str) -> AdapterManifest:
        """Raises AdapterNotFoundError when nothing is installed under that id."""

    @abstractmethod
    def find(self, adapter_id: str) -> Optional[AdapterManifest]: ...

    @abstractmethod
    def list(
        self,
        *,
        kind: Optional[AdapterKind] = None,
        mode: Optional[AdapterMode] = None,
        scope: Optional[CredentialScope] = None,
        simulated: Optional[bool] = None,
    ) -> Sequence[AdapterManifest]: ...

    @property
    @abstractmethod
    def errors(self) -> Sequence[ManifestLoadError]: ...


@dataclass(frozen=True)
class AdapterContext:
    """Everything an adapter instance needs and nothing it does not: its own
    manifest, the binding that configured it, and the decrypted credentials for
    exactly that binding's scope."""

    manifest: AdapterManifest
    binding: ConnectorBinding
    credentials: Mapping[str, str]

    def __repr__(self) -> str:  # never let a credential reach a log or a traceback
        return (
            f"AdapterContext(adapter={self.manifest.id!r}, "
            f"binding={self.binding.id!r}, credentials=<redacted>)"
        )


class AdapterRuntimes(ABC):
    """Which adapters this process can actually instantiate.

    A manifest can be installed without a runtime: that is what
    Availability.AVAILABLE means, and the health loop reports such a binding
    UNKNOWN rather than inventing a verdict.
    """

    @abstractmethod
    def has(self, adapter_id: str) -> bool: ...

    @abstractmethod
    def build(self, context: AdapterContext) -> Adapter: ...


class ConnectorBindingRepository(ABC):
    """Tenant isolation lives in the implementation (REQ-013): every method that
    reads tenant data takes tenant_id and filters on it inside the repository."""

    @abstractmethod
    async def add(self, binding: ConnectorBinding) -> None: ...

    @abstractmethod
    async def get(self, tenant_id: str, binding_id: str) -> Optional[ConnectorBinding]: ...

    @abstractmethod
    async def list(
        self,
        tenant_id: str,
        *,
        app_id: Optional[str] = None,
        adapter_id: Optional[str] = None,
        enabled_only: bool = False,
    ) -> Sequence[ConnectorBinding]: ...

    @abstractmethod
    async def find_conflict(
        self,
        tenant_id: str,
        *,
        app_id: str,
        adapter_id: str,
        scope: CredentialScope,
    ) -> Optional[ConnectorBinding]:
        """The uniqueness key: one binding per (app, adapter, credential scope)."""

    @abstractmethod
    async def update(self, binding: ConnectorBinding) -> None: ...

    @abstractmethod
    async def delete(self, tenant_id: str, binding_id: str) -> bool: ...

    @abstractmethod
    async def list_enabled_for_health(self) -> Sequence[ConnectorBinding]:
        """Cross-tenant by design, like EventStore.tenants: one health loop
        serves the whole install. Every binding it returns carries its own
        tenant_id, and everything the loop then writes goes back through the
        tenant-scoped methods above."""
