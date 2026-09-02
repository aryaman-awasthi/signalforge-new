"""Runtimes for the Spring Actuator family (REQ-031).

A Spring Boot application with Micrometer on its classpath is already serving
`/actuator/prometheus` and `/actuator/health`. These two runtimes read exactly
those and translate them into the canonical models - nothing else. No retry, no
cache, no interpretation, and in particular no recomputing of the aggregate
health status: "adapters translate; they never decide" is the SPI's first rule,
and an adapter that decided an application was healthier than the application
said would be the most expensive kind of helpful.

Two things this family deliberately does not do:

- **A range query.** `/actuator/prometheus` is a scrape. It carries the current
  sample and no history, so there is no honest way to answer "what were the
  values between then and now" from it. The manifest declares only
  `query_instant`; a deployment that needs ranges binds the prometheus adapter
  against the server that stores these scrapes.
- **Logs, actions, chaos and traces.** Actuator has no search endpoint, no
  action manifest, no fault-injection surface and no trace store, so no adapter
  here claims one. An application that wants SignalForge to read its logs or
  execute a remediation serves `contracts/observe.py`; this family is what can
  be read from an application that has been asked for nothing at all.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping

from signalforge.contracts.canonical import (
    ComponentHealth,
    HealthReport,
    HealthStatus,
    MetricSeries,
    ProbeResult,
    ProbeSpec,
)
from signalforge.contracts.errors import AdapterError, NotSupportedError
from signalforge.contracts.spi import HealthAdapter, MetricsAdapter
from signalforge.domain.integrations.promtext import parse_exposition, select
from signalforge.infrastructure.adapters.base import GenericAdapter
from signalforge.infrastructure.adapters.probes import run_synthetic_probe

METRICS_PATH = "/actuator/prometheus"
HEALTH_PATH = "/actuator/health"

DETAIL_MAX_CHARS = 200

SPRING_STATUS: Mapping[str, HealthStatus] = {
    "UP": HealthStatus.UP,
    "DOWN": HealthStatus.DOWN,
    "OUT_OF_SERVICE": HealthStatus.DEGRADED,
}
"""Actuator's status is an open set - an application may register a status of
its own, and a health group may report one this table has never seen. Anything
unrecognised becomes UNKNOWN, which says "not measured"; mapping it to UP would
be the one answer capable of hiding an outage."""


def spring_status(raw: Any) -> HealthStatus:
    return SPRING_STATUS.get(str(raw).strip().upper(), HealthStatus.UNKNOWN)


class ActuatorMetricsAdapter(GenericAdapter, MetricsAdapter):
    async def ping(self) -> bool:
        # The endpoint this binding actually reads, not /actuator/health: a
        # metrics binding whose scrape returns 500 is not healthy because the
        # application's health endpoint says it is.
        return await self.transport().reachable(METRICS_PATH)

    async def query_instant(self, query: str, at: datetime) -> list[MetricSeries]:
        text = await self.transport().get_text(METRICS_PATH)
        series = parse_exposition(text, at, max_rows=self.spec.limits.max_rows)
        return self.truncate(select(series, query))

    async def query_range(
        self, query: str, start: datetime, end: datetime, step_s: float
    ) -> list[MetricSeries]:
        raise NotSupportedError(
            f"{METRICS_PATH} is a scrape and carries no history, so a range query "
            "has no honest answer here; bind the prometheus adapter against the "
            "server that stores these scrapes for query_range"
        )


class ActuatorHealthAdapter(GenericAdapter, HealthAdapter):
    async def ping(self) -> bool:
        return await self.transport().reachable(HEALTH_PATH)

    async def get_health(self) -> HealthReport:
        payload = await self.transport().get_json(HEALTH_PATH)
        try:
            return self._report(payload)
        except Exception as exc:
            raise AdapterError(
                f"{HEALTH_PATH} did not return a Spring Actuator health document: {exc}"
            ) from exc

    async def run_probe(self, probe: ProbeSpec) -> ProbeResult:
        return await run_synthetic_probe(self.transport(), probe, self.now)

    # -- translation -------------------------------------------------------

    def _report(self, payload: Any) -> HealthReport:
        if not isinstance(payload, Mapping):
            raise ValueError("expected a JSON object")
        components = payload.get("components") or {}
        if not isinstance(components, Mapping):
            # Actuator keys components by name. A list here means something
            # other than Actuator is answering on this path, and reading it as
            # "no components" would report that application healthy.
            raise ValueError("'components' must be an object keyed by component name")
        return HealthReport(
            # The application's own aggregate, carried through as it was given.
            # Recomputing it from the components would be this adapter deciding
            # what the application meant.
            status=spring_status(payload.get("status")),
            components=[
                self._component(name, entry) for name, entry in components.items()
            ],
            checked_at=self.now(),
        )

    def _component(self, name: Any, entry: Any) -> ComponentHealth:
        body = entry if isinstance(entry, Mapping) else {}
        details = body.get("details")
        return ComponentHealth(
            component=str(name),
            status=spring_status(body.get("status")),
            # An indicator's details are free text the application chose without
            # knowing it would be stored, shown and put in front of a model, so
            # they go through the manifest's redaction here, at the boundary,
            # before they can reach an event or an evidence record.
            detail=(
                None
                if details is None
                else self.redact(str(details))[:DETAIL_MAX_CHARS]
            ),
        )
