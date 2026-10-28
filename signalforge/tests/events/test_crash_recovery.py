"""Sprint 2 acceptance: kill the consuming process mid-consumption; nothing is
lost, nothing is applied twice, order per partition survives (REQ-020, REQ-021,
REQ-022).

This is a real subprocess taking a real SIGKILL-equivalent, not a simulated
exception, because the failure being designed for is a machine going away.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from signalforge.tests.events.crash_runner import PROJECTION_TOPIC, make_dispatcher

pytestmark = pytest.mark.anyio

ROOT = Path(__file__).resolve().parents[3]
EVENT_COUNT = 40
KILL_AFTER_ROWS = 5


def _projection_rows(db_path: Path) -> list[dict]:
    conn = sqlite3.connect(db_path, timeout=10)
    try:
        rows = conn.execute(
            "select payload from audit_log where topic = ? order by seq",
            (PROJECTION_TOPIC,),
        ).fetchall()
    finally:
        conn.close()
    return [json.loads(r[0]) for r in rows]


@pytest.mark.req("REQ-020", "REQ-021", "REQ-022")
async def test_hard_kill_mid_consumption_loses_nothing(bus):
    published = []
    for i in range(EVENT_COUNT):
        incident = "inc-a" if i % 2 == 0 else "inc-b"
        published.append(
            await bus.publish(topic=f"app.step.n{i:02d}", incident_id=incident)
        )

    child = subprocess.Popen(
        [sys.executable, "-m", "signalforge.tests.events.crash_runner", str(bus.db_path), "0.05"],
        cwd=str(ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        rows_at_kill = 0
        for _ in range(400):  # up to ~20s
            rows_at_kill = len(_projection_rows(bus.db_path))
            if rows_at_kill >= KILL_AFTER_ROWS:
                break
            if child.poll() is not None:
                break
            await asyncio.sleep(0.05)
        child.kill()
    finally:
        stdout, stderr = child.communicate(timeout=30)

    assert child.poll() is not None, "the consuming process is gone"
    assert KILL_AFTER_ROWS <= rows_at_kill < EVENT_COUNT, (
        "the kill must land mid-consumption, not before or after it; "
        f"rows={rows_at_kill} stderr={stderr.decode(errors='replace')[-800:]}"
    )

    # A fresh dispatcher over the same database: catch-up is the normal path.
    recovered = make_dispatcher(str(bus.db_path))
    report = await recovered.run_once()
    assert report.dead_lettered == 0

    rows = _projection_rows(bus.db_path)
    applied = [r["event_id"] for r in rows]

    assert len(applied) == EVENT_COUNT, "no event lost"
    assert len(set(applied)) == EVENT_COUNT, "no event applied twice"
    assert set(applied) == {str(e.event_id) for e in published}

    for partition in ("inc-a", "inc-b"):
        seen = [r["seq"] for r in rows if r["partition_key"] == partition]
        expected = [e.seq for e in published if e.partition_key == partition]
        assert seen == expected, f"order preserved within {partition}"

    assert (await recovered.run_once()).delivered == 0, "offsets are durable"
