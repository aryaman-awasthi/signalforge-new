"""SQLAlchemy models for `rollups.db` - the second database (S4-T01).

A separate `DeclarativeBase`, a separate engine and a separate connection
string, for reasons that are about failure rather than about tidiness:

- **Write rate.** The rollup loop writes every five seconds per application. The
  incident database carries the audit hash chain, whose append is a read of the
  previous tip inside the same transaction; putting a 5-second telemetry loop on
  that transaction would make the chain the contended resource in the system.
- **Recoverability.** Everything here is derived. Rollups are re-read from the
  integrated system, signal states are recomputed from rollups, probe results
  are re-run. Deleting `rollups.db` costs the detector its recent history and
  costs the incident record nothing - which is exactly what makes it safe to
  prune aggressively.
- **Retention.** These tables age out in hours. The incident plane is kept and
  sealed. Two lifetimes in one file is how the wrong thing gets deleted.

There is no Alembic revision for this database. Alembic's environment targets
the incident database's metadata, and a derived store whose entire contents can
be rebuilt is created from its models at startup (`create_rollup_schema`) - a
migration that could only ever be "drop and refill" is ceremony, not safety.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import (
    Boolean,
    Float,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from signalforge.infrastructure.persistence.models import TZDateTime


class RollupBase(DeclarativeBase):
    """Metadata for `rollups.db`. Deliberately not `models.Base`: two bases is
    how two databases stay two databases."""


class MetricRollupRow(RollupBase):
    """One mapped signal, one region, one five-second bucket.

    The unique constraint is the idempotency: a bucket is one observation, so a
    poller tick that overlaps the next, a restart mid-sweep, or an operator
    forcing an evaluation cannot turn one scrape into two samples and move the
    baseline the detector is about to compare against.
    """

    __tablename__ = "metric_rollups"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "app_id", "signal_key", "region", "bucket_start",
            name="uq_metric_rollups_bucket",
        ),
        Index(
            "ix_metric_rollups_series",
            "tenant_id", "app_id", "signal_key", "region", "bucket_start",
        ),
        Index("ix_metric_rollups_app_time", "tenant_id", "app_id", "bucket_start"),
        Index("ix_metric_rollups_age", "bucket_start"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(36))
    app_id: Mapped[str] = mapped_column(String(100))
    signal_key: Mapped[str] = mapped_column(String(64))
    region: Mapped[str] = mapped_column(String(64), default="")
    family: Mapped[str] = mapped_column(String(20))
    bucket_start: Mapped[datetime] = mapped_column(TZDateTime)
    samples: Mapped[int] = mapped_column(Integer, default=1)
    mean: Mapped[float] = mapped_column(Float)
    minimum: Mapped[float] = mapped_column(Float)
    maximum: Mapped[float] = mapped_column(Float)
    last: Mapped[float] = mapped_column(Float)
    series_count: Mapped[int] = mapped_column(Integer, default=1)


class SignalStateRow(RollupBase):
    """What the detector believes about one (signal, region), durably.

    The breach streak lives here rather than in memory so a restart during a
    developing incident resumes counting instead of starting again at zero and
    adding three evaluations to the time-to-detection.
    """

    __tablename__ = "signal_states"
    __table_args__ = (
        Index("ix_signal_states_app", "tenant_id", "app_id"),
        Index("ix_signal_states_status", "tenant_id", "status"),
    )

    tenant_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    app_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    signal_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    region: Mapped[str] = mapped_column(String(64), primary_key=True, default="")
    family: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(20))
    streak: Mapped[int] = mapped_column(Integer, default=0)
    value: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    basis: Mapped[str] = mapped_column(String(10), default="none")
    baseline_mean: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    baseline_stddev: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    baseline_source: Mapped[str] = mapped_column(String(20), default="")
    threshold: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    deviation: Mapped[float] = mapped_column(Float, default=0.0)
    samples: Mapped[int] = mapped_column(Integer, default=0)
    first_breach_at: Mapped[Optional[datetime]] = mapped_column(TZDateTime, nullable=True)
    anomalous_since: Mapped[Optional[datetime]] = mapped_column(TZDateTime, nullable=True)
    last_value_at: Mapped[Optional[datetime]] = mapped_column(TZDateTime, nullable=True)
    evaluated_at: Mapped[Optional[datetime]] = mapped_column(TZDateTime, nullable=True)
    detail: Mapped[str] = mapped_column(Text, default="")


class DetectionSnapshotRow(RollupBase):
    """The last verdict per application, in the fields a transition is decided
    from. One row, overwritten every evaluation: it is a cache of a conclusion,
    not a record of an incident, and losing it costs one duplicate event."""

    __tablename__ = "detection_snapshots"

    tenant_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    app_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    stance: Mapped[str] = mapped_column(String(20))
    severity: Mapped[Optional[str]] = mapped_column(String(4), nullable=True)
    health_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    health_band: Mapped[str] = mapped_column(String(20), default="unknown")
    corroborated: Mapped[bool] = mapped_column(Boolean, default=False)
    burn_verdict: Mapped[str] = mapped_column(String(10), default="none")
    storm_fingerprint: Mapped[str] = mapped_column(String(32), default="")
    suppressed: Mapped[int] = mapped_column(Integer, default=0)
    anomalous_signals: Mapped[int] = mapped_column(Integer, default=0)
    evaluated_at: Mapped[datetime] = mapped_column(TZDateTime)
    detail: Mapped[str] = mapped_column(Text, default="")


class ProbeResultRow(RollupBase):
    """One synthetic probe run, per journey and per region.

    `verdict` has three values and `unknown` is one of them: a probe that could
    not be run is not a probe that failed, and the unique constraint means a
    runner tick replayed against the same instant is a duplicate rather than a
    second data point.
    """

    __tablename__ = "probe_results"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "app_id", "probe_id", "region", "at",
            name="uq_probe_results_run",
        ),
        Index("ix_probe_results_app_time", "tenant_id", "app_id", "at"),
        Index("ix_probe_results_age", "at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(36))
    app_id: Mapped[str] = mapped_column(String(100))
    probe_id: Mapped[str] = mapped_column(String(120))
    journey_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    region: Mapped[str] = mapped_column(String(64), default="")
    verdict: Mapped[str] = mapped_column(String(10))
    status_code: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    latency_ms: Mapped[float] = mapped_column(Float, default=0.0)
    at: Mapped[datetime] = mapped_column(TZDateTime)
    detail: Mapped[str] = mapped_column(Text, default="")
    synthetic: Mapped[bool] = mapped_column(Boolean, default=True)
