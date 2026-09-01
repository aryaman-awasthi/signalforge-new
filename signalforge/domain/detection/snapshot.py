"""The last verdict, in the few fields a transition is decided from.

A five-second loop that published its verdict on every tick would put seventeen
thousand events a day per application onto a timeline meant to be read by a
person. So detection publishes only what *changed*, and "changed since when" has
to survive a restart - otherwise the first tick after a deploy re-announces
every open anomaly on every application at once.

This is that memory: one row per application, holding exactly the values a
transition is decided from and nothing else. It is a cache of a conclusion, not
a record of an incident: it lives in `rollups.db` with the rollups it was
derived from, and losing it costs one duplicate announcement.

`storm_fingerprint` is a hash of the cluster ids rather than the clusters
themselves, so "the same storm, still going" and "a new cluster joined it" are
distinguishable without storing the alerts twice.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Sequence

MAX_DETAIL_CHARS = 600


def storm_fingerprint(cluster_ids: Sequence[str]) -> str:
    if not cluster_ids:
        return ""
    joined = "\n".join(sorted(cluster_ids))
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class DetectionSnapshot:
    tenant_id: str
    app_id: str
    stance: str
    evaluated_at: datetime
    severity: Optional[str] = None
    health_score: Optional[float] = None
    health_band: str = "unknown"
    corroborated: bool = False
    burn_verdict: str = "none"
    storm_fingerprint: str = ""
    suppressed: int = 0
    anomalous_signals: int = 0
    detail: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "detail", (self.detail or "")[:MAX_DETAIL_CHARS])
