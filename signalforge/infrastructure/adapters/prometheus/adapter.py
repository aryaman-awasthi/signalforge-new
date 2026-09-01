"""Prometheus HTTP API adapter (REQ-031).

This is the range-query adapter. `/observe/metrics` is a scrape and can only
ever answer "what is the value now"; baselines, burn rates and every chart in
the incident timeline need "what were the values between then and now", and that
is `/api/v1/query_range`.

Only the two query endpoints are used, and the response is translated into
canonical `MetricSeries` - the platform above never sees a Prometheus shape, so
swapping this for a vendor metrics adapter changes nothing upstream. Prometheus'
own error envelope (`status: error`) is translated into the frozen taxonomy
rather than surfacing as an empty result, because "the query was wrong" and
"there is no data" are answers a responder must not confuse.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any

from signalforge.contracts.canonical import MetricPoint, MetricSeries
from signalforge.contracts.errors import AdapterError
from signalforge.contracts.spi import MetricsAdapter
from signalforge.infrastructure.adapters.base import GenericAdapter

QUERY_PATH = "/api/v1/query"
QUERY_RANGE_PATH = "/api/v1/query_range"

MAX_POINTS_PER_QUERY = 11_000
"""Prometheus' own ceiling on (end-start)/step. Checked before the call so an
over-wide range fails with a message that says what to change, rather than with
a 400 from the server."""


class PrometheusAdapter(GenericAdapter, MetricsAdapter):
    async def ping(self) -> bool:
        payload = await self.transport().get_json(QUERY_PATH, params={"query": "1"})
        return isinstance(payload, dict) and payload.get("status") == "success"

    async def query_instant(self, query: str, at: datetime) -> list[MetricSeries]:
        payload = await self.transport().get_json(
            QUERY_PATH, params={"query": query, "time": _unix(at)}
        )
        return self.truncate(self._series(payload, query))

    async def query_range(
        self, query: str, start: datetime, end: datetime, step_s: float
    ) -> list[MetricSeries]:
        if step_s <= 0:
            raise AdapterError("query_range needs a step greater than zero")
        if end < start:
            raise AdapterError("query_range end is before its start")
        points = (end - start).total_seconds() / step_s
        if points > MAX_POINTS_PER_QUERY:
            raise AdapterError(
                f"{int(points)} points requested; Prometheus resolves at most "
                f"{MAX_POINTS_PER_QUERY} - widen the step or narrow the window"
            )
        payload = await self.transport().get_json(
            QUERY_RANGE_PATH,
            params={
                "query": query,
                "start": _unix(start),
                "end": _unix(end),
                "step": step_s,
            },
        )
        return self.truncate(self._series(payload, query))

    # -- translation -------------------------------------------------------

    def _series(self, payload: Any, query: str) -> list[MetricSeries]:
        if not isinstance(payload, dict):
            raise AdapterError("Prometheus did not return a JSON object")
        if payload.get("status") != "success":
            raise AdapterError(
                f"Prometheus refused {query!r}: "
                f"{payload.get('errorType', 'error')}: {payload.get('error', '')}"[:300]
            )
        data = payload.get("data") or {}
        result_type = data.get("resultType")
        if result_type not in ("vector", "matrix"):
            raise AdapterError(
                f"Prometheus returned resultType {result_type!r}; this adapter reads "
                "instant vectors and range matrices"
            )
        series: list[MetricSeries] = []
        for entry in data.get("result") or []:
            labels = dict(entry.get("metric") or {})
            name = labels.pop("__name__", "")
            raw_points = (
                [entry.get("value")] if result_type == "vector" else entry.get("values") or []
            )
            points = [_point(raw) for raw in raw_points if raw]
            series.append(
                MetricSeries(
                    name=name or query,
                    labels={key: str(value) for key, value in labels.items()},
                    points=points,
                )
            )
        return series


def _unix(moment: datetime) -> float:
    return moment.timestamp()


def _point(raw: Any) -> MetricPoint:
    try:
        timestamp, value = raw[0], raw[1]
    except (TypeError, IndexError) as exc:
        raise AdapterError(f"malformed Prometheus sample {raw!r}") from exc
    text = str(value).strip().lower()
    if text in ("nan", "+inf", "-inf", "inf"):
        numeric = {"nan": math.nan, "+inf": math.inf, "inf": math.inf, "-inf": -math.inf}[text]
    else:
        try:
            numeric = float(value)
        except (TypeError, ValueError) as exc:
            raise AdapterError(f"non-numeric Prometheus sample value {value!r}") from exc
    return MetricPoint(
        ts=datetime.fromtimestamp(float(timestamp), tz=timezone.utc), value=numeric
    )
