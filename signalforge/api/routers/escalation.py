"""/escalation: policies with ack timers (REQ-014, REQ-012).

test (simulated fire) requires run_drills - an incident-command capability an
Executive does not hold, which is S1's acceptance demo for the clean 403.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from signalforge.contracts.roles import Permission
from signalforge.domain.identity.oncall import EscalationStep, EscalationTargetType
from signalforge.api import deps
from signalforge.api.schemas.identity import (
    PolicyIn,
    PolicyOut,
    TestFireOut,
    page_plan_out,
    policy_out,
)
from signalforge.application.identity.principal import Principal

router = APIRouter(
    prefix="/escalation", tags=["escalation"], dependencies=[Depends(deps.get_principal)]
)


def _steps(body: PolicyIn) -> list[EscalationStep]:
    return [
        EscalationStep(
            target_type=EscalationTargetType(s.target_type),
            target_id=s.target_id,
            timeout_s=s.timeout_s,
        )
        for s in body.steps
    ]


@router.get("/policies", response_model=list[PolicyOut])
async def list_policies(
    principal: Principal = Depends(deps.get_principal),
    container: deps.Container = Depends(deps.get_container),
):
    return [policy_out(p) for p in await container.oncall.list_policies(principal)]


@router.post("/policies", response_model=PolicyOut, status_code=201)
async def create_policy(
    body: PolicyIn,
    principal: Principal = Depends(deps.require(Permission.MANAGE_ORG)),
    container: deps.Container = Depends(deps.get_container),
):
    policy = await container.oncall.save_policy(
        principal, name=body.name, steps=_steps(body), app_id=body.app_id
    )
    return policy_out(policy)


@router.put("/policies/{policy_id}", response_model=PolicyOut)
async def update_policy(
    policy_id: str,
    body: PolicyIn,
    principal: Principal = Depends(deps.require(Permission.MANAGE_ORG)),
    container: deps.Container = Depends(deps.get_container),
):
    policy = await container.oncall.save_policy(
        principal, name=body.name, steps=_steps(body), app_id=body.app_id,
        policy_id=policy_id,
    )
    return policy_out(policy)


@router.delete("/policies/{policy_id}", status_code=204)
async def delete_policy(
    policy_id: str,
    principal: Principal = Depends(deps.require(Permission.MANAGE_ORG)),
    container: deps.Container = Depends(deps.get_container),
):
    await container.oncall.delete_policy(principal, policy_id)


@router.post("/policies/{policy_id}/test", response_model=TestFireOut)
async def test_fire_policy(
    policy_id: str,
    principal: Principal = Depends(deps.require(Permission.RUN_DRILLS)),
    container: deps.Container = Depends(deps.get_container),
):
    plan = await container.oncall.test_fire(principal, policy_id)
    return page_plan_out(policy_id, plan)
