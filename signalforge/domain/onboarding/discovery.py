"""Auto-discovery: what to probe, and what a hit means (REQ-036).

The rule that makes this app-agnostic is that nothing here names an adapter and
nothing here names an application. There are two sources of truth:

- the **frozen observe contract** (`contracts/observe.py`) says which path each
  adapter kind reads, so probing an application's base URL for those paths asks
  "which parts of the contract do you serve?" rather than "are you the app we
  know about?";
- the **registry** says which installed adapter can read a kind or speaks to a
  vendor, so the suggestion comes from what is installed at run time rather than
  from a list compiled into the core.

The two well-known local services are declared by *vendor* and port, not by
adapter id, and are matched against manifests through `AdapterManifest.vendor`.
A deployment that installs a different Prometheus adapter gets that one
suggested with no change here.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping, Optional, Sequence

from signalforge.contracts.envelope import AdapterKind
from signalforge.contracts.observe import OBSERVE_PATHS

BASE_URL_FIELD = "base_url"

OBSERVE_KIND_PATHS: dict[AdapterKind, str] = {
    AdapterKind.METRICS: OBSERVE_PATHS["metrics"],
    AdapterKind.LOGS: OBSERVE_PATHS["logs"],
    AdapterKind.TRACES: OBSERVE_PATHS["traces"],
    AdapterKind.HEALTH: OBSERVE_PATHS["health"],
    AdapterKind.TOPOLOGY: OBSERVE_PATHS["topology"],
    AdapterKind.ACTIONS: OBSERVE_PATHS["actions_manifest"],
    AdapterKind.CHAOS: OBSERVE_PATHS["chaos_manifest"],
}
"""The seven kinds the observe contract covers, and the path each one reads.
Derived from the frozen paths, so a contract change cannot leave discovery
probing something that no longer exists."""


class DiscoverySource(str, Enum):
    OBSERVE = "observe"
    LOCAL = "local"


@dataclass(frozen=True)
class LocalService:
    """A monitoring service a single-machine deployment plausibly already runs.

    Identified by vendor so the suggestion is resolved against whatever adapter
    the registry holds for that vendor, and by a health path that is cheap and
    unauthenticated on both of them.
    """

    vendor: str
    label: str
    url: str
    health_path: str

    @property
    def probe_url(self) -> str:
        return f"{self.url.rstrip('/')}/{self.health_path.lstrip('/')}"


LOCAL_SERVICES: tuple[LocalService, ...] = (
    LocalService(
        vendor="prometheus",
        label="Prometheus",
        url="http://localhost:9090",
        health_path="/-/healthy",
    ),
    LocalService(
        vendor="grafana",
        label="Grafana",
        url="http://localhost:3000",
        health_path="/api/health",
    ),
)


@dataclass(frozen=True)
class DiscoveryTarget:
    """One URL to try, and what a 2xx from it would mean."""

    source: DiscoverySource
    label: str
    url: str
    base_url: str
    kind: Optional[AdapterKind] = None
    vendor: Optional[str] = None

    @property
    def key(self) -> str:
        return (
            f"{self.source.value}:{self.kind.value}"
            if self.kind is not None
            else f"{self.source.value}:{self.vendor}"
        )


def observe_targets(base_url: str) -> tuple[DiscoveryTarget, ...]:
    """Every observe path, under the application's own root."""
    root = base_url.rstrip("/")
    return tuple(
        DiscoveryTarget(
            source=DiscoverySource.OBSERVE,
            label=f"{kind.value} ({path})",
            url=f"{root}{path}",
            base_url=root,
            kind=kind,
        )
        for kind, path in OBSERVE_KIND_PATHS.items()
    )


def local_targets(
    services: Sequence[LocalService] = LOCAL_SERVICES,
) -> tuple[DiscoveryTarget, ...]:
    return tuple(
        DiscoveryTarget(
            source=DiscoverySource.LOCAL,
            label=service.label,
            url=service.probe_url,
            base_url=service.url,
            vendor=service.vendor,
        )
        for service in services
    )


def prefill_config(
    config_schema: Mapping[str, Any], base_url: str
) -> dict[str, Any]:
    """The Basics base URL, dropped into whichever config field asks for it.

    This is the whole of "app base URL in Basics prefills observe adapters": the
    wizard renders an adapter's own `config_schema`, and any adapter declaring a
    `base_url` property arrives with it already filled. Nothing is invented -
    a schema that does not ask for a base URL gets an empty prefill.
    """
    properties = (config_schema or {}).get("properties") or {}
    if not isinstance(properties, Mapping) or BASE_URL_FIELD not in properties:
        return {}
    return {BASE_URL_FIELD: base_url.rstrip("/")}
