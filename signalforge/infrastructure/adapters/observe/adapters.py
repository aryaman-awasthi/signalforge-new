"""Runtimes for the http-observe set (REQ-031).

Everything an application has to do to be onboarded is serve
`contracts/observe.py`. These runtimes translate those responses into the
canonical models and nothing else: they do not decide, retry, cache or
interpret, because "adapters translate; they never decide" is the SPI's first
rule and every later guarantee - policy, budgets, confidence - depends on it.

Two behaviours here are load-bearing beyond translation, and both are about
failure:

- **An action execution that times out returns UNCERTAIN.** It does not raise
  and it does not report failure, because neither is true: the request may still
  be running and the change may already have happened. `get_execution` is the
  reconcile-before-retry primitive the gateway uses to find out (REQ-062).
- **A repeated idempotency key is not a second execution.** The adapter asks the
  application whether that key already ran before it sends anything.
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Any, Optional

from signalforge.contracts.canonical import (
    ActionDefinition,
    ActionExecution,
    ChaosInjection,
    ChaosScenario,
    ExecutionState,
    HealthReport,
    LogRecord,
    MetricSeries,
    ProbeResult,
    ProbeSpec,
    TraceSpan,
)
from signalforge.contracts.errors import (
    AdapterError,
    AdapterTimeoutError,
    AdapterUnavailableError,
    NotSupportedError,
)
from signalforge.contracts.manifests import (
    validate_action_manifest,
    validate_chaos_manifest,
)
from signalforge.contracts.observe import (
    OBSERVE_PATHS,
    ObserveLogsResponse,
    ObserveTracesResponse,
)
from signalforge.contracts.spi import (
    ActionsAdapter,
    ChaosAdapter,
    HealthAdapter,
    LogsAdapter,
    MetricsAdapter,
    TracesAdapter,
)
from signalforge.domain.integrations.promtext import parse_exposition, select
from signalforge.infrastructure.adapters.base import GenericAdapter
from signalforge.infrastructure.adapters.probes import run_synthetic_probe

DRILL_SCOPE_PREFIX = "drill:"


def _iso(value: datetime) -> str:
    return value.isoformat()


class ObserveAdapter(GenericAdapter):
    """Common to the six: ping the endpoint this adapter actually uses.

    Pinging `/observe/health` for all of them would report a metrics binding
    healthy while `/observe/metrics` returns 500. A connector's health has to
    mean "the thing this binding does works".
    """

    observe_path = "health"

    async def ping(self) -> bool:
        return await self.transport().reachable(OBSERVE_PATHS[self.observe_path])


class ObserveMetricsAdapter(ObserveAdapter, MetricsAdapter):
    observe_path = "metrics"

    async def query_instant(self, query: str, at: datetime) -> list[MetricSeries]:
        text = await self.transport().get_text(OBSERVE_PATHS["metrics"])
        series = parse_exposition(text, at, max_rows=self.spec.limits.max_rows)
        return self.truncate(select(series, query))

    async def query_range(
        self, query: str, start: datetime, end: datetime, step_s: float
    ) -> list[MetricSeries]:
        # `/observe/metrics` is a scrape: it carries the current sample and no
        # history, so there is no honest way to answer a range from it. The
        # manifest declares only query_instant, and the platform's own rollups
        # (S4) are built from repeated instant scrapes. A deployment that needs
        # ranges binds the prometheus adapter, which has a range API.
        raise NotSupportedError(
            "http-observe reads a point-in-time scrape and cannot answer a range "
            "query; bind the prometheus adapter for query_range"
        )


class ObserveLogsAdapter(ObserveAdapter, LogsAdapter):
    observe_path = "logs"

    async def ping(self) -> bool:
        now = self.now()
        await self._fetch(query="", start=now, end=now, limit=1)
        return True

    async def search(
        self, query: str, start: datetime, end: datetime, limit: int
    ) -> list[LogRecord]:
        records = await self._fetch(query=query, start=start, end=end, limit=limit)
        return [self._redacted(record) for record in self.truncate(records, limit)]

    async def aggregate(
        self, query: str, start: datetime, end: datetime, group_by: str
    ) -> dict[str, int]:
        """Count matching records per value of `group_by`.

        Grouped here rather than pushed down because the observe contract has no
        aggregation endpoint: an application only has to be able to list its
        logs. `max_rows` bounds what is counted, and the count is over exactly
        what was read - it is never extrapolated.
        """
        records = await self._fetch(
            query=query, start=start, end=end, limit=self.spec.limits.max_rows
        )
        counts: dict[str, int] = {}
        for record in records:
            key = self._group_key(record, group_by)
            counts[key] = counts.get(key, 0) + 1
        return dict(sorted(counts.items(), key=lambda item: (-item[1], item[0])))

    async def sample(
        self, query: str, start: datetime, end: datetime, n: int
    ) -> list[LogRecord]:
        """`n` records spread across the window, not the first `n`.

        The first n records of an incident window are all from its first second,
        which is the least useful sample anyone could take.
        """
        records = await self._fetch(
            query=query, start=start, end=end, limit=self.spec.limits.max_rows
        )
        if n <= 0 or not records:
            return []
        if len(records) <= n:
            return [self._redacted(record) for record in records]
        stride = len(records) / n
        picked = [records[min(int(index * stride), len(records) - 1)] for index in range(n)]
        return [self._redacted(record) for record in picked]

    async def _fetch(
        self, *, query: str, start: datetime, end: datetime, limit: int
    ) -> list[LogRecord]:
        payload = await self.transport().get_json(
            OBSERVE_PATHS["logs"],
            params={
                "query": query,
                "start": _iso(start),
                "end": _iso(end),
                "limit": min(limit, self.spec.limits.max_rows),
            },
        )
        try:
            response = ObserveLogsResponse.model_validate(payload)
        except Exception as exc:
            raise AdapterError(f"/observe/logs did not match the contract: {exc}") from exc
        return list(response.records)

    def _redacted(self, record: LogRecord) -> LogRecord:
        return record.model_copy(
            update={
                "message": self.redact(record.message),
                "attributes": {
                    key: self.redact(value) for key, value in record.attributes.items()
                },
            }
        )

    @staticmethod
    def _group_key(record: LogRecord, group_by: str) -> str:
        if group_by.startswith("attributes."):
            return record.attributes.get(group_by[len("attributes."):], "")
        value = getattr(record, group_by, None)
        if value is None:
            raise AdapterError(
                f"cannot group logs by {group_by!r}; use a LogRecord field or "
                "attributes.<key>"
            )
        return value.value if hasattr(value, "value") else str(value)


class ObserveTracesAdapter(ObserveAdapter, TracesAdapter):
    observe_path = "traces"

    async def ping(self) -> bool:
        now = self.now()
        await self._fetch({"start": _iso(now), "end": _iso(now), "limit": 1})
        return True

    async def search(
        self,
        start: datetime,
        end: datetime,
        limit: int,
        service: Optional[str] = None,
        operation: Optional[str] = None,
        errors_only: bool = False,
    ) -> list[TraceSpan]:
        params: dict[str, Any] = {
            "start": _iso(start),
            "end": _iso(end),
            "limit": min(limit, self.spec.limits.max_rows),
            "errors_only": str(bool(errors_only)).lower(),
        }
        if service:
            params["service"] = service
        if operation:
            params["operation"] = operation
        spans = await self._fetch(params)
        return [self._redacted(span) for span in self.truncate(spans, limit)]

    async def get_trace(self, trace_id: str) -> list[TraceSpan]:
        spans = await self._fetch({"trace_id": trace_id})
        return [self._redacted(span) for span in self.truncate(spans)]

    async def _fetch(self, params: dict[str, Any]) -> list[TraceSpan]:
        payload = await self.transport().get_json(OBSERVE_PATHS["traces"], params=params)
        try:
            response = ObserveTracesResponse.model_validate(payload)
        except Exception as exc:
            raise AdapterError(
                f"/observe/traces did not match the contract: {exc}"
            ) from exc
        return list(response.spans)

    def _redacted(self, span: TraceSpan) -> TraceSpan:
        return span.model_copy(
            update={
                "attributes": {
                    key: self.redact(value) for key, value in span.attributes.items()
                }
            }
        )


class ObserveHealthAdapter(ObserveAdapter, HealthAdapter):
    observe_path = "health"

    async def get_health(self) -> HealthReport:
        payload = await self.transport().get_json(OBSERVE_PATHS["health"])
        try:
            return HealthReport.model_validate(payload)
        except Exception as exc:
            raise AdapterError(
                f"/observe/health did not match the contract: {exc}"
            ) from exc

    async def run_probe(self, probe: ProbeSpec) -> ProbeResult:
        """A synthetic probe SignalForge runs itself.

        The target may be an absolute URL or a path relative to the binding's
        base URL, so a journey probe can exercise a public entry point rather
        than only the observe surface. A non-2xx is a *result*, not an error:
        `expected_status` is what decides, and a probe that raised on 500 could
        never assert that a page correctly returns 500.

        The measurement itself lives in `adapters/probes.py` because it is not a
        translation of anything the observe contract publishes - it is
        SignalForge making a request and timing it, and every health adapter has
        to measure it the same way.
        """
        return await run_synthetic_probe(self.transport(), probe, self.now)


class ObserveActionsAdapter(ObserveAdapter, ActionsAdapter):
    observe_path = "actions_manifest"

    async def manifest(self) -> list[ActionDefinition]:
        document = await self.transport().get_json(OBSERVE_PATHS["actions_manifest"])
        if not isinstance(document, dict):
            raise AdapterError("/observe/actions must return an action manifest object")
        # Validated against the frozen schema, so an application cannot publish
        # a high-risk action with no approver roles and have it silently become
        # executable here.
        validate_action_manifest(document)
        return [ActionDefinition.model_validate(item) for item in document["actions"]]

    async def execute(
        self, action_id: str, params: dict[str, Any], idempotency_key: str
    ) -> ActionExecution:
        already = await self.get_execution(idempotency_key)
        if already is not None:
            return already
        try:
            payload = await self.transport().post_json(
                OBSERVE_PATHS["action_execute"].format(action_id=action_id),
                body={"params": dict(params), "idempotency_key": idempotency_key},
            )
        except (AdapterTimeoutError, AdapterUnavailableError) as exc:
            # The outcome is unknown, which is not the same as failed. Reporting
            # failure here would invite a retry that double-applies the change.
            return ActionExecution(
                execution_id=self._uncertain_id(idempotency_key),
                action_id=action_id,
                idempotency_key=idempotency_key,
                state=ExecutionState.UNCERTAIN,
                requested_at=self.now(),
                error=f"{type(exc).__name__}: {exc}"[:300],
            )
        return self._execution(payload)

    async def get_execution(self, idempotency_key: str) -> Optional[ActionExecution]:
        """Reconcile-before-retry (REQ-062): None means the key never ran."""
        payload = await self.transport().get_json_optional(
            OBSERVE_PATHS["action_execution"].format(idempotency_key=idempotency_key)
        )
        return None if payload is None else self._execution(payload)

    @staticmethod
    def _uncertain_id(idempotency_key: str) -> str:
        """Derived from the key so the same unresolved attempt keeps one id
        across restarts and retries."""
        digest = hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()
        return f"uncertain-{digest[:24]}"

    @staticmethod
    def _execution(payload: Any) -> ActionExecution:
        try:
            return ActionExecution.model_validate(payload)
        except Exception as exc:
            raise AdapterError(
                f"/observe/actions did not return a valid ActionExecution: {exc}"
            ) from exc


class ObserveChaosAdapter(ObserveAdapter, ChaosAdapter):
    observe_path = "chaos_manifest"

    async def manifest(self) -> list[ChaosScenario]:
        document = await self.transport().get_json(OBSERVE_PATHS["chaos_manifest"])
        if not isinstance(document, dict):
            raise AdapterError("/observe/chaos must return a chaos manifest object")
        validate_chaos_manifest(document)
        return [ChaosScenario.model_validate(item) for item in document["scenarios"]]

    async def inject(
        self, scenario_id: str, params: dict[str, Any], scope: str, idempotency_key: str
    ) -> ChaosInjection:
        self._assert_drill_scope(scope)
        payload = await self.transport().post_json(
            OBSERVE_PATHS["chaos_inject"].format(scenario_id=scenario_id),
            body={
                "params": dict(params),
                "scope": scope,
                "idempotency_key": idempotency_key,
            },
        )
        return self._injection(payload)

    async def status(self, injection_id: str) -> ChaosInjection:
        payload = await self.transport().get_json(
            OBSERVE_PATHS["chaos_status"].format(injection_id=injection_id)
        )
        return self._injection(payload)

    async def abort(self, injection_id: str) -> ChaosInjection:
        payload = await self.transport().post_json(
            OBSERVE_PATHS["chaos_abort"].format(injection_id=injection_id), body={}
        )
        return self._injection(payload)

    @staticmethod
    def _assert_drill_scope(scope: str) -> None:
        """Defence in depth. The credential-scope floor already refuses chaos on
        anything but a drill credential (`assert_can_invoke`); this refuses an
        injection whose *scope string* is not a drill, so a caller cannot reach
        the target with an empty or invented authorisation.
        """
        if not (scope or "").startswith(DRILL_SCOPE_PREFIX):
            raise AdapterError(
                f"chaos injection needs a drill scope like "
                f"'{DRILL_SCOPE_PREFIX}<drill_id>', got {scope!r}"
            )

    @staticmethod
    def _injection(payload: Any) -> ChaosInjection:
        try:
            return ChaosInjection.model_validate(payload)
        except Exception as exc:
            raise AdapterError(
                f"/observe/chaos did not return a valid ChaosInjection: {exc}"
            ) from exc
