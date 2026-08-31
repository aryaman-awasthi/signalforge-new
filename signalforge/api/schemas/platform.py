"""Wire shapes for the platform observability surface."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel

from signalforge.application.observability.platform_status import PlatformStatus
from signalforge.domain.observability.agent_runs import AgentRun, AgentRunStep


class ComponentOut(BaseModel):
    component: str
    status: str
    detail: Optional[str] = None


class BusStatusOut(BaseModel):
    head_seq: int
    max_lag: int
    groups: dict[str, int]
    dead_letters: dict[str, int]


class ClockOut(BaseModel):
    source: str
    now: datetime
    speed: float
    paused: bool


class RetentionOut(BaseModel):
    plane: str
    pruned_through_seq: int
    pruned_count: int
    pruned_at: datetime
    retention_days: int


class PlatformStatusOut(BaseModel):
    status: str
    version: str
    checked_at: datetime
    clock: ClockOut
    bus: BusStatusOut
    worker: dict[str, Any]
    components: list[ComponentOut]
    retention: list[RetentionOut]


class AgentRunOut(BaseModel):
    id: str
    agent: str
    status: str
    incident_id: Optional[str]
    trigger_topic: Optional[str]
    trigger_event_id: Optional[str]
    started_at: datetime
    finished_at: Optional[datetime]
    model: Optional[str]
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    error: Optional[str]


class AgentRunStepOut(BaseModel):
    id: str
    seq: int
    step: str
    status: str
    started_at: datetime
    finished_at: Optional[datetime]
    tokens: int
    detail: dict[str, Any]


class AgentRunDetailOut(AgentRunOut):
    steps: list[AgentRunStepOut]


def platform_status_out(status: PlatformStatus) -> PlatformStatusOut:
    return PlatformStatusOut(
        status=status.status.value,
        version=status.version,
        checked_at=status.checked_at,
        clock=ClockOut(**status.clock),
        bus=BusStatusOut(
            head_seq=status.bus.head_seq,
            max_lag=status.bus.max_lag,
            groups=status.bus.groups,
            dead_letters=status.bus.dead_letters,
        ),
        worker=status.worker,
        components=[ComponentOut(**c) for c in status.components],
        retention=[RetentionOut(**r) for r in status.retention],
    )


def agent_run_out(run: AgentRun) -> AgentRunOut:
    return AgentRunOut(
        id=run.id,
        agent=run.agent,
        status=run.status.value,
        incident_id=run.incident_id,
        trigger_topic=run.trigger_topic,
        trigger_event_id=run.trigger_event_id,
        started_at=run.started_at,
        finished_at=run.finished_at,
        model=run.model,
        prompt_tokens=run.prompt_tokens,
        completion_tokens=run.completion_tokens,
        total_tokens=run.total_tokens,
        error=run.error,
    )


def agent_step_out(step: AgentRunStep) -> AgentRunStepOut:
    return AgentRunStepOut(
        id=step.id,
        seq=step.seq,
        step=step.step,
        status=step.status,
        started_at=step.started_at,
        finished_at=step.finished_at,
        tokens=step.tokens,
        detail=step.detail,
    )
