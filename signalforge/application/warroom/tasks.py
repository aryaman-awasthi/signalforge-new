"""The incident task board (REQ-045).

Tasks arrive two ways - harvested from the bridge, or typed by a commander - and
they are the same thing afterwards, because a commitment made out loud and a
commitment written down are the same commitment.

The permission split is deliberate and is the only place in the war room where
one act is guarded two ways:

- **Creating and reassigning** is command work and carries `declare_incident`.
  Putting work on somebody else's board is a decision about other people's time.
- **Completing your own task** carries no permission and is self-scoped, for the
  same reason acknowledging a page is: the SME holds nothing in the frozen roles
  matrix, and a board they cannot tick is a board they will not use.

An unassigned task is completable by anybody in the tenant, and that is on
purpose - somebody has to be able to close out "check the connection pool" when
they have checked the connection pool, and refusing until a commander assigns it
first is how a board fills with work that was actually done.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Callable, Optional, Sequence

from signalforge.application.events.bus import EventBus
from signalforge.application.identity.principal import Principal
from signalforge.application.identity.uow import IdentityUnitOfWork
from signalforge.application.warroom.common import (
    TASK_CREATED_TOPIC,
    TASK_UPDATED_TOPIC,
    record,
)
from signalforge.domain.identity.ports import Clock
from signalforge.domain.incidents.errors import IncidentNotFoundError
from signalforge.domain.warroom.entities import (
    IncidentTask,
    TaskSource,
    TaskStatus,
)
from signalforge.domain.warroom.errors import (
    NotYourPageError,
    TaskNotFoundError,
    WarRoomValidationError,
)


class IncidentTaskService:
    def __init__(
        self,
        uow_factory: Callable[[], IdentityUnitOfWork],
        bus: EventBus,
        clock: Clock,
        metrics=None,
    ):
        self._uow_factory = uow_factory
        self._bus = bus
        self._clock = clock
        self._metrics = metrics

    async def list(
        self, principal: Principal, incident_id: str, *, limit: int = 200
    ) -> Sequence[IncidentTask]:
        async with self._uow_factory() as uow:
            await self._require_incident(uow, principal.tenant_id, incident_id)
            return list(
                await uow.incident_tasks.list(
                    principal.tenant_id, incident_id, limit=limit
                )
            )

    async def create(
        self,
        principal: Principal,
        incident_id: str,
        *,
        title: str,
        owner_user_id: Optional[str] = None,
        owner_label: str = "",
        due_at: Optional[datetime] = None,
        due_label: str = "",
        note: str = "",
    ) -> IncidentTask:
        now = self._clock.now()
        async with self._uow_factory() as uow:
            incident = await self._require_incident(
                uow, principal.tenant_id, incident_id
            )
            if owner_user_id:
                await self._require_user(uow, principal.tenant_id, owner_user_id)
            task = IncidentTask(
                id=str(uuid.uuid4()),
                tenant_id=principal.tenant_id,
                incident_id=incident_id,
                app_id=incident.app_id,
                title=title,
                status=TaskStatus.OPEN,
                source=TaskSource.MANUAL,
                created_at=now,
                created_by=principal.user_id,
                owner_user_id=owner_user_id,
                owner_label=owner_label,
                due_at=due_at,
                due_label=due_label,
                note=note,
            )
            await uow.incident_tasks.add(task)
            await record(
                uow,
                self._bus,
                self._clock,
                principal=principal,
                app_id=incident.app_id,
                incident_id=incident_id,
                topic=TASK_CREATED_TOPIC,
                payload=_payload(task, incident.reference),
            )
            await uow.commit()
        self._count(task)
        return task

    async def update(
        self,
        principal: Principal,
        task_id: str,
        *,
        owner_user_id: Optional[str] = None,
        due_at: Optional[datetime] = None,
        cancel: bool = False,
        note: str = "",
    ) -> IncidentTask:
        """Reassign, re-time or cancel. Command work, so it carries a permission."""
        async with self._uow_factory() as uow:
            task = await self._require_task(uow, principal.tenant_id, task_id)
            incident = await self._require_incident(
                uow, principal.tenant_id, task.incident_id
            )
            if owner_user_id:
                await self._require_user(uow, principal.tenant_id, owner_user_id)
            now = self._clock.now()
            updated = (
                task.cancelled(at=now, by=principal.user_id, note=note)
                if cancel
                else task.reassigned(
                    at=now,
                    owner_user_id=owner_user_id or task.owner_user_id,
                    due_at=due_at,
                )
            )
            if note and not cancel:
                updated = _with_note(updated, note)
            await uow.incident_tasks.update(updated)
            await record(
                uow,
                self._bus,
                self._clock,
                principal=principal,
                app_id=task.app_id,
                incident_id=task.incident_id,
                topic=TASK_UPDATED_TOPIC,
                payload={
                    **_payload(updated, incident.reference),
                    "cancelled": cancel,
                    "previous_owner": task.owner_user_id,
                },
            )
            await uow.commit()
        return updated

    async def complete(
        self, principal: Principal, task_id: str, *, note: str = ""
    ) -> IncidentTask:
        """Tick your own task.

        The route behind this carries no permission, so the authorization is the
        check below: the task is yours, or it is nobody's. An owned task can only
        be completed by its owner, because "done" is a claim about work somebody
        did and a claim made on another person's behalf is not evidence of
        anything.
        """
        async with self._uow_factory() as uow:
            task = await self._require_task(uow, principal.tenant_id, task_id)
            if task.owner_user_id and task.owner_user_id != principal.user_id:
                raise NotYourPageError(
                    "a task is completed by the person who owns it"
                )
            incident = await self._require_incident(
                uow, principal.tenant_id, task.incident_id
            )
            done = task.completed(
                at=self._clock.now(), by=principal.user_id, note=note
            )
            if done is task:
                return task
            await uow.incident_tasks.update(done)
            await record(
                uow,
                self._bus,
                self._clock,
                principal=principal,
                app_id=task.app_id,
                incident_id=task.incident_id,
                topic=TASK_UPDATED_TOPIC,
                payload={
                    **_payload(done, incident.reference),
                    "completed": True,
                    "was_unassigned": not task.assigned,
                },
            )
            await uow.commit()
        if self._metrics is not None:
            self._metrics.increment(
                "signalforge_incident_tasks_completed_total",
                {"source": done.source.value},
            )
        return done

    # -- internals -----------------------------------------------------------

    @staticmethod
    async def _require_incident(uow: IdentityUnitOfWork, tenant_id: str, incident_id: str):
        incident = await uow.incidents.get(tenant_id, incident_id)
        if incident is None:
            raise IncidentNotFoundError(f"incident {incident_id} not found")
        return incident

    @staticmethod
    async def _require_task(
        uow: IdentityUnitOfWork, tenant_id: str, task_id: str
    ) -> IncidentTask:
        task = await uow.incident_tasks.get(tenant_id, task_id)
        if task is None:
            # Identical whether it belongs to another tenant or to nobody.
            raise TaskNotFoundError(f"task {task_id} not found")
        return task

    @staticmethod
    async def _require_user(uow: IdentityUnitOfWork, tenant_id: str, user_id: str) -> None:
        user = await uow.users.get(tenant_id, user_id)
        if user is None:
            raise WarRoomValidationError(
                f"user {user_id} is not a member of this organisation; a task "
                "cannot be assigned outside it"
            )

    def _count(self, task: IncidentTask) -> None:
        if self._metrics is None:
            return
        self._metrics.increment(
            "signalforge_incident_tasks_total",
            {
                "source": task.source.value,
                "assigned": "true" if task.assigned else "false",
            },
        )


def _with_note(task: IncidentTask, note: str) -> IncidentTask:
    from dataclasses import replace

    return replace(task, note=note)


def _payload(task: IncidentTask, reference: str) -> dict:
    return {
        "incident_id": task.incident_id,
        "reference": reference,
        "task_id": task.id,
        "title": task.title,
        "status": task.status.value,
        "source": task.source.value,
        "owner_user_id": task.owner_user_id,
        "owner_label": task.owner_label,
        "assigned": task.assigned,
        "due_at": task.due_at.isoformat() if task.due_at else None,
        "due_label": task.due_label,
    }
