"""On-call and escalation use cases (REQ-014), including the drill-gated
escalation test-fire that S1's acceptance demo denies to an Executive."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Optional, Sequence

from signalforge.domain.identity.errors import NotFoundError
from signalforge.domain.identity.oncall import (
    EscalationPolicy,
    EscalationStep,
    EscalationTargetType,
    OnCallRotation,
)
from signalforge.domain.identity.ports import Clock
from signalforge.application.identity.auth_service import UowFactory
from signalforge.application.identity.principal import Principal
from signalforge.application.identity.uow import IdentityUnitOfWork


@dataclass(frozen=True)
class PagePlanEntry:
    order: int
    target_type: str
    target_id: str
    resolved_user_id: str
    page_at_offset_s: int


class OnCallService:
    def __init__(self, uow_factory: UowFactory, clock: Clock):
        self._uow_factory = uow_factory
        self._clock = clock

    # -- rotations ---------------------------------------------------------

    async def list_rotations(self, principal: Principal) -> Sequence[OnCallRotation]:
        async with self._uow_factory() as uow:
            return await uow.oncall.list(principal.tenant_id)

    async def save_rotation(
        self,
        principal: Principal,
        *,
        name: str,
        user_ids: Sequence[str],
        app_id: Optional[str] = None,
        handoff_interval_hours: int = 168,
        rotation_id: Optional[str] = None,
    ) -> OnCallRotation:
        async with self._uow_factory() as uow:
            if rotation_id is not None:
                existing = await uow.oncall.get(principal.tenant_id, rotation_id)
                if existing is None:
                    raise NotFoundError("rotation not found")
            await self._require_users(uow, principal.tenant_id, user_ids)
            rotation = OnCallRotation(
                id=rotation_id or str(uuid.uuid4()), tenant_id=principal.tenant_id,
                name=name, user_ids=tuple(user_ids), app_id=app_id,
                handoff_interval_hours=handoff_interval_hours,
            )
            await uow.oncall.upsert(rotation)
            await uow.audit.append(
                tenant_id=principal.tenant_id, topic="audit.oncall.rotation_saved",
                actor_type="human", actor_id=principal.user_id,
                payload={
                    "rotation_id": rotation.id, "name": name,
                    "order": list(user_ids),
                    "handoff_interval_hours": handoff_interval_hours,
                },
                app_id=app_id,
            )
            await uow.commit()
        return rotation

    async def delete_rotation(self, principal: Principal, rotation_id: str) -> None:
        async with self._uow_factory() as uow:
            deleted = await uow.oncall.delete(principal.tenant_id, rotation_id)
            if not deleted:
                raise NotFoundError("rotation not found")
            await uow.audit.append(
                tenant_id=principal.tenant_id, topic="audit.oncall.rotation_deleted",
                actor_type="human", actor_id=principal.user_id,
                payload={"rotation_id": rotation_id},
            )
            await uow.commit()

    # -- escalation policies -----------------------------------------------

    async def list_policies(self, principal: Principal) -> Sequence[EscalationPolicy]:
        async with self._uow_factory() as uow:
            return await uow.escalations.list(principal.tenant_id)

    async def save_policy(
        self,
        principal: Principal,
        *,
        name: str,
        steps: Sequence[EscalationStep],
        app_id: Optional[str] = None,
        policy_id: Optional[str] = None,
    ) -> EscalationPolicy:
        async with self._uow_factory() as uow:
            if policy_id is not None:
                existing = await uow.escalations.get(principal.tenant_id, policy_id)
                if existing is None:
                    raise NotFoundError("escalation policy not found")
            for step in steps:
                await self._require_target(uow, principal.tenant_id, step)
            policy = EscalationPolicy(
                id=policy_id or str(uuid.uuid4()), tenant_id=principal.tenant_id,
                name=name, steps=tuple(steps), app_id=app_id,
            )
            await uow.escalations.upsert(policy)
            await uow.audit.append(
                tenant_id=principal.tenant_id, topic="audit.escalation.policy_saved",
                actor_type="human", actor_id=principal.user_id,
                payload={
                    "policy_id": policy.id, "name": name,
                    "steps": [
                        [s.target_type.value, s.target_id, s.timeout_s] for s in steps
                    ],
                },
                app_id=app_id,
            )
            await uow.commit()
        return policy

    async def delete_policy(self, principal: Principal, policy_id: str) -> None:
        async with self._uow_factory() as uow:
            deleted = await uow.escalations.delete(principal.tenant_id, policy_id)
            if not deleted:
                raise NotFoundError("escalation policy not found")
            await uow.audit.append(
                tenant_id=principal.tenant_id, topic="audit.escalation.policy_deleted",
                actor_type="human", actor_id=principal.user_id,
                payload={"policy_id": policy_id},
            )
            await uow.commit()

    async def test_fire(
        self, principal: Principal, policy_id: str
    ) -> list[PagePlanEntry]:
        """Simulates the escalation chain without paging anyone: resolves who
        each step would page and when. Requires run_drills (IC/owner only)."""
        now = self._clock.now()
        async with self._uow_factory() as uow:
            policy = await uow.escalations.get(principal.tenant_id, policy_id)
            if policy is None:
                raise NotFoundError("escalation policy not found")
            plan: list[PagePlanEntry] = []
            offsets = policy.page_offsets_s()
            for index, step in enumerate(policy.steps):
                if step.target_type is EscalationTargetType.ROTATION:
                    rotation = await uow.oncall.get(principal.tenant_id, step.target_id)
                    if rotation is None:
                        raise NotFoundError(
                            f"rotation {step.target_id} referenced by step {index + 1} not found"
                        )
                    shift_s = rotation.handoff_interval_hours * 3600
                    resolved = rotation.on_call(int(now.timestamp()) // shift_s)
                else:
                    resolved = step.target_id
                plan.append(
                    PagePlanEntry(
                        order=index + 1, target_type=step.target_type.value,
                        target_id=step.target_id, resolved_user_id=resolved,
                        page_at_offset_s=offsets[index],
                    )
                )
            await uow.audit.append(
                tenant_id=principal.tenant_id, topic="audit.escalation.test_fired",
                actor_type="human", actor_id=principal.user_id,
                payload={
                    "policy_id": policy_id,
                    "plan": [
                        [p.order, p.target_type, p.resolved_user_id, p.page_at_offset_s]
                        for p in plan
                    ],
                },
            )
            await uow.commit()
        return plan

    # -- helpers ------------------------------------------------------------

    @staticmethod
    async def _require_users(
        uow: IdentityUnitOfWork, tenant_id: str, user_ids: Sequence[str]
    ) -> None:
        for user_id in user_ids:
            if await uow.users.get(tenant_id, user_id) is None:
                raise NotFoundError(f"user {user_id} not found in this organisation")

    @staticmethod
    async def _require_target(
        uow: IdentityUnitOfWork, tenant_id: str, step: EscalationStep
    ) -> None:
        if step.target_type is EscalationTargetType.USER:
            if await uow.users.get(tenant_id, step.target_id) is None:
                raise NotFoundError(f"user {step.target_id} not found in this organisation")
        else:
            if await uow.oncall.get(tenant_id, step.target_id) is None:
                raise NotFoundError(f"rotation {step.target_id} not found in this organisation")
