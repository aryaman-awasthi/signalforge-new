"""Connector health as a domain concept (REQ-025).

Every source of data the platform depends on - its own database, the bus, the
worker group today, and every adapter instance from Sprint 3 - answers the same
question the same way, using the canonical HealthReport from the frozen
contracts. That is what lets the integration health screen treat a database and
a vendor API identically, and what lets system.connector.health be one topic
rather than one per integration.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional, Sequence

from signalforge.contracts.canonical import ComponentHealth, HealthStatus
from signalforge.contracts.envelope import AdapterKind, Topics

CONNECTOR_HEALTH_TOPIC = Topics.CONNECTOR_HEALTH

PLATFORM_TENANT_ID = "_platform"
"""Reserved tenant for events about the platform itself rather than any
customer's incidents. Replay is tenant-scoped, so no tenant can read these; the
platform status endpoint serves them to operators instead."""

_SEVERITY = {
    HealthStatus.UP: 0,
    HealthStatus.UNKNOWN: 1,
    HealthStatus.DEGRADED: 1,
    HealthStatus.DOWN: 2,
}


class HealthSource(ABC):
    """Something whose health the platform polls."""

    source_id: str
    kind: Optional[AdapterKind] = None

    @abstractmethod
    async def check(self) -> ComponentHealth: ...


def overall_status(components: Sequence[ComponentHealth]) -> HealthStatus:
    """The worst component wins. UNKNOWN counts as degraded rather than healthy:
    a source that cannot be reached is not evidence that anything is fine."""
    if not components:
        return HealthStatus.UNKNOWN
    worst = max(_SEVERITY[c.status] for c in components)
    if worst == 0:
        return HealthStatus.UP
    if worst == 2:
        return HealthStatus.DOWN
    return HealthStatus.DEGRADED


def transitioned(previous: Optional[HealthStatus], current: HealthStatus) -> bool:
    """Health is polled every few seconds; only a change is worth an audit entry,
    or the audit chain becomes a heartbeat log nobody can read."""
    return previous != current
