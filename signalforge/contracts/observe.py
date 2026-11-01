"""/observe/* : the contract an onboarded application exposes to SignalForge.

Frozen contract (CLAUDE.md rule 3). Any app that serves these paths with these
shapes can be onboarded by the generic http-observe adapters, with no
app-specific code in the SignalForge core.
"""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field

from signalforge.contracts.canonical import (
    ActionExecution,
    ChaosInjection,
    HealthReport,
    LogRecord,
    TopologySnapshot,
    TraceSpan,
)

# GET /observe/metrics returns Prometheus text exposition format, not JSON.
OBSERVE_METRICS_CONTENT_TYPE = "text/plain; version=0.0.4"

OBSERVE_PATHS: dict[str, str] = {
    "metrics": "/observe/metrics",
    "logs": "/observe/logs",
    "traces": "/observe/traces",
    "health": "/observe/health",
    "topology": "/observe/topology",
    "actions_manifest": "/observe/actions",
    "action_execute": "/observe/actions/{action_id}/execute",
    "action_execution": "/observe/actions/executions/{idempotency_key}",
    "chaos_manifest": "/observe/chaos",
    "chaos_inject": "/observe/chaos/{scenario_id}/inject",
    "chaos_injections": "/observe/chaos/injections",
    "chaos_status": "/observe/chaos/injections/{injection_id}",
    "chaos_abort": "/observe/chaos/injections/{injection_id}/abort",
}


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ObserveLogsResponse(_Model):
    records: list[LogRecord] = Field(default_factory=list)
    total: Optional[int] = Field(default=None, description="Total matches if known")


class ObserveTracesResponse(_Model):
    spans: list[TraceSpan] = Field(default_factory=list)


# /observe/health returns a canonical HealthReport;
# /observe/topology returns a canonical TopologySnapshot;
# /observe/actions and /observe/chaos return their validated manifest documents.
ObserveHealthResponse = HealthReport
ObserveTopologyResponse = TopologySnapshot


class ActionExecuteRequest(_Model):
    params: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str = Field(min_length=1, max_length=256)


# POST /observe/actions/{id}/execute and
# GET  /observe/actions/executions/{idempotency_key} return an ActionExecution.
ActionExecuteResponse = ActionExecution


class ChaosInjectRequest(_Model):
    params: dict[str, Any] = Field(default_factory=dict)
    scope: str = Field(min_length=1, description="Drill authorization scope, e.g. drill:<id>")
    idempotency_key: str = Field(min_length=1, max_length=256)


ChaosInjectResponse = ChaosInjection
