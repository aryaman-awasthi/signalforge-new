"""The `rollups.db` stores (REQ-040, REQ-013).

Tenant isolation lives here, not in the callers: every read and write carries
the tenant predicate inside the repository, and a cross-tenant read returns
nothing rather than raising - the same shape as a read of an application that
does not exist. The one method that names no tenant is `prune`, which removes by
age across the install and returns a count; it is documented at its port and in
docs/10.

The idempotency of `append` is the load-bearing part. A bucket is one
observation, so the write is insert-or-count-as-duplicate rather than an upsert
that accumulates: two polls landing inside one five-second bucket must not be
able to move a baseline, and an upsert that averaged them would let a retry do
exactly that. The uniqueness is enforced twice - a lookup first, and the unique
constraint underneath it inside a savepoint - so a second writer that slips
between the two is still counted as a duplicate rather than raising.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional, Sequence

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from signalforge.domain.detection.anomaly import SignalState, SignalStatus
from signalforge.domain.detection.baselines import Basis
from signalforge.domain.detection.ports import (
    DetectionUnitOfWork,
    ProbeResultStore,
    RollupStore,
    SignalStateStore,
    SnapshotStore,
)
from signalforge.domain.detection.probes import ProbeRecord, ProbeVerdict
from signalforge.domain.detection.rollups import MetricRollup
from signalforge.domain.detection.snapshot import DetectionSnapshot
from signalforge.domain.onboarding.profile import GoldenSignal
from signalforge.infrastructure.persistence.rollup_models import (
    DetectionSnapshotRow,
    MetricRollupRow,
    ProbeResultRow,
    RollupBase,
    SignalStateRow,
)

MAX_WINDOW_ROWS = 20_000


async def create_rollup_schema(engine) -> None:
    """Create `rollups.db`'s tables if they are not there.

    Not an Alembic migration on purpose (see rollup_models.py): this database is
    derived, is pruned in hours, and can be rebuilt by letting the poller run.
    """
    async with engine.begin() as connection:
        await connection.run_sync(RollupBase.metadata.create_all)


class SqlRollupStore(RollupStore):
    def __init__(self, session: AsyncSession):
        self._s = session

    async def append(self, rollups: Sequence[MetricRollup]) -> tuple[int, int]:
        written = duplicates = 0
        for rollup in rollups:
            existing = (
                await self._s.execute(
                    select(MetricRollupRow.id).where(
                        MetricRollupRow.tenant_id == rollup.tenant_id,
                        MetricRollupRow.app_id == rollup.app_id,
                        MetricRollupRow.signal_key == rollup.signal_key,
                        MetricRollupRow.region == rollup.region,
                        MetricRollupRow.bucket_start == rollup.bucket_start,
                    )
                )
            ).scalars().first()
            if existing is not None:
                duplicates += 1
                continue
            try:
                async with self._s.begin_nested():
                    self._s.add(
                        MetricRollupRow(
                            tenant_id=rollup.tenant_id,
                            app_id=rollup.app_id,
                            signal_key=rollup.signal_key,
                            region=rollup.region,
                            family=rollup.family.value,
                            bucket_start=rollup.bucket_start,
                            samples=rollup.samples,
                            mean=rollup.mean,
                            minimum=rollup.minimum,
                            maximum=rollup.maximum,
                            last=rollup.last,
                            series_count=rollup.series_count,
                        )
                    )
            except IntegrityError:
                # Another writer got the bucket between the lookup and the
                # insert. The bucket is still one observation.
                duplicates += 1
                continue
            written += 1
        return written, duplicates

    async def window(
        self,
        tenant_id: str,
        app_id: str,
        start: datetime,
        end: datetime,
        *,
        signal_key: Optional[str] = None,
        region: Optional[str] = None,
        limit: int = MAX_WINDOW_ROWS,
    ) -> Sequence[MetricRollup]:
        statement = select(MetricRollupRow).where(
            MetricRollupRow.tenant_id == tenant_id,
            MetricRollupRow.app_id == app_id,
            MetricRollupRow.bucket_start >= start,
            MetricRollupRow.bucket_start < end,
        )
        if signal_key is not None:
            statement = statement.where(MetricRollupRow.signal_key == signal_key)
        if region is not None:
            statement = statement.where(MetricRollupRow.region == region)
        rows = (
            await self._s.execute(
                statement.order_by(MetricRollupRow.bucket_start, MetricRollupRow.id)
                .limit(max(1, min(int(limit), MAX_WINDOW_ROWS)))
            )
        ).scalars().all()
        return [_to_rollup(row) for row in rows]

    async def latest_bucket(self, tenant_id: str, app_id: str) -> Optional[datetime]:
        return (
            await self._s.execute(
                select(MetricRollupRow.bucket_start)
                .where(
                    MetricRollupRow.tenant_id == tenant_id,
                    MetricRollupRow.app_id == app_id,
                )
                .order_by(MetricRollupRow.bucket_start.desc())
                .limit(1)
            )
        ).scalars().first()

    async def prune(self, before: datetime) -> int:
        result = await self._s.execute(
            delete(MetricRollupRow).where(MetricRollupRow.bucket_start < before)
        )
        return int(result.rowcount or 0)


class SqlSignalStateStore(SignalStateStore):
    def __init__(self, session: AsyncSession):
        self._s = session

    async def list(self, tenant_id: str, app_id: str) -> Sequence[SignalState]:
        rows = (
            await self._s.execute(
                select(SignalStateRow)
                .where(
                    SignalStateRow.tenant_id == tenant_id,
                    SignalStateRow.app_id == app_id,
                )
                .order_by(SignalStateRow.signal_key, SignalStateRow.region)
            )
        ).scalars().all()
        return [_to_state(row) for row in rows]

    async def put_many(self, states: Sequence[SignalState]) -> int:
        for state in states:
            row = await self._s.get(
                SignalStateRow,
                (state.tenant_id, state.app_id, state.signal_key, state.region),
            )
            values = {
                "family": state.family.value,
                "status": state.status.value,
                "streak": state.streak,
                "value": state.value,
                "basis": state.basis.value,
                "baseline_mean": state.baseline_mean,
                "baseline_stddev": state.baseline_stddev,
                "baseline_source": state.baseline_source,
                "threshold": state.threshold,
                "deviation": state.deviation,
                "samples": state.samples,
                "first_breach_at": state.first_breach_at,
                "anomalous_since": state.anomalous_since,
                "last_value_at": state.last_value_at,
                "evaluated_at": state.evaluated_at,
                "detail": state.detail,
            }
            if row is None:
                self._s.add(
                    SignalStateRow(
                        tenant_id=state.tenant_id,
                        app_id=state.app_id,
                        signal_key=state.signal_key,
                        region=state.region,
                        **values,
                    )
                )
            else:
                for name, value in values.items():
                    setattr(row, name, value)
        await self._s.flush()
        return len(states)

    async def delete_for(self, tenant_id: str, app_id: str) -> int:
        result = await self._s.execute(
            delete(SignalStateRow).where(
                SignalStateRow.tenant_id == tenant_id,
                SignalStateRow.app_id == app_id,
            )
        )
        return int(result.rowcount or 0)


class SqlProbeResultStore(ProbeResultStore):
    def __init__(self, session: AsyncSession):
        self._s = session

    async def append(self, results: Sequence[ProbeRecord]) -> int:
        written = 0
        for record in results:
            try:
                async with self._s.begin_nested():
                    self._s.add(
                        ProbeResultRow(
                            tenant_id=record.tenant_id,
                            app_id=record.app_id,
                            probe_id=record.probe_id,
                            journey_id=record.journey_id,
                            region=record.region,
                            verdict=record.verdict.value,
                            status_code=record.status_code,
                            latency_ms=record.latency_ms,
                            at=record.at,
                            detail=record.detail,
                        )
                    )
            except IntegrityError:
                # The same probe, in the same region, at the same instant. A
                # replayed tick is not a second measurement.
                continue
            written += 1
        return written

    async def window(
        self,
        tenant_id: str,
        app_id: str,
        start: datetime,
        end: datetime,
        *,
        limit: int = 5_000,
    ) -> Sequence[ProbeRecord]:
        rows = (
            await self._s.execute(
                select(ProbeResultRow)
                .where(
                    ProbeResultRow.tenant_id == tenant_id,
                    ProbeResultRow.app_id == app_id,
                    ProbeResultRow.at >= start,
                    ProbeResultRow.at < end,
                )
                .order_by(ProbeResultRow.at, ProbeResultRow.id)
                .limit(max(1, min(int(limit), MAX_WINDOW_ROWS)))
            )
        ).scalars().all()
        return [_to_probe(row) for row in rows]

    async def prune(self, before: datetime) -> int:
        result = await self._s.execute(
            delete(ProbeResultRow).where(ProbeResultRow.at < before)
        )
        return int(result.rowcount or 0)


class SqlSnapshotStore(SnapshotStore):
    def __init__(self, session: AsyncSession):
        self._s = session

    async def get(self, tenant_id: str, app_id: str) -> Optional[DetectionSnapshot]:
        row = await self._s.get(DetectionSnapshotRow, (tenant_id, app_id))
        if row is None:
            return None
        return DetectionSnapshot(
            tenant_id=row.tenant_id,
            app_id=row.app_id,
            stance=row.stance,
            evaluated_at=row.evaluated_at,
            severity=row.severity,
            health_score=row.health_score,
            health_band=row.health_band or "unknown",
            corroborated=bool(row.corroborated),
            burn_verdict=row.burn_verdict or "none",
            storm_fingerprint=row.storm_fingerprint or "",
            suppressed=row.suppressed or 0,
            anomalous_signals=row.anomalous_signals or 0,
            detail=row.detail or "",
        )

    async def put(self, snapshot: DetectionSnapshot) -> None:
        values = {
            "stance": snapshot.stance,
            "severity": snapshot.severity,
            "health_score": snapshot.health_score,
            "health_band": snapshot.health_band,
            "corroborated": snapshot.corroborated,
            "burn_verdict": snapshot.burn_verdict,
            "storm_fingerprint": snapshot.storm_fingerprint,
            "suppressed": snapshot.suppressed,
            "anomalous_signals": snapshot.anomalous_signals,
            "evaluated_at": snapshot.evaluated_at,
            "detail": snapshot.detail,
        }
        row = await self._s.get(
            DetectionSnapshotRow, (snapshot.tenant_id, snapshot.app_id)
        )
        if row is None:
            self._s.add(
                DetectionSnapshotRow(
                    tenant_id=snapshot.tenant_id, app_id=snapshot.app_id, **values
                )
            )
        else:
            for name, value in values.items():
                setattr(row, name, value)
        await self._s.flush()

    async def delete_for(self, tenant_id: str, app_id: str) -> int:
        result = await self._s.execute(
            delete(DetectionSnapshotRow).where(
                DetectionSnapshotRow.tenant_id == tenant_id,
                DetectionSnapshotRow.app_id == app_id,
            )
        )
        return int(result.rowcount or 0)


class SqlDetectionUnitOfWork(DetectionUnitOfWork):
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]):
        self._session_factory = session_factory

    async def __aenter__(self) -> "SqlDetectionUnitOfWork":
        self._session = self._session_factory()
        self.rollups = SqlRollupStore(self._session)
        self.states = SqlSignalStateStore(self._session)
        self.probes = SqlProbeResultStore(self._session)
        self.snapshots = SqlSnapshotStore(self._session)
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if exc_type is not None:
            await self._session.rollback()
        await self._session.close()

    async def commit(self) -> None:
        await self._session.commit()


def _to_rollup(row: MetricRollupRow) -> MetricRollup:
    return MetricRollup(
        tenant_id=row.tenant_id,
        app_id=row.app_id,
        signal_key=row.signal_key,
        region=row.region or "",
        family=GoldenSignal(row.family),
        bucket_start=row.bucket_start,
        samples=row.samples,
        mean=row.mean,
        minimum=row.minimum,
        maximum=row.maximum,
        last=row.last,
        series_count=row.series_count,
    )


def _to_state(row: SignalStateRow) -> SignalState:
    return SignalState(
        tenant_id=row.tenant_id,
        app_id=row.app_id,
        signal_key=row.signal_key,
        region=row.region or "",
        family=GoldenSignal(row.family),
        status=SignalStatus(row.status),
        streak=row.streak,
        value=row.value,
        basis=Basis(row.basis or "none"),
        baseline_mean=row.baseline_mean,
        baseline_stddev=row.baseline_stddev,
        baseline_source=row.baseline_source or "",
        threshold=row.threshold,
        deviation=row.deviation or 0.0,
        samples=row.samples or 0,
        first_breach_at=row.first_breach_at,
        anomalous_since=row.anomalous_since,
        last_value_at=row.last_value_at,
        evaluated_at=row.evaluated_at,
        detail=row.detail or "",
    )


def _to_probe(row: ProbeResultRow) -> ProbeRecord:
    return ProbeRecord(
        tenant_id=row.tenant_id,
        app_id=row.app_id,
        probe_id=row.probe_id,
        journey_id=row.journey_id,
        region=row.region or "",
        verdict=ProbeVerdict(row.verdict),
        status_code=row.status_code,
        latency_ms=row.latency_ms or 0.0,
        at=row.at,
        detail=row.detail or "",
    )
