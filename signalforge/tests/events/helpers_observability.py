"""Health sources used by the observability tests."""

from __future__ import annotations

from signalforge.contracts.canonical import ComponentHealth, HealthStatus
from signalforge.domain.observability.health import HealthSource


class FlippingHealthSource(HealthSource):
    """A source whose verdict the test changes between polls."""

    def __init__(self, source_id: str, status: HealthStatus):
        self.source_id = source_id
        self.status = status

    async def check(self) -> ComponentHealth:
        return ComponentHealth(component=self.source_id, status=self.status)


class ExplodingHealthSource(HealthSource):
    """A source whose check itself fails - the case that must not take the poll
    loop down with it."""

    def __init__(self, source_id: str):
        self.source_id = source_id

    async def check(self) -> ComponentHealth:
        raise RuntimeError("connection refused")
