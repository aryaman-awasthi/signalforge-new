"""The incident lifecycle's one scheduled job (S4-T02).

    incident-itsm-sync   every 30s   retry every ticket link that is not settled

`workers/` owns *when*, never *what*: the sweep itself is an application service
(`IncidentTicketSync.retry_pending`) and this module only gives it a cadence.

It is a job rather than a retry loop inside the request because the request has
already returned. Declaring an incident commits before the ticket is attempted -
a ticket queue that is down must never be able to un-declare an incident - so
something outside that request has to carry the attempt forward, and a scheduled
sweep is the shape that survives a process restart mid-incident.

`run_at_start` is False. A node coming back up after an outage would otherwise
fire every unsettled ticket sync in the install at the same instant, against an
ITSM that may well be the thing that was down.
"""

from __future__ import annotations

from typing import Sequence

from signalforge.application.incidents.itsm import IncidentTicketSync
from signalforge.workers.runner import ScheduledJob

ITSM_SYNC_JOB = "incident-itsm-sync"

DEFAULT_INTERVAL_S = 30.0


def lifecycle_jobs(
    tickets: IncidentTicketSync, *, interval_s: float = DEFAULT_INTERVAL_S
) -> list[ScheduledJob]:
    async def sweep() -> str:
        return str(await tickets.retry_pending())

    return [
        ScheduledJob(
            name=ITSM_SYNC_JOB,
            interval_s=interval_s,
            run=sweep,
            run_at_start=False,
        )
    ]


def job_names() -> Sequence[str]:
    return (ITSM_SYNC_JOB,)
