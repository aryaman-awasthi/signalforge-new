"""Agent-run traces (REQ-025).

The tables and the port exist from Sprint 2 so that when the agent suite arrives
in Sprint 5 it writes into a trace store that is already tenant-scoped, already
in the migration history and already readable by the audit surface - rather than
observability being retrofitted around agents that are already running.

A run is one agent invocation triggered by one event; steps are what it did
inside that invocation, including token cost, which is what per-incident budgets
are measured against.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import Enum
from typing import Any, Optional, Sequence


class AgentRunStatus(str, Enum):
    STARTED = "started"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    DEGRADED = "degraded"
    """Completed on the deterministic fallback path rather than the model."""


@dataclass(frozen=True)
class AgentRun:
    id: str
    tenant_id: str
    agent: str
    status: AgentRunStatus
    started_at: datetime
    incident_id: Optional[str] = None
    trigger_topic: Optional[str] = None
    trigger_event_id: Optional[str] = None
    finished_at: Optional[datetime] = None
    model: Optional[str] = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    error: Optional[str] = None

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    def completed(
        self,
        *,
        status: AgentRunStatus,
        at: datetime,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        error: Optional[str] = None,
    ) -> "AgentRun":
        return replace(
            self,
            status=status,
            finished_at=at,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            error=error,
        )


@dataclass(frozen=True)
class AgentRunStep:
    id: str
    run_id: str
    seq: int
    step: str
    status: str
    started_at: datetime
    finished_at: Optional[datetime] = None
    tokens: int = 0
    detail: dict[str, Any] = field(default_factory=dict)


class AgentRunStore(ABC):
    @abstractmethod
    async def start(self, run: AgentRun) -> None: ...

    @abstractmethod
    async def finish(self, run: AgentRun) -> None: ...

    @abstractmethod
    async def add_step(self, step: AgentRunStep) -> None: ...

    @abstractmethod
    async def get(self, tenant_id: str, run_id: str) -> Optional[AgentRun]: ...

    @abstractmethod
    async def list(
        self, tenant_id: str, incident_id: Optional[str] = None, limit: int = 50
    ) -> Sequence[AgentRun]: ...

    @abstractmethod
    async def steps(self, tenant_id: str, run_id: str) -> Sequence[AgentRunStep]: ...
