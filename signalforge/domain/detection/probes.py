"""Synthetic probe results, per journey and per region (REQ-040).

A probe is the only signal in this package that is not somebody else's
telemetry: SignalForge issues the request itself, so a probe failure is a
first-hand observation rather than a report. That is what makes it usable as the
customer-facing half of corroboration when an application's own business counter
has stopped being exported.

The verdict has three values on purpose. PASSED and FAILED are what the probe
saw; UNKNOWN is what it records when the probe could not be *run* - the health
connector was unreachable, the adapter timed out, nothing on this node can run
probes. An UNKNOWN is never counted as a failure, because "we could not check"
and "the journey is broken" lead to opposite next actions, and a severity
proposal built on the first while claiming the second is exactly the mistake
that makes an operator stop trusting the platform.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Iterable, Optional, Sequence

from signalforge.domain.detection.errors import DetectionValidationError

MAX_DETAIL_CHARS = 300


class ProbeVerdict(str, Enum):
    PASSED = "passed"
    FAILED = "failed"
    UNKNOWN = "unknown"

    @property
    def measured(self) -> bool:
        return self is not ProbeVerdict.UNKNOWN


@dataclass(frozen=True)
class ProbeRecord:
    tenant_id: str
    app_id: str
    probe_id: str
    region: str
    verdict: ProbeVerdict
    at: datetime
    journey_id: Optional[str] = None
    status_code: Optional[int] = None
    latency_ms: float = 0.0
    detail: str = ""

    def __post_init__(self) -> None:
        if not (self.probe_id or "").strip():
            raise DetectionValidationError("a probe result names its probe")
        if self.at.tzinfo is None:
            raise DetectionValidationError("probe timestamps must be timezone-aware")
        object.__setattr__(self, "region", self.region or "")
        object.__setattr__(self, "detail", (self.detail or "")[:MAX_DETAIL_CHARS])
        object.__setattr__(self, "latency_ms", max(0.0, float(self.latency_ms)))

    @property
    def evidence_ref(self) -> str:
        region = self.region or "-"
        return f"probe:{self.probe_id}/{region}@{self.at.isoformat()}"


@dataclass(frozen=True)
class JourneyHealth:
    """One journey in one region, over the evaluation window."""

    journey_id: Optional[str]
    region: str
    passed: int
    failed: int
    unknown: int
    tier: Optional[int] = None
    last_at: Optional[datetime] = None
    last_detail: str = ""

    @property
    def measured(self) -> int:
        return self.passed + self.failed

    @property
    def success_rate_pct(self) -> Optional[float]:
        """None when nothing was measured. Not 100, and not 0: an unrun probe is
        not a passing probe and is not a failing one either."""
        if self.measured == 0:
            return None
        return 100.0 * self.passed / self.measured

    @property
    def failing(self) -> bool:
        return self.failed > 0


def journey_health(
    records: Iterable[ProbeRecord],
    *,
    tiers: Optional[dict[str, int]] = None,
) -> tuple[JourneyHealth, ...]:
    """Fold probe results into one row per (journey, region).

    Probes with no journey are kept under `journey_id=None` rather than dropped:
    an availability probe on the front door measures no particular journey and
    is still the clearest evidence that customers cannot get in.
    """
    tiers = tiers or {}
    buckets: dict[tuple[Optional[str], str], list[ProbeRecord]] = {}
    for record in records:
        buckets.setdefault((record.journey_id, record.region), []).append(record)
    rows: list[JourneyHealth] = []
    for (journey_id, region), items in buckets.items():
        ordered = sorted(items, key=lambda record: record.at)
        failures = [r for r in ordered if r.verdict is ProbeVerdict.FAILED]
        rows.append(
            JourneyHealth(
                journey_id=journey_id,
                region=region,
                passed=sum(1 for r in ordered if r.verdict is ProbeVerdict.PASSED),
                failed=len(failures),
                unknown=sum(1 for r in ordered if r.verdict is ProbeVerdict.UNKNOWN),
                tier=tiers.get(journey_id or ""),
                last_at=ordered[-1].at,
                # A failing journey's detail describes its most recent failure,
                # not whichever probe happened to run last: the moment a probe
                # passed after a run of refusals, the refusal's words ("403
                # LICENSE_EXPIRED") vanished from the evidence while the row
                # still counted as failing, and the brain lost the one thing
                # that named the cause.
                last_detail=(failures[-1] if failures else ordered[-1]).detail,
            )
        )
    return tuple(
        sorted(rows, key=lambda row: ((row.journey_id or ""), row.region))
    )


def worst_success_rate(rows: Sequence[JourneyHealth]) -> Optional[float]:
    measured = [row.success_rate_pct for row in rows if row.success_rate_pct is not None]
    return min(measured) if measured else None
