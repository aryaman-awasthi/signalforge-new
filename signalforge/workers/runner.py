"""The worker asyncio group.

Per the architecture the platform is one process with two groups: the API serves
requests, the worker runs everything scheduled - the bus dispatcher, pollers,
retention. Each job owns its own loop and its own failures: a job that raises is
recorded and retried on its next tick rather than taking the group down with it,
because a broken poller must never stop incident traffic.

The snapshot each job keeps is what /health and /metrics report, so "the worker
is alive" is an observation rather than an assumption.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Awaitable, Callable, Optional, Sequence

from signalforge.domain.identity.ports import Clock

logger = logging.getLogger("signalforge.worker")


@dataclass(frozen=True)
class ScheduledJob:
    name: str
    interval_s: float
    run: Callable[[], Awaitable[Any]]
    run_at_start: bool = True


@dataclass
class JobStatus:
    name: str
    interval_s: float
    runs: int = 0
    failures: int = 0
    last_run_at: Optional[datetime] = None
    last_error: Optional[str] = None
    last_result: Optional[str] = None

    @property
    def healthy(self) -> bool:
        return self.last_error is None

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "interval_s": self.interval_s,
            "runs": self.runs,
            "failures": self.failures,
            "last_run_at": self.last_run_at.isoformat() if self.last_run_at else None,
            "last_error": self.last_error,
            "last_result": self.last_result,
            "healthy": self.healthy,
        }


class PlatformWorker:
    def __init__(self, jobs: Sequence[ScheduledJob], clock: Clock):
        self._jobs = list(jobs)
        self._clock = clock
        self._status = {job.name: JobStatus(job.name, job.interval_s) for job in jobs}
        self._stop = asyncio.Event()
        self._tasks: list[asyncio.Task] = []

    @property
    def statuses(self) -> list[JobStatus]:
        return list(self._status.values())

    @property
    def running(self) -> bool:
        return bool(self._tasks) and not self._stop.is_set()

    def snapshot(self) -> dict[str, Any]:
        return {
            "running": self.running,
            "jobs": [status.as_dict() for status in self._status.values()],
        }

    async def start(self) -> None:
        self._stop.clear()
        self._tasks = [asyncio.create_task(self._loop(job), name=f"worker:{job.name}")
                       for job in self._jobs]

    async def stop(self) -> None:
        self._stop.set()
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._tasks = []

    async def run_job_once(self, name: str) -> JobStatus:
        """Run one job now - used by tests and by an operator forcing a sweep."""
        job = next(j for j in self._jobs if j.name == name)
        await self._run_once(job)
        return self._status[name]

    async def _loop(self, job: ScheduledJob) -> None:
        if not job.run_at_start:
            if await self._sleep(job.interval_s):
                return
        while not self._stop.is_set():
            await self._run_once(job)
            if await self._sleep(job.interval_s):
                return

    async def _run_once(self, job: ScheduledJob) -> None:
        status = self._status[job.name]
        try:
            result = await job.run()
            status.last_error = None
            status.last_result = None if result is None else str(result)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            status.failures += 1
            status.last_error = f"{type(exc).__name__}: {exc}"[:500]
            logger.warning("worker job failed", extra={"job": job.name, "error": status.last_error})
        finally:
            status.runs += 1
            status.last_run_at = self._clock.now()

    async def _sleep(self, seconds: float) -> bool:
        """Returns True if the worker was asked to stop while sleeping."""
        try:
            await asyncio.wait_for(self._stop.wait(), timeout=seconds)
            return True
        except asyncio.TimeoutError:
            return False
