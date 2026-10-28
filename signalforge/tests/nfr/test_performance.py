"""A small, reproducible benchmark of the reads a cockpit actually makes.

Artifact 17. Deliberately *not* a load test: this build is one machine, SQLite
WAL and a single uvicorn worker, and a synthetic thousand-user number from that
would be a claim about nothing. What is worth measuring, and is measured here,
is the shape of each read on a realistic incident: how long the median takes,
how long the slow tail takes, and whether any of them fails.

The numbers are written to var/nfr/performance.json and artifact 17 is generated
from that file, so the report can only ever say what a run actually did. The
environment is recorded beside them, because a latency without its machine is a
number without units.
"""

from __future__ import annotations

import platform
import statistics
import sys
import time

import pytest

from signalforge.tests.nfr.conftest import (  # noqa: F401  (fixtures)
    APP_ID,
    act,
    governed,
    observe,
    record,
    request_execution,
    run_validation,
)

pytestmark = pytest.mark.anyio

SAMPLES = 30
"""Enough for a stable median and a meaningful p95 without turning the suite
into a benchmark run. A p95 over thirty samples is the 29th value; that is
stated in the report rather than dressed up as a percentile estimate."""

ROLLBACK = "rollback-checkout"

BUDGET_MS = 1_500.0
"""A ceiling, not a target. It exists so that a change which makes a cockpit read
ten times slower fails a test instead of quietly shipping; it is not a
performance claim, and the report prints measured numbers rather than this."""


@pytest.mark.req("REQ-073")
async def test_cockpit_reads_are_measured_and_recorded(governed):
    api = governed.api
    incident_id = await _a_realistic_incident(governed)
    token = governed.requester_token
    owner = governed.owner_token

    operations = {
        "incident read": (f"/incidents/{incident_id}", token),
        "war-room read": (f"/incidents/{incident_id}/war-room", token),
        "brain read": (f"/incidents/{incident_id}/brain", token),
        "next best action read": (f"/incidents/{incident_id}/actions", token),
        "execution ledger read": (f"/incidents/{incident_id}/executions", token),
        "validation read": (f"/incidents/{incident_id}/validation", token),
        "communications read": (f"/incidents/{incident_id}/communications", token),
        "audit replay": (f"/incidents/{incident_id}/replay", owner),
        "incident list": ("/incidents", token),
        "detection read": (f"/apps/{APP_ID}/detection", token),
    }

    measurements = {}
    for name, (path, use) in operations.items():
        samples: list[float] = []
        failures = 0
        for _ in range(SAMPLES):
            started = time.perf_counter()
            response = api.client.get(path, headers=api.auth_header(use))
            samples.append((time.perf_counter() - started) * 1000.0)
            if response.status_code != 200:
                failures += 1
        ordered = sorted(samples)
        measurements[name] = {
            "path": path,
            "samples": len(samples),
            "failures": failures,
            "min_ms": round(ordered[0], 2),
            "median_ms": round(statistics.median(ordered), 2),
            "p95_ms": round(ordered[int(len(ordered) * 0.95) - 1], 2),
            "max_ms": round(ordered[-1], 2),
        }
        assert failures == 0, f"{name} failed {failures}/{SAMPLES} times"

    record(
        "performance",
        {
            "environment": {
                "python": sys.version.split()[0],
                "platform": platform.platform(),
                "processor": platform.processor() or "unknown",
                "database": "SQLite (file, WAL), in-process ASGI transport",
                "note": (
                    "Single machine, no Docker, one process. Latencies are "
                    "in-process: they exclude a socket and a browser, and they "
                    "include the whole application, ORM and SQLite path."
                ),
                "samples_per_operation": SAMPLES,
                "p95_method": (
                    f"the {int(SAMPLES * 0.95)}th of {SAMPLES} ordered samples, "
                    "not an interpolated percentile"
                ),
            },
            "operations": measurements,
        },
    )

    slow = {
        name: row["p95_ms"]
        for name, row in measurements.items()
        if row["p95_ms"] > BUDGET_MS
    }
    assert not slow, f"cockpit reads past the {BUDGET_MS}ms ceiling: {slow}"


async def _a_realistic_incident(governed) -> str:
    """An incident with a graph, a ledger, an approval and a validation cycle.

    Benchmarking an empty incident would measure an empty incident.
    """
    api = governed.api
    incident_id = governed.declare(title="Checkout failing after the release")["id"]
    api.client.post(
        f"/incidents/{incident_id}/brain/refresh",
        json={},
        headers=api.auth_header(governed.requester_token),
    )
    requested = request_execution(
        api,
        governed.requester_token,
        incident_id,
        action_id=ROLLBACK,
        args={"release": "v11"},
    )
    execution_id = requested["execution"]["execution_id"]
    act(api, governed.approver_token, incident_id, execution_id, "approve", note="ok")
    act(api, governed.requester_token, incident_id, execution_id, "run")
    run_validation(api, governed.requester_token, incident_id)
    api.client.post(
        f"/incidents/{incident_id}/communications",
        json={"audience": "responders", "use_model": False},
        headers=api.auth_header(governed.requester_token),
    )
    return incident_id
