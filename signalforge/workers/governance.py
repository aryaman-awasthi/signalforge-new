"""The governance scheduled jobs (S6-T02, REQ-061, REQ-062).

Two jobs, both housekeeping, and neither of them is where a control lives.

**Approval expiry** marks granted approvals whose clock has run out. The
*enforcement* is `Approval.assert_valid_now`, which refuses an expired approval
whether or not this job has ever run - so a node with no worker group is exactly
as safe, it just shows a countdown at -00:42 instead of the word `expired`.

**The uncertain sweep** logs and counts executions that have been sitting in
`uncertain` with nobody reconciling them. It deliberately does **not** reconcile
them itself. Reconciliation queries a production system and its verdict decides
whether a retry is permitted; that is an operator's action, taken while they are
looking at the incident, not something a background loop should do at 04:00 on
behalf of nobody. What the job does is make sure an unresolved consequential
action cannot go quietly unnoticed.

Neither job raises: `PlatformWorker` records a failed run and the next tick tries
again. A governance sweep that could take the worker group down would take the
dispatcher and the escalation timers with it.
"""

from __future__ import annotations

import logging

from signalforge.workers.runner import ScheduledJob

logger = logging.getLogger("signalforge.governance")

UNCERTAIN_ALERT_AFTER_S = 300.0
"""Five minutes. Long enough that an operator working the incident is not
nagged mid-decision, short enough that an execution nobody owns is visible
within one shift handover."""


def governance_jobs(
    gateway,
    uow_factory,
    clock,
    *,
    interval_s: float = 60.0,
    metrics=None,
) -> list[ScheduledJob]:
    async def expire_approvals() -> None:
        expired = await gateway.expire_approvals()
        if expired:
            logger.info("approvals expired", extra={"count": expired})

    async def watch_uncertain() -> None:
        from datetime import timedelta

        cutoff = clock.now() - timedelta(seconds=UNCERTAIN_ALERT_AFTER_S)
        async with uow_factory() as uow:
            stale = await uow.executions.due_for_reconciliation(
                older_than=cutoff, limit=50
            )
        if metrics is not None:
            metrics.set_gauge("signalforge_executions_unreconciled", len(stale))
        for execution in stale:
            # Logged, never resolved here. Reconciliation asks a production
            # system a question whose answer decides whether a retry may happen,
            # and that is a person's call.
            logger.warning(
                "an execution has been uncertain with nobody reconciling it",
                extra={
                    "execution_id": execution.id,
                    "incident_id": execution.incident_id,
                    "action_id": execution.action_id,
                    "requested_at": execution.requested_at.isoformat(),
                },
            )

    return [
        ScheduledJob(
            name="approval-expiry", interval_s=interval_s, run=expire_approvals
        ),
        ScheduledJob(
            name="uncertain-executions",
            interval_s=max(interval_s, 60.0),
            run=watch_uncertain,
        ),
    ]
