"""Detection ports (CLAUDE.md rule 1).

Three stores, all of them backed by `rollups.db` rather than by the incident
database. That separation is the point of the port existing: rollups, signal
states and probe results are high-frequency **derived** telemetry, they are
rebuilt from the integrated system rather than recovered from a backup, and a
five-second write loop has no business sharing a transaction with the audit hash
chain.

Tenant isolation is the implementation's job (REQ-013): every method here takes
`tenant_id` and the predicate is applied inside the repository. There is one
deliberate cross-tenant method - `prune`, the retention sweep's work list -
which names no tenant because it removes by age across the whole install, and
returns a count rather than rows.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Optional, Sequence

from signalforge.domain.detection.anomaly import SignalState
from signalforge.domain.detection.probes import ProbeRecord
from signalforge.domain.detection.rollups import MetricRollup
from signalforge.domain.detection.snapshot import DetectionSnapshot


class RollupStore(ABC):
    @abstractmethod
    async def append(self, rollups: Sequence[MetricRollup]) -> tuple[int, int]:
        """Write a batch. Returns (written, duplicates).

        A bucket is one observation of one (signal, region): the first write for
        a bucket wins and a second one is a duplicate, not a second sample. That
        is what makes the poller safe to run twice - a slow tick that overlaps
        the next, a restart, an operator forcing a sweep - without moving a
        baseline the detector is about to compare against.
        """

    @abstractmethod
    async def window(
        self,
        tenant_id: str,
        app_id: str,
        start: datetime,
        end: datetime,
        *,
        signal_key: Optional[str] = None,
        region: Optional[str] = None,
        limit: int = 20_000,
    ) -> Sequence[MetricRollup]:
        """Rollups in [start, end), oldest first."""

    @abstractmethod
    async def latest_bucket(self, tenant_id: str, app_id: str) -> Optional[datetime]: ...

    @abstractmethod
    async def prune(self, before: datetime) -> int:
        """Cross-tenant by design: retention removes by age across the install."""


class SignalStateStore(ABC):
    @abstractmethod
    async def list(self, tenant_id: str, app_id: str) -> Sequence[SignalState]: ...

    @abstractmethod
    async def put_many(self, states: Sequence[SignalState]) -> int:
        """Insert or replace, keyed on (tenant, app, signal, region).

        The whole set is written every evaluation, including the states that did
        not change, because how fresh a belief is is part of the belief: "healthy
        as of four minutes ago" and "healthy as of now" are different facts.
        """

    @abstractmethod
    async def delete_for(self, tenant_id: str, app_id: str) -> int:
        """Everything the detector believes about one application. Called when
        the application is removed, and never otherwise."""


class ProbeResultStore(ABC):
    @abstractmethod
    async def append(self, results: Sequence[ProbeRecord]) -> int: ...

    @abstractmethod
    async def window(
        self,
        tenant_id: str,
        app_id: str,
        start: datetime,
        end: datetime,
        *,
        limit: int = 5_000,
    ) -> Sequence[ProbeRecord]: ...

    @abstractmethod
    async def prune(self, before: datetime) -> int: ...


class SnapshotStore(ABC):
    @abstractmethod
    async def get(self, tenant_id: str, app_id: str) -> Optional[DetectionSnapshot]: ...

    @abstractmethod
    async def put(self, snapshot: DetectionSnapshot) -> None:
        """One row per application, overwritten. What it is *for* is deciding
        whether the current verdict differs from the last one, so that a
        five-second loop publishes transitions rather than a heartbeat."""

    @abstractmethod
    async def delete_for(self, tenant_id: str, app_id: str) -> int: ...


class DetectionUnitOfWork(ABC):
    """One session over `rollups.db`, holding all four stores.

    Separate from the identity unit of work on purpose: an adapter poll is not a
    use case, and the transaction that stores five seconds of telemetry must not
    be able to carry identity or incident state with it.
    """

    rollups: RollupStore
    states: SignalStateStore
    probes: ProbeResultStore
    snapshots: SnapshotStore

    @abstractmethod
    async def __aenter__(self) -> "DetectionUnitOfWork": ...

    @abstractmethod
    async def __aexit__(self, exc_type, exc, tb) -> None: ...

    @abstractmethod
    async def commit(self) -> None: ...
