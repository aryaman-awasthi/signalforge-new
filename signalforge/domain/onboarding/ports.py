"""Onboarding ports (CLAUDE.md rule 1).

Tenant isolation lives in the implementations (REQ-013): every method that
touches tenant data takes `tenant_id` and applies the predicate inside the
repository, never in a handler.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Sequence

from signalforge.contracts.canonical import TopologySnapshot
from signalforge.domain.onboarding.applications import Application
from signalforge.domain.onboarding.profile import OnboardingProfile


class ApplicationRepository(ABC):
    @abstractmethod
    async def add(self, application: Application) -> None: ...

    @abstractmethod
    async def get(self, tenant_id: str, app_id: str) -> Optional[Application]: ...

    @abstractmethod
    async def list(self, tenant_id: str) -> Sequence[Application]: ...

    @abstractmethod
    async def update(self, application: Application) -> None: ...

    @abstractmethod
    async def delete(self, tenant_id: str, app_id: str) -> bool: ...


class OnboardingProfileRepository(ABC):
    @abstractmethod
    async def get(self, tenant_id: str, app_id: str) -> Optional[OnboardingProfile]: ...

    @abstractmethod
    async def put(self, profile: OnboardingProfile) -> None:
        """Insert or replace. The profile is one document per application, so a
        step that writes only its own section still writes the whole row - which
        is what keeps the aggregate's cross-section invariants (a probe naming a
        journey, a baseline naming a signal) enforceable at all."""

    @abstractmethod
    async def delete(self, tenant_id: str, app_id: str) -> bool: ...


@dataclass(frozen=True)
class TopologyVersion:
    """One imported map, kept forever.

    Versions are never overwritten: "has the topology changed since the incident
    started" is answered by comparing versions, and S5's blast-radius delta needs
    the older graph to still exist.
    """

    tenant_id: str
    app_id: str
    version: str
    snapshot: TopologySnapshot
    imported_at: datetime
    imported_by: str = ""
    source: str = "upload"
    normalized_by: str = ""

    @property
    def node_count(self) -> int:
        return len(self.snapshot.nodes)

    @property
    def edge_count(self) -> int:
        return len(self.snapshot.edges)


class TopologyVersionRepository(ABC):
    @abstractmethod
    async def add(self, version: TopologyVersion) -> None: ...

    @abstractmethod
    async def find(
        self, tenant_id: str, app_id: str, version: str
    ) -> Optional[TopologyVersion]:
        """The idempotency check: re-uploading an unchanged document must not
        create a second version."""

    @abstractmethod
    async def latest(self, tenant_id: str, app_id: str) -> Optional[TopologyVersion]: ...

    @abstractmethod
    async def list(
        self, tenant_id: str, app_id: str, *, limit: int = 50
    ) -> Sequence[TopologyVersion]: ...

    @abstractmethod
    async def delete_all(self, tenant_id: str, app_id: str) -> int:
        """Every version of one application's map. Only ever called when the
        application itself is being removed - a map is never pruned while the
        application it describes is still watched."""


@dataclass(frozen=True)
class ProbeOutcome:
    """What one discovery probe saw. A non-2xx is a result, not an error: an
    endpoint answering 404 is a definite "this application does not serve that
    part of the contract", which is exactly what discovery wants to know."""

    url: str
    reachable: bool
    status_code: Optional[int] = None
    latency_ms: float = 0.0
    content_type: str = ""
    detail: str = ""

    @property
    def served(self) -> bool:
        return self.reachable and self.status_code is not None and (
            200 <= self.status_code < 300
        )

    @property
    def guarded(self) -> bool:
        """401 or 403: the endpoint exists and wants a credential.

        Discovery runs unauthenticated, so this is the *normal* answer from an
        application that protects its observe surface - and it is positive
        evidence, not a failure. Treating it as absence would make discovery
        useless against exactly the applications that are configured correctly.
        """
        return self.reachable and self.status_code in (401, 403)

    @property
    def present(self) -> bool:
        return self.served or self.guarded


class EndpointProbe(ABC):
    """An unauthenticated GET used only by auto-discovery.

    Separate from the adapter transport on purpose: discovery runs *before* any
    binding exists, so there is no manifest to take limits from and no credential
    to present. Implementations must bound the request themselves and must never
    follow a redirect off the host they were pointed at.
    """

    @abstractmethod
    async def probe(self, url: str, *, timeout_s: float = 3.0) -> ProbeOutcome: ...
