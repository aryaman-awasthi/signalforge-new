"""The retention sweep (REQ-023).

The only code in the platform that removes a recorded event. It works one
(tenant, plane) chain at a time, refuses the incident plane outright, stops
short of anything a seal covers, and leaves a watermark holding the hash of the
last event it removed so the surviving chain still verifies. Every sweep that
removed something publishes a kept event and writes an audit entry, so the
absence of old data is itself evidenced.

Time comes from the wall clock, never the drill clock: fast-forwarding a
scenario must not age out a week of real telemetry.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional

from signalforge.application.events.bus import EventBus
from signalforge.application.events.uow import EventUnitOfWork
from signalforge.contracts.envelope import (
    Actor,
    ActorType,
    EventEnvelope,
    Plane,
    Source,
)
from signalforge.domain.events.entities import RetentionWatermark
from signalforge.domain.events.retention import PRUNED_TOPIC, RetentionPolicy
from signalforge.domain.identity.ports import Clock


@dataclass
class RetentionResult:
    pruned: int = 0
    protected_by_seal: int = 0
    tenants: list[str] = field(default_factory=list)


class RetentionService:
    def __init__(
        self,
        uow_factory: Callable[[], EventUnitOfWork],
        bus: EventBus,
        clock: Clock,
        policy: Optional[RetentionPolicy] = None,
    ):
        self._uow_factory = uow_factory
        self._bus = bus
        self._clock = clock
        self._policy = policy or RetentionPolicy()

    @property
    def policy(self) -> RetentionPolicy:
        return self._policy

    async def sweep(self) -> RetentionResult:
        now = self._clock.now()
        cutoff = self._policy.cutoff(now)
        result = RetentionResult()

        async with self._uow_factory() as uow:
            tenant_ids = list(await uow.events.tenants())

        for tenant_id in tenant_ids:
            result.tenants.append(tenant_id)
            for plane in Plane:
                if not self._policy.applies_to(plane):
                    continue
                pruned, protected = await self._prune_plane(tenant_id, plane, cutoff, now)
                result.pruned += pruned
                result.protected_by_seal += protected
        return result

    async def _prune_plane(
        self, tenant_id: str, plane: Plane, cutoff, now
    ) -> tuple[int, int]:
        async with self._uow_factory() as uow:
            seals = await uow.seals.list(tenant_id=tenant_id)
            protect_from_seq = min(
                (s.from_seq for s in seals if s.plane is plane), default=None
            )
            pruned, anchor, protected = await uow.events.prune(
                tenant_id=tenant_id,
                plane=plane,
                recorded_before=cutoff,
                protect_from_seq=protect_from_seq,
            )
            if pruned == 0:
                await uow.rollback()
                return 0, protected

            previous = await uow.retention.get(tenant_id, plane)
            await uow.retention.upsert(
                RetentionWatermark(
                    tenant_id=tenant_id,
                    plane=plane,
                    pruned_through_seq=anchor.seq,
                    anchor_hash=anchor.payload_hash,
                    pruned_count=(previous.pruned_count if previous else 0) + pruned,
                    pruned_at=now,
                )
            )
            payload = {
                "plane": plane.value,
                "pruned": pruned,
                "pruned_through_seq": anchor.seq,
                "retention_days": self._policy.signal_retention_days,
                "cutoff": cutoff.isoformat(),
                "protected_by_seal": protected,
            }
            await self._bus.publish(
                uow,
                EventEnvelope(
                    topic=PRUNED_TOPIC,
                    tenant_id=tenant_id,
                    # Kept plane: the record that data was removed must outlive
                    # the retention window that removed it.
                    plane=Plane.INCIDENT,
                    occurred_at=now,
                    actor=Actor(type=ActorType.SYSTEM, id="retention"),
                    source=Source(system="signalforge.retention"),
                    payload=payload,
                ),
            )
            await uow.audit.append(
                tenant_id=tenant_id,
                topic=PRUNED_TOPIC,
                actor_type="system",
                actor_id="retention",
                payload=payload,
            )
            await uow.commit()
        return pruned, protected
