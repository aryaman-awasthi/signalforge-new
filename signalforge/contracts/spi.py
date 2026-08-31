"""Adapter SPI: the 13 abstract interfaces every integration implements.

Frozen contract (CLAUDE.md rule 3). Rules for implementors:
- All methods are async. Vendor errors are translated to contracts.errors.
- An adapter may not support every method of its kind: unsupported optional
  capabilities raise NotSupportedError, and the registry manifest (Sprint 3)
  must declare the capabilities that are supported.
- Push-mode adapters implement normalize() to turn a raw inbound payload into
  canonical models; pull-mode adapters implement the fetching methods.
- Adapters translate; they never decide. Policy, RBAC and budgets live above.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any, Optional

from signalforge.contracts.canonical import (
    ActionDefinition,
    ActionExecution,
    Alert,
    ChangeEvent,
    ChaosInjection,
    ChaosScenario,
    CollabMessage,
    DeliveryReceipt,
    HealthReport,
    KnowledgeDocument,
    KnowledgeHit,
    LogRecord,
    MetricSeries,
    Notification,
    ProbeResult,
    ProbeSpec,
    Ticket,
    TopologySnapshot,
    TraceSpan,
)
from signalforge.contracts.envelope import AdapterKind


class Adapter(ABC):
    """Common base. `kind` is fixed per subclass and must match the registry manifest."""

    kind: AdapterKind

    @abstractmethod
    async def ping(self) -> bool:
        """Cheap liveness/auth check used by the connector health loop."""


class MetricsAdapter(Adapter):
    kind = AdapterKind.METRICS

    @abstractmethod
    async def query_instant(self, query: str, at: datetime) -> list[MetricSeries]: ...

    @abstractmethod
    async def query_range(
        self, query: str, start: datetime, end: datetime, step_s: float
    ) -> list[MetricSeries]: ...


class LogsAdapter(Adapter):
    kind = AdapterKind.LOGS

    @abstractmethod
    async def search(
        self, query: str, start: datetime, end: datetime, limit: int
    ) -> list[LogRecord]: ...

    @abstractmethod
    async def aggregate(
        self, query: str, start: datetime, end: datetime, group_by: str
    ) -> dict[str, int]: ...

    @abstractmethod
    async def sample(
        self, query: str, start: datetime, end: datetime, n: int
    ) -> list[LogRecord]: ...


class TracesAdapter(Adapter):
    kind = AdapterKind.TRACES

    @abstractmethod
    async def search(
        self,
        start: datetime,
        end: datetime,
        limit: int,
        service: Optional[str] = None,
        operation: Optional[str] = None,
        errors_only: bool = False,
    ) -> list[TraceSpan]: ...

    @abstractmethod
    async def get_trace(self, trace_id: str) -> list[TraceSpan]: ...


class HealthAdapter(Adapter):
    kind = AdapterKind.HEALTH

    @abstractmethod
    async def get_health(self) -> HealthReport: ...

    @abstractmethod
    async def run_probe(self, probe: ProbeSpec) -> ProbeResult: ...


class AlertsAdapter(Adapter):
    kind = AdapterKind.ALERTS

    @abstractmethod
    def normalize(self, raw: dict[str, Any]) -> list[Alert]:
        """Translate one inbound webhook payload into canonical alerts. Pure."""

    @abstractmethod
    async def fetch_active(self) -> list[Alert]: ...


class ChangesAdapter(Adapter):
    kind = AdapterKind.CHANGES

    @abstractmethod
    def normalize(self, raw: dict[str, Any]) -> ChangeEvent:
        """Translate one inbound change/deploy payload. Pure."""

    @abstractmethod
    async def list_changes(self, start: datetime, end: datetime) -> list[ChangeEvent]: ...


class TopologyAdapter(Adapter):
    kind = AdapterKind.TOPOLOGY

    @abstractmethod
    async def fetch(self) -> TopologySnapshot: ...

    @abstractmethod
    def normalize(self, raw: dict[str, Any]) -> TopologySnapshot:
        """Translate an uploaded/imported topology document. Pure."""


class CollaborationAdapter(Adapter):
    kind = AdapterKind.COLLABORATION

    @abstractmethod
    async def post(
        self, channel: str, text: str, thread_id: Optional[str] = None
    ) -> CollabMessage: ...

    @abstractmethod
    async def fetch(
        self, channel: str, since: datetime, limit: int
    ) -> list[CollabMessage]: ...


class ItsmAdapter(Adapter):
    kind = AdapterKind.ITSM

    @abstractmethod
    async def create(self, ticket: Ticket) -> Ticket: ...

    @abstractmethod
    async def update(self, ticket_id: str, fields: dict[str, Any]) -> Ticket: ...

    @abstractmethod
    async def get(self, ticket_id: str) -> Ticket: ...


class ActionsAdapter(Adapter):
    kind = AdapterKind.ACTIONS

    @abstractmethod
    async def manifest(self) -> list[ActionDefinition]:
        """Actions the target app publishes, from its validated action manifest."""

    @abstractmethod
    async def execute(
        self, action_id: str, params: dict[str, Any], idempotency_key: str
    ) -> ActionExecution: ...

    @abstractmethod
    async def get_execution(self, idempotency_key: str) -> Optional[ActionExecution]:
        """Look up an execution by idempotency key; None if never applied.
        This is the reconcile-before-retry primitive: after an UNCERTAIN
        outcome the gateway MUST call this before any retry (Sprint 6)."""


class NotificationsAdapter(Adapter):
    kind = AdapterKind.NOTIFICATIONS

    @abstractmethod
    async def send(self, notification: Notification) -> DeliveryReceipt: ...

    @abstractmethod
    async def status(self, notification_id: str) -> DeliveryReceipt: ...


class KnowledgeAdapter(Adapter):
    kind = AdapterKind.KNOWLEDGE

    @abstractmethod
    async def index(self, document: KnowledgeDocument) -> None: ...

    @abstractmethod
    async def search(self, query: str, k: int) -> list[KnowledgeHit]: ...


class ChaosAdapter(Adapter):
    kind = AdapterKind.CHAOS

    @abstractmethod
    async def manifest(self) -> list[ChaosScenario]:
        """Scenarios the target app publishes, from its validated chaos manifest."""

    @abstractmethod
    async def inject(
        self, scenario_id: str, params: dict[str, Any], scope: str, idempotency_key: str
    ) -> ChaosInjection:
        """`scope` carries the drill authorization (e.g. drill:<id>); injecting
        outside an authorized drill scope must be refused above this SPI."""

    @abstractmethod
    async def status(self, injection_id: str) -> ChaosInjection: ...

    @abstractmethod
    async def abort(self, injection_id: str) -> ChaosInjection: ...


ADAPTER_SPI_BY_KIND: dict[AdapterKind, type[Adapter]] = {
    AdapterKind.METRICS: MetricsAdapter,
    AdapterKind.LOGS: LogsAdapter,
    AdapterKind.TRACES: TracesAdapter,
    AdapterKind.HEALTH: HealthAdapter,
    AdapterKind.ALERTS: AlertsAdapter,
    AdapterKind.CHANGES: ChangesAdapter,
    AdapterKind.TOPOLOGY: TopologyAdapter,
    AdapterKind.COLLABORATION: CollaborationAdapter,
    AdapterKind.ITSM: ItsmAdapter,
    AdapterKind.ACTIONS: ActionsAdapter,
    AdapterKind.NOTIFICATIONS: NotificationsAdapter,
    AdapterKind.KNOWLEDGE: KnowledgeAdapter,
    AdapterKind.CHAOS: ChaosAdapter,
}
