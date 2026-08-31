"""Canonical models: the app-agnostic shapes every adapter translates into.

Frozen contract (CLAUDE.md rule 3). One section per adapter kind, in the
canonical order: metrics, logs, traces, health, alerts, changes, topology,
collaboration, itsm, actions, notifications, knowledge, chaos.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field

from signalforge.contracts.envelope import Actor


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --- metrics -----------------------------------------------------------------

class MetricPoint(_Model):
    ts: datetime
    value: float


class MetricSeries(_Model):
    name: str
    labels: dict[str, str] = Field(default_factory=dict)
    points: list[MetricPoint] = Field(default_factory=list)


# --- logs --------------------------------------------------------------------

class LogLevel(str, Enum):
    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class LogRecord(_Model):
    ts: datetime
    level: LogLevel
    message: str
    service: Optional[str] = None
    logger: Optional[str] = None
    trace_id: Optional[str] = None
    attributes: dict[str, str] = Field(default_factory=dict)


# --- traces ------------------------------------------------------------------

class SpanStatus(str, Enum):
    OK = "ok"
    ERROR = "error"


class TraceSpan(_Model):
    trace_id: str
    span_id: str
    parent_span_id: Optional[str] = None
    service: str
    operation: str
    start: datetime
    end: datetime
    status: SpanStatus = SpanStatus.OK
    attributes: dict[str, str] = Field(default_factory=dict)


# --- health ------------------------------------------------------------------

class HealthStatus(str, Enum):
    UP = "up"
    DEGRADED = "degraded"
    DOWN = "down"
    UNKNOWN = "unknown"


class ComponentHealth(_Model):
    component: str
    status: HealthStatus
    detail: Optional[str] = None


class HealthReport(_Model):
    status: HealthStatus
    components: list[ComponentHealth] = Field(default_factory=list)
    checked_at: datetime


class ProbeSpec(_Model):
    probe_id: str
    target: str = Field(description="URL or address the probe exercises")
    method: str = "GET"
    timeout_s: float = Field(default=5.0, gt=0)
    expected_status: int = 200
    journey: Optional[str] = Field(default=None, description="User journey this probe represents")


class ProbeResult(_Model):
    probe_id: str
    target: str
    ok: bool
    latency_ms: float = Field(ge=0)
    status_code: Optional[int] = None
    detail: Optional[str] = None
    executed_at: datetime


# --- alerts ------------------------------------------------------------------

class AlertSeverity(str, Enum):
    CRITICAL = "critical"
    WARNING = "warning"
    INFO = "info"


class AlertStatus(str, Enum):
    FIRING = "firing"
    RESOLVED = "resolved"


class Alert(_Model):
    alert_id: str
    name: str
    severity: AlertSeverity
    status: AlertStatus
    starts_at: datetime
    ends_at: Optional[datetime] = None
    labels: dict[str, str] = Field(default_factory=dict)
    annotations: dict[str, str] = Field(default_factory=dict)


# --- changes -----------------------------------------------------------------

class ChangeType(str, Enum):
    DEPLOYMENT = "deployment"
    CONFIG = "config"
    INFRA = "infra"
    FEATURE_FLAG = "feature_flag"


class ChangeStatus(str, Enum):
    STARTED = "started"
    COMPLETED = "completed"
    FAILED = "failed"
    ROLLED_BACK = "rolled_back"


class ChangeEvent(_Model):
    change_id: str
    change_type: ChangeType
    service: str
    status: ChangeStatus
    started_at: datetime
    completed_at: Optional[datetime] = None
    version: Optional[str] = None
    actor: Optional[str] = None
    diff_ref: Optional[str] = Field(default=None, description="Commit range, PR or ticket link")


# --- topology ----------------------------------------------------------------

class TopologyNodeType(str, Enum):
    SERVICE = "service"
    DATABASE = "database"
    QUEUE = "queue"
    CACHE = "cache"
    EXTERNAL = "external"
    OTHER = "other"


class TopologyEdgeType(str, Enum):
    CALLS = "calls"
    DEPENDS_ON = "depends_on"
    REPLICATES_TO = "replicates_to"


class TopologyNode(_Model):
    node_id: str
    name: str
    node_type: TopologyNodeType
    region: Optional[str] = None
    labels: dict[str, str] = Field(default_factory=dict)


class TopologyEdge(_Model):
    source: str
    target: str
    edge_type: TopologyEdgeType = TopologyEdgeType.CALLS
    labels: dict[str, str] = Field(default_factory=dict)


class TopologySnapshot(_Model):
    version: str
    captured_at: datetime
    nodes: list[TopologyNode] = Field(default_factory=list)
    edges: list[TopologyEdge] = Field(default_factory=list)


# --- collaboration -----------------------------------------------------------

class CollabMessage(_Model):
    message_id: str
    channel: str
    author: Actor
    text: str
    sent_at: datetime
    thread_id: Optional[str] = None
    simulated: bool = False


# --- itsm --------------------------------------------------------------------

class TicketStatus(str, Enum):
    OPEN = "open"
    IN_PROGRESS = "in_progress"
    RESOLVED = "resolved"
    CLOSED = "closed"


class Ticket(_Model):
    ticket_id: str
    title: str
    status: TicketStatus
    created_at: datetime
    updated_at: Optional[datetime] = None
    description: Optional[str] = None
    severity: Optional[str] = None
    assignee: Optional[str] = None
    external_ref: Optional[str] = None
    links: list[str] = Field(default_factory=list)


# --- actions -----------------------------------------------------------------

class ActionClass(str, Enum):
    """The first six are the high-risk classes the policy floor can never auto-execute."""

    ROLLBACK = "rollback"
    RESTART = "restart"
    FAILOVER = "failover"
    TRAFFIC_SHIFT = "traffic_shift"
    CONFIG_CHANGE = "config_change"
    CUSTOMER_IMPACTING = "customer_impacting"
    DIAGNOSTIC = "diagnostic"
    OTHER = "other"


HIGH_RISK_ACTION_CLASSES = frozenset(
    {
        ActionClass.ROLLBACK,
        ActionClass.RESTART,
        ActionClass.FAILOVER,
        ActionClass.TRAFFIC_SHIFT,
        ActionClass.CONFIG_CHANGE,
        ActionClass.CUSTOMER_IMPACTING,
    }
)


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ActionDefinition(_Model):
    action_id: str
    name: str
    description: str
    action_class: ActionClass
    risk: RiskLevel
    target: Optional[str] = None
    params_schema: dict[str, Any] = Field(
        default_factory=dict, description="JSON schema for execute() params"
    )
    timeout_s: float = Field(default=60.0, gt=0)
    idempotent: bool = False
    reversible: bool = False
    reverse_action_id: Optional[str] = None
    preconditions: list[str] = Field(default_factory=list)
    verification: list[str] = Field(default_factory=list, description="Post-execution checks")
    approver_roles: list[str] = Field(default_factory=list)
    simulated: bool = False


class ExecutionState(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    UNCERTAIN = "uncertain"  # requested but outcome unknown (timeout, lost response)
    CANCELLED = "cancelled"


class ReconcileOutcome(str, Enum):
    """Verdicts of reconcile-before-retry over an UNCERTAIN execution (Sprint 6)."""

    COMPLETED_LATE = "completed_late"
    NOT_APPLIED = "not_applied"
    PARTIAL = "partial"


class ActionExecution(_Model):
    execution_id: str
    action_id: str
    idempotency_key: str
    state: ExecutionState
    requested_at: datetime
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    output: dict[str, Any] = Field(default_factory=dict)
    error: Optional[str] = None
    reconcile_outcome: Optional[ReconcileOutcome] = None


# --- notifications -----------------------------------------------------------

class NotificationChannel(str, Enum):
    IN_APP = "in_app"
    BROWSER_PUSH = "browser_push"
    SMS = "sms"
    VOICE = "voice"
    DM = "dm"


class Notification(_Model):
    notification_id: str
    channel: NotificationChannel
    recipient: str = Field(description="User id or channel-native address")
    title: str
    body: str
    incident_id: Optional[str] = None
    requires_ack: bool = False


class DeliveryStatus(str, Enum):
    QUEUED = "queued"
    SENT = "sent"
    DELIVERED = "delivered"
    FAILED = "failed"
    ACKED = "acked"


class DeliveryReceipt(_Model):
    notification_id: str
    status: DeliveryStatus
    at: datetime
    detail: Optional[str] = None


# --- knowledge ---------------------------------------------------------------

class KnowledgeDocType(str, Enum):
    RUNBOOK = "runbook"
    PIR = "pir"
    NOTE = "note"


class KnowledgeDocument(_Model):
    doc_id: str
    title: str
    body: str
    doc_type: KnowledgeDocType = KnowledgeDocType.NOTE
    tags: list[str] = Field(default_factory=list)
    updated_at: datetime


class KnowledgeHit(_Model):
    doc_id: str
    title: str
    snippet: str
    score: float = Field(ge=0)


# --- chaos -------------------------------------------------------------------

class ChaosFaultType(str, Enum):
    LATENCY = "latency"
    ERROR_RATE = "error_rate"
    KILL = "kill"
    CPU = "cpu"
    MEMORY = "memory"
    DEPENDENCY_DOWN = "dependency_down"


class ChaosScenario(_Model):
    scenario_id: str
    name: str
    description: str
    fault_type: ChaosFaultType
    target: str
    params_schema: dict[str, Any] = Field(default_factory=dict)
    max_duration_s: float = Field(gt=0)
    blast_radius: Optional[str] = None
    abort_conditions: list[str] = Field(default_factory=list)
    simulated: bool = True


class ChaosInjectionState(str, Enum):
    REQUESTED = "requested"
    ACTIVE = "active"
    COMPLETED = "completed"
    ABORTED = "aborted"
    FAILED = "failed"


class ChaosInjection(_Model):
    injection_id: str
    scenario_id: str
    target: str
    params: dict[str, Any] = Field(default_factory=dict)
    state: ChaosInjectionState
    scope: str = Field(description="Required authorization scope, e.g. drill:<drill_id>")
    started_at: Optional[datetime] = None
    ended_at: Optional[datetime] = None
