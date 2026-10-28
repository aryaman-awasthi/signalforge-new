"""The worker group beside the API: jobs tick, a failing job is contained and
observable rather than fatal (REQ-025, REQ-023)."""

from __future__ import annotations

import asyncio

import pytest

from signalforge.tests.helpers import FakeClock
from signalforge.workers.runner import PlatformWorker, ScheduledJob

pytestmark = pytest.mark.anyio


@pytest.mark.req("REQ-025")
async def test_jobs_run_on_their_interval_and_report_status():
    ticks = []

    async def job():
        ticks.append(1)
        return f"ran {len(ticks)}"

    worker = PlatformWorker(
        [ScheduledJob(name="ticker", interval_s=0.02, run=job)], clock=FakeClock()
    )
    await worker.start()
    try:
        for _ in range(50):
            if len(ticks) >= 3:
                break
            await asyncio.sleep(0.01)
    finally:
        await worker.stop()

    assert len(ticks) >= 3
    status = worker.statuses[0]
    assert status.name == "ticker" and status.healthy
    assert status.failures == 0 and status.last_result.startswith("ran ")
    assert worker.snapshot()["running"] is False


@pytest.mark.req("REQ-025")
async def test_a_failing_job_is_contained_and_visible():
    """One broken poller must not take the group - and with it incident
    traffic - down; it must be visible instead."""
    calls = {"bad": 0, "good": 0}

    async def bad():
        calls["bad"] += 1
        raise RuntimeError("upstream is down")

    async def good():
        calls["good"] += 1

    worker = PlatformWorker(
        [
            ScheduledJob(name="bad", interval_s=0.02, run=bad),
            ScheduledJob(name="good", interval_s=0.02, run=good),
        ],
        clock=FakeClock(),
    )
    await worker.start()
    try:
        for _ in range(50):
            if calls["bad"] >= 2 and calls["good"] >= 2:
                break
            await asyncio.sleep(0.01)
    finally:
        await worker.stop()

    statuses = {s.name: s for s in worker.statuses}
    assert statuses["bad"].failures >= 2
    assert statuses["bad"].last_error == "RuntimeError: upstream is down"
    assert not statuses["bad"].healthy
    assert statuses["good"].healthy and calls["good"] >= 2, "the healthy job kept ticking"


@pytest.mark.req("REQ-023", "REQ-025")
async def test_the_retention_sweep_is_a_scheduled_job(api):
    """The sweep is wired into the worker, not left to be called by hand."""
    container = api.container
    jobs = {job.name for job in container._jobs()}
    assert "retention" in jobs

    status = await container.worker.run_job_once("retention")
    assert status.healthy and status.runs == 1
