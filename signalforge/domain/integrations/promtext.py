"""Prometheus text exposition parsing and metric selection (REQ-031).

`/observe/metrics` is frozen as Prometheus text exposition
(`contracts/observe.py`), so every application that onboards speaks this format
and exactly one piece of code has to understand it. It lives in the domain
because it is a pure translation between a wire format and the canonical
`MetricSeries` - no I/O, no vendor client, and therefore testable against the
awkward lines rather than against a server.

The selector is a deliberate subset of PromQL's instant-vector selector:

    http_requests_total
    http_requests_total{route="/checkout"}
    http_requests_total{route!="/health",region=~"eu-.*"}

Matchers `=`, `!=`, `=~` and `!~` are supported and nothing else. A query that
uses more than that is rejected with a message saying so, because an adapter
that silently ignores half a filter returns the wrong data confidently - which
is worse than returning none.
"""

from __future__ import annotations

import math
import re
from datetime import datetime
from typing import Optional, Sequence

from signalforge.contracts.canonical import MetricPoint, MetricSeries
from signalforge.contracts.errors import AdapterError

_SAMPLE = re.compile(
    r"^(?P<name>[a-zA-Z_:][a-zA-Z0-9_:]*)"
    r"(?:\{(?P<labels>.*)\})?"
    r"\s+(?P<value>[^\s]+)"
    r"(?:\s+(?P<timestamp>-?\d+))?\s*$"
)
_LABEL = re.compile(
    r'(?P<name>[a-zA-Z_][a-zA-Z0-9_]*)\s*=\s*"(?P<value>(?:[^"\\]|\\.)*)"\s*,?'
)
_SELECTOR = re.compile(
    r"^\s*(?P<name>[a-zA-Z_:][a-zA-Z0-9_:]*)?\s*(?:\{(?P<matchers>.*)\})?\s*$"
)
_MATCHER = re.compile(
    r'(?P<name>[a-zA-Z_][a-zA-Z0-9_]*)\s*(?P<op>=~|!~|!=|=)\s*"(?P<value>(?:[^"\\]|\\.)*)"\s*,?'
)

_SPECIAL_VALUES = {"+inf": math.inf, "-inf": -math.inf, "inf": math.inf, "nan": math.nan}


def _unescape(value: str) -> str:
    return (
        value.replace("\\\\", "\x00")
        .replace('\\"', '"')
        .replace("\\n", "\n")
        .replace("\x00", "\\")
    )


def parse_value(raw: str) -> float:
    lowered = raw.strip().lower()
    if lowered in _SPECIAL_VALUES:
        return _SPECIAL_VALUES[lowered]
    try:
        return float(raw)
    except ValueError as exc:
        raise AdapterError(f"not a Prometheus sample value: {raw!r}") from exc


def parse_exposition(text: str, at: datetime, *, max_rows: int = 50_000) -> list[MetricSeries]:
    """Parse text exposition into one MetricSeries per (name, label set).

    `at` is the observation time the caller measured; a sample carrying its own
    millisecond timestamp keeps that instead, because an exporter that dates its
    own data knows better than the poller. Comments, `# HELP`/`# TYPE` lines and
    blank lines are skipped; a malformed sample line raises rather than being
    dropped, since a metrics feed that quietly loses rows is a detection bug
    waiting to happen.
    """
    series: dict[tuple[str, tuple[tuple[str, str], ...]], MetricSeries] = {}
    rows = 0
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        match = _SAMPLE.match(line)
        if match is None:
            raise AdapterError(f"malformed Prometheus exposition line: {line[:120]!r}")
        rows += 1
        if rows > max_rows:
            raise AdapterError(
                f"metrics response exceeds the adapter's max_rows limit ({max_rows})"
            )
        labels = parse_labels(match.group("labels"))
        key = (match.group("name"), tuple(sorted(labels.items())))
        timestamp = match.group("timestamp")
        point = MetricPoint(
            ts=(
                datetime.fromtimestamp(int(timestamp) / 1000.0, tz=at.tzinfo)
                if timestamp
                else at
            ),
            value=parse_value(match.group("value")),
        )
        existing = series.get(key)
        if existing is None:
            series[key] = MetricSeries(
                name=match.group("name"), labels=labels, points=[point]
            )
        else:
            existing.points.append(point)
    return list(series.values())


def parse_labels(raw: Optional[str]) -> dict[str, str]:
    if not raw or not raw.strip():
        return {}
    labels: dict[str, str] = {}
    consumed = 0
    for match in _LABEL.finditer(raw):
        labels[match.group("name")] = _unescape(match.group("value"))
        consumed = match.end()
    if raw[consumed:].strip():
        raise AdapterError(f"malformed Prometheus label set: {raw[:120]!r}")
    return labels


class Selector:
    """A parsed metric selector. `matches` is pure and total."""

    def __init__(self, name: Optional[str], matchers: Sequence[tuple[str, str, str]]):
        self.name = name
        self.matchers = tuple(matchers)
        self._compiled = {
            (label, op, value): re.compile(f"^(?:{value})$")
            for label, op, value in matchers
            if op in ("=~", "!~")
        }

    def matches(self, series: MetricSeries) -> bool:
        if self.name is not None and series.name != self.name:
            return False
        for label, op, value in self.matchers:
            actual = series.labels.get(label, "")
            if op == "=" and actual != value:
                return False
            if op == "!=" and actual == value:
                return False
            if op == "=~" and not self._compiled[(label, op, value)].match(actual):
                return False
            if op == "!~" and self._compiled[(label, op, value)].match(actual):
                return False
        return True


def parse_selector(query: str) -> Selector:
    text = (query or "").strip()
    if not text:
        # An empty selector is every series. Explicit, because "give me
        # everything" is a real onboarding question and guessing is not.
        return Selector(None, ())
    match = _SELECTOR.match(text)
    if match is None or (not match.group("name") and not match.group("matchers")):
        raise AdapterError(
            f"{query!r} is not a supported metric selector; this adapter accepts "
            'name{label="value"} with =, !=, =~ and !~'
        )
    matchers: list[tuple[str, str, str]] = []
    raw_matchers = match.group("matchers")
    if raw_matchers and raw_matchers.strip():
        consumed = 0
        for matcher in _MATCHER.finditer(raw_matchers):
            matchers.append(
                (
                    matcher.group("name"),
                    matcher.group("op"),
                    _unescape(matcher.group("value")),
                )
            )
            consumed = matcher.end()
        if raw_matchers[consumed:].strip():
            raise AdapterError(
                f"unsupported matcher in {query!r}; this adapter accepts =, !=, =~ and !~"
            )
    try:
        selector = Selector(match.group("name"), matchers)
    except re.error as exc:
        raise AdapterError(f"invalid regex matcher in {query!r}: {exc}") from exc
    return selector


def select(series: Sequence[MetricSeries], query: str) -> list[MetricSeries]:
    selector = parse_selector(query)
    return [one for one in series if selector.matches(one)]
