"""Five-second metric rollups (REQ-040).

A rollup is one mapped golden signal, in one region, over one five-second
bucket. It is the unit everything downstream reasons about, and it exists for
three reasons that a raw scrape cannot serve:

1. **A bucket is a fixed grid.** Two signals sampled in the same bucket are
   comparable even though the polls that produced them were milliseconds apart,
   so "errors rose while the business counter fell" is a statement about the
   same instant rather than about two adjacent ones.
2. **A bucket is idempotent.** The poller is a loop on a five-second timer; a
   slow tick, a restart, or an operator forcing a sweep can land two polls
   inside one bucket. The bucket is the identity, so the second one is a
   duplicate rather than a second observation - and a duplicated poll must not
   be able to move a baseline.
3. **Rollups are cheap enough to keep.** They live in their own database
   (`rollups.db`) because they are high-frequency derived telemetry: losing them
   loses no incident record, and keeping them out of the incident database keeps
   a 5-second write loop off the transaction that carries the audit chain.

Nothing here is a rate. Whether a series is a level or a counter is decided in
`baselines.py` from the shape of the window, because the operator's mapping says
which way is bad and nothing else in the frozen contract says whether a number
accumulates.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from typing import Iterable, Optional, Sequence

from signalforge.contracts.canonical import MetricSeries
from signalforge.domain.detection.errors import DetectionValidationError
from signalforge.domain.integrations.promtext import select
from signalforge.domain.onboarding.profile import GoldenSignal, SignalMapping

ROLLUP_INTERVAL_S = 5
"""The architecture's poller cadence. Also the bucket width, deliberately: a
bucket wider than the poll would average two polls into one number and a bucket
narrower than the poll would leave holes the baseline has to interpolate."""

MAX_ROLLUP_WINDOW_S = 24 * 3_600
"""The widest window anything may ask the store for. A detection question is
about minutes; a day is already generous, and an unbounded window is a way to
make one API call read the whole database."""

ROLLUP_RETENTION_S = 6 * 3_600
"""How long a rollup is kept. Long enough for the slow burn window (30 minutes)
several times over, short enough that a five-second loop over twenty signals is
a bounded table. Rollups are derived: pruning them loses no record of anything
that happened, only the ability to re-derive a baseline from before the cut."""

NO_REGION = ""
"""What the region column holds when the application declares no region label,
or when a series carries no value for it. Empty rather than NULL so it is part
of the primary key without three-valued logic."""

MAX_REGION_CHARS = 64


def bucket_start(at: datetime, interval_s: int = ROLLUP_INTERVAL_S) -> datetime:
    """Floor a timestamp onto the rollup grid, in UTC.

    Floor rather than round, so a bucket contains only observations that have
    already happened: a rounded-up bucket would be stamped in the future and a
    reader asking for "the last minute" would miss it.
    """
    if at.tzinfo is None:
        raise DetectionValidationError("rollup timestamps must be timezone-aware")
    if interval_s <= 0:
        raise DetectionValidationError("the rollup interval must be positive")
    moment = at.astimezone(timezone.utc)
    epoch = int(moment.timestamp())
    return datetime.fromtimestamp(epoch - (epoch % interval_s), tz=timezone.utc)


@dataclass(frozen=True)
class MetricRollup:
    """One signal, one region, one bucket.

    `samples` and `series_count` are kept beside the statistics for the same
    reason a baseline keeps its window: a number derived from one series in one
    scrape and a number derived from forty are not equally trustworthy, and the
    reader should be able to tell without asking.
    """

    tenant_id: str
    app_id: str
    signal_key: str
    region: str
    family: GoldenSignal
    bucket_start: datetime
    samples: int
    mean: float
    minimum: float
    maximum: float
    last: float
    series_count: int = 1

    def __post_init__(self) -> None:
        if not (self.tenant_id or "").strip():
            raise DetectionValidationError("a rollup belongs to a tenant")
        if not (self.app_id or "").strip():
            raise DetectionValidationError("a rollup belongs to an application")
        if not (self.signal_key or "").strip():
            raise DetectionValidationError("a rollup belongs to a mapped signal")
        if int(self.samples) < 1:
            raise DetectionValidationError(
                f"rollup {self.signal_key!r} has no samples; a bucket with nothing "
                "in it is absence, and absence is not written"
            )
        if self.bucket_start != bucket_start(self.bucket_start):
            raise DetectionValidationError(
                f"rollup {self.signal_key!r} is stamped {self.bucket_start.isoformat()}, "
                f"which is not aligned to the {ROLLUP_INTERVAL_S}s grid"
            )
        object.__setattr__(self, "region", (self.region or NO_REGION)[:MAX_REGION_CHARS])
        for name in ("mean", "minimum", "maximum", "last"):
            value = float(getattr(self, name))
            if math.isnan(value):
                raise DetectionValidationError(
                    f"rollup {self.signal_key!r}: {name} is NaN. NaN is not a "
                    "measurement and is dropped when the bucket is folded"
                )
            object.__setattr__(self, name, value)

    @property
    def key(self) -> tuple[str, str]:
        return (self.signal_key, self.region)

    def with_family(self, family: GoldenSignal) -> "MetricRollup":
        return replace(self, family=family)


def fold_bucket(
    series: Sequence[MetricSeries],
    signal: SignalMapping,
    *,
    tenant_id: str,
    app_id: str,
    region_label: Optional[str],
    at: datetime,
) -> tuple[MetricRollup, ...]:
    """One scrape, one signal, into one rollup per region.

    The whole exposition is selected locally rather than queried per signal,
    because `/observe/metrics` is a point-in-time exposition: one HTTP call
    answers every mapped signal, and every signal in the result is then measured
    at the same instant.

    NaN is dropped rather than folded. Prometheus uses NaN for "there is a
    series here but no value right now", and averaging that into a baseline
    would poison every comparison made against it afterwards. A region whose
    only samples were NaN produces no rollup at all, which the evaluator then
    sees as missing data rather than as health.
    """
    matched = select(list(series), signal.selector)
    bucket = bucket_start(at)
    grouped: dict[str, list[float]] = {}
    counted: dict[str, int] = {}
    for one in matched:
        region = _region_of(one, region_label)
        counted[region] = counted.get(region, 0) + 1
        values = grouped.setdefault(region, [])
        values.extend(
            point.value
            for point in one.points
            if not math.isnan(point.value) and not math.isinf(point.value)
        )
    rollups: list[MetricRollup] = []
    for region in sorted(grouped):
        values = grouped[region]
        if not values:
            continue
        rollups.append(
            MetricRollup(
                tenant_id=tenant_id,
                app_id=app_id,
                signal_key=signal.key,
                region=region,
                family=signal.signal,
                bucket_start=bucket,
                samples=len(values),
                mean=sum(values) / len(values),
                minimum=min(values),
                maximum=max(values),
                last=values[-1],
                series_count=counted[region],
            )
        )
    return tuple(rollups)


def _region_of(series: MetricSeries, region_label: Optional[str]) -> str:
    if not region_label:
        return NO_REGION
    return (series.labels.get(region_label) or NO_REGION)[:MAX_REGION_CHARS]


def group_by_key(
    rollups: Iterable[MetricRollup],
) -> dict[tuple[str, str], tuple[MetricRollup, ...]]:
    """Rollups keyed by (signal, region), each series in bucket order.

    Sorted here rather than trusted from the store, because a baseline computed
    over an unordered window is a baseline over a different window.
    """
    grouped: dict[tuple[str, str], list[MetricRollup]] = {}
    for rollup in rollups:
        grouped.setdefault(rollup.key, []).append(rollup)
    return {
        key: tuple(sorted(values, key=lambda r: r.bucket_start))
        for key, values in grouped.items()
    }


def window_bounds(at: datetime, window_s: float) -> tuple[datetime, datetime]:
    """The half-open [start, end) a window of `window_s` ending now covers."""
    seconds = float(window_s)
    if not (0 < seconds <= MAX_ROLLUP_WINDOW_S):
        raise DetectionValidationError(
            f"a rollup window must be between 0 and {MAX_ROLLUP_WINDOW_S} seconds, "
            f"got {window_s}"
        )
    end = bucket_start(at) + timedelta(seconds=ROLLUP_INTERVAL_S)
    return end - timedelta(seconds=seconds), end
