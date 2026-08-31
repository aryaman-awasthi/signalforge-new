"""The operator's view of failed delivery (REQ-022).

Requeue is a state transition on a dead letter, recorded and audited, not an
instruction to a particular process. That separation matters: the architecture
allows splitting api and worker into two processes, and in that shape the API
node owns no consumer groups at all - a requeue that demanded a locally
registered group would simply be impossible there.

So this service records the intent and reports whether a consumer for that group
is registered on the node answering, and whichever dispatcher owns the group
drains it on its next sweep.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional, Sequence

from signalforge.application.events.uow import EventUnitOfWork
from signalforge.application.identity.principal import Principal
from signalforge.domain.events.entities import DeadLetter, DeadLetterState
from signalforge.domain.events.errors import DeadLetterNotFoundError
from signalforge.domain.identity.errors import ConflictError
from signalforge.domain.identity.ports import Clock

REQUEUED_TOPIC = "system.bus.requeued"


@dataclass(frozen=True)
class RequeueResult:
    dead_letter: DeadLetter
    drained_here: bool
    """False when no consumer for this group runs on the node that took the
    request - the event is queued, but an operator should know a worker has to
    be running somewhere for it to move."""


class DeadLetterService:
    def __init__(self, uow_factory: Callable[[], EventUnitOfWork], clock: Clock):
        self._uow_factory = uow_factory
        self._clock = clock

    async def list(
        self,
        principal: Principal,
        *,
        state: Optional[DeadLetterState] = None,
        group: Optional[str] = None,
        limit: int = 100,
    ) -> Sequence[DeadLetter]:
        async with self._uow_factory() as uow:
            return list(
                await uow.dead_letters.list(
                    tenant_id=principal.tenant_id, group=group, state=state, limit=limit
                )
            )

    async def requeue(
        self, principal: Principal, dead_letter_id: str, *, local_groups: Sequence[str] = ()
    ) -> RequeueResult:
        async with self._uow_factory() as uow:
            dead_letter = await uow.dead_letters.get(
                dead_letter_id, tenant_id=principal.tenant_id
            )
            if dead_letter is None:
                raise DeadLetterNotFoundError(f"dead letter {dead_letter_id} not found")
            if dead_letter.state is DeadLetterState.REQUEUED:
                raise ConflictError("that event is already queued for redelivery")

            requeued = dead_letter.requeued(by=principal.user_id, at=self._clock.now())
            await uow.dead_letters.update(requeued)
            await uow.audit.append(
                tenant_id=dead_letter.tenant_id,
                topic=REQUEUED_TOPIC,
                actor_type="human",
                actor_id=principal.user_id,
                payload={
                    "dead_letter_id": dead_letter.id,
                    "group": dead_letter.group,
                    "event_id": dead_letter.event_id,
                    "event_topic": dead_letter.topic,
                    "attempts": dead_letter.attempts,
                },
            )
            await uow.commit()
        return RequeueResult(
            dead_letter=requeued, drained_here=dead_letter.group in set(local_groups)
        )
