"""War-room scheduled jobs (S4-T03, REQ-045).

Two jobs, and both exist because a war room's most important behaviour happens
when nobody is looking at it.

**escalation** is the countdown. It holds no timers: it asks the repository for
pages whose stored `ack_deadline_at` has passed, which means a restart loses
nothing, a node that was asleep catches up on its next tick, and a drill running
on a virtual clock at 100x escalates at 100x with no drill-aware code anywhere.
It runs whether or not detection does - an incident declared by hand on a node
with no detector still has phones to ring - and it reconciles before it acts, so
a page answered over the /ws socket is not escalated past.

**bridge-harvest** turns what was said into work. It reads each open bridge
forward from its own cursor, so a room open for six hours costs the same as one
open for six minutes, and the commitment keys are derived, so a restart produces
one task rather than a second copy of every task.

Neither job raises: `PlatformWorker` records a failed run and the next tick tries
again. A sweep that could crash the worker group would take the dispatcher and
the retention job down with it.
"""

from __future__ import annotations

import logging

from signalforge.application.warroom.bridge import BridgeService
from signalforge.application.warroom.paging import PagingService
from signalforge.workers.runner import ScheduledJob

logger = logging.getLogger("signalforge.warroom")


def warroom_jobs(
    paging: PagingService,
    bridge: BridgeService,
    *,
    escalation_interval_s: float = 15.0,
    harvest_interval_s: float = 30.0,
) -> list[ScheduledJob]:
    async def escalate() -> None:
        sweep = await paging.sweep()
        if sweep.considered:
            logger.info("escalation sweep: %s", sweep)

    async def harvest() -> None:
        sweep = await bridge.harvest_all()
        if sweep.commitments:
            logger.info("bridge harvest: %s", sweep)

    return [
        ScheduledJob(
            name="page-escalation", interval_s=escalation_interval_s, run=escalate
        ),
        ScheduledJob(
            name="bridge-harvest", interval_s=harvest_interval_s, run=harvest
        ),
    ]
