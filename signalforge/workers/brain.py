"""The incident-brain scheduled job (S5-T03, REQ-052).

One job, and it is deliberately small: drain the set of incidents the bus
consumer woke, and run a governor pass for each.

The separation from the consumer is the point. A pass reads several stores, may
call a model over the network and writes a transaction of its own; running that
inside the dispatcher's transaction would hold a SQLite write lock for seconds
against a loop meant to take milliseconds. Here it holds nothing anybody else is
waiting on, and a pass that takes ten seconds delays only the next pass.

The job never raises: `PlatformWorker` records a failed run and the next tick
tries again. A brain that could take the worker group down would take the
dispatcher, the escalation sweep and the retention job with it - which is the
exact inversion of "AI is an accelerator, not a dependency".
"""

from __future__ import annotations

import logging

from signalforge.workers.runner import ScheduledJob

logger = logging.getLogger("signalforge.brain")


def brain_jobs(governor, *, interval_s: float = 20.0) -> list[ScheduledJob]:
    async def think() -> None:
        if governor.pending == 0:
            return
        results = await governor.drain()
        for result in results:
            if result.changed or result.moved:
                logger.info(
                    "incident brain pass",
                    extra={
                        "incident_id": result.incident_id,
                        "nodes": result.nodes_written,
                        "versions": result.versions_written,
                        "moved": len(result.moved),
                        "degraded": result.degraded,
                    },
                )

    return [
        ScheduledJob(name="incident-brain", interval_s=interval_s, run=think),
    ]
