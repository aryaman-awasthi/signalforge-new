"""The detection pollers and the probe runner, as scheduled jobs (S4-T01).

`workers/` is the scheduling layer: it owns *when* things run and nothing about
*what* they do. The poller, the probe runner and the evaluator are application
services; this module turns them into `ScheduledJob`s with the cadences the
architecture specifies, so a reader can see the whole timetable in one place and
so the layering holds - a worker calls the application, never the domain and
never an adapter.

    metric-rollups   every 5s    scrape once, fold into five-second buckets
    detection        every 5s    evaluate the buckets and announce transitions
    probes           every 30s   run each journey probe in each region
    rollup-retention every 1h    age out rollups and probe results

Poll and evaluate are two jobs rather than one, because `PlatformWorker` gives
each job its own failure isolation: an application whose profile will not
evaluate must not be able to stop the scrape that feeds every other
application's baseline. They share a cadence and are idempotent in both
directions - an evaluation with no new rollup re-reads the same window and
reaches the same verdict, and a scrape that lands in a bucket that already
exists is a duplicate rather than a second sample.

The probe cadence is deliberately six times slower than the metric one: a
synthetic probe is real traffic against somebody's production system, and
detection that DDoSes the application it is watching is not detection.
"""

from __future__ import annotations

from typing import Sequence

from signalforge.application.detection.poller import MetricRollupPoller
from signalforge.application.detection.probes import ProbeRunner
from signalforge.application.detection.service import DetectionService
from signalforge.workers.runner import ScheduledJob

ROLLUP_JOB = "metric-rollups"
DETECTION_JOB = "detection"
PROBE_JOB = "probes"
ROLLUP_RETENTION_JOB = "rollup-retention"

RETENTION_INTERVAL_S = 3_600.0


def detection_jobs(
    poller: MetricRollupPoller,
    probes: ProbeRunner,
    detection: DetectionService,
    *,
    rollup_interval_s: float,
    detection_interval_s: float,
    probe_interval_s: float,
    retention_s: float,
    retention_interval_s: float = RETENTION_INTERVAL_S,
) -> list[ScheduledJob]:
    """The four jobs, in the order they matter.

    `run_at_start` is False for the probe runner: a process restart must not
    fire every journey probe of every application at the same instant, which is
    a synchronised burst against exactly the systems already being restarted.
    Rollups and evaluation do run at start, because they read rather than write
    to somebody else's system and because starting cold with no rollup means
    fifteen seconds of blindness.
    """

    async def prune() -> str:
        removed = await detection.prune(retention_s)
        return f"{removed} row(s) aged out"

    return [
        ScheduledJob(
            name=ROLLUP_JOB,
            interval_s=rollup_interval_s,
            run=lambda: _described(poller.poll_all()),
        ),
        ScheduledJob(
            name=DETECTION_JOB,
            interval_s=detection_interval_s,
            run=lambda: _described(detection.evaluate_all()),
        ),
        ScheduledJob(
            name=PROBE_JOB,
            interval_s=probe_interval_s,
            run=lambda: _described(probes.run_all()),
            run_at_start=False,
        ),
        ScheduledJob(
            name=ROLLUP_RETENTION_JOB,
            interval_s=retention_interval_s,
            run=prune,
            run_at_start=False,
        ),
    ]


def job_names() -> Sequence[str]:
    return (ROLLUP_JOB, DETECTION_JOB, PROBE_JOB, ROLLUP_RETENTION_JOB)


async def _described(awaitable):
    """The worker records `str(result)` as a job's last result, so each sweep
    returns something a person reading /health can understand."""
    return str(await awaitable)
