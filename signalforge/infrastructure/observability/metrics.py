"""A small Prometheus text-exposition registry (REQ-025).

Deliberately hand-rolled rather than pulling in a client library: the platform
needs counters, gauges and one histogram, and owning the renderer means the rule
that matters here can be enforced in code. That rule is the label set. /metrics
is scraped without a token, so every metric declares exactly which labels it
accepts and an undeclared one - a tenant id, an incident id - is a hard error
rather than a silent cross-tenant disclosure.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Sequence

DEFAULT_BUCKETS: tuple[float, ...] = (
    0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0
)

FORBIDDEN_LABEL_NAMES = frozenset(
    {"tenant", "tenant_id", "org", "org_id", "incident", "incident_id",
     "user", "user_id", "email", "actor_id"}
)


class MetricType(str, Enum):
    COUNTER = "counter"
    GAUGE = "gauge"
    HISTOGRAM = "histogram"


@dataclass
class Metric:
    name: str
    type: MetricType
    help: str
    label_names: tuple[str, ...] = ()
    buckets: tuple[float, ...] = ()
    values: dict[tuple[str, ...], float] = field(default_factory=dict)
    counts: dict[tuple[str, ...], list[float]] = field(default_factory=dict)
    sums: dict[tuple[str, ...], float] = field(default_factory=dict)
    totals: dict[tuple[str, ...], int] = field(default_factory=dict)


class MetricsRegistry:
    def __init__(self) -> None:
        self._metrics: dict[str, Metric] = {}

    @property
    def declared(self) -> Sequence[Metric]:
        return list(self._metrics.values())

    def declare(
        self,
        name: str,
        type: MetricType,
        help: str,
        label_names: Sequence[str] = (),
        buckets: Optional[Sequence[float]] = None,
    ) -> None:
        forbidden = FORBIDDEN_LABEL_NAMES & set(label_names)
        if forbidden:
            raise ValueError(
                f"{name} may not carry identifying labels {sorted(forbidden)}: "
                "/metrics is unauthenticated"
            )
        self._metrics[name] = Metric(
            name=name,
            type=type,
            help=help,
            label_names=tuple(label_names),
            buckets=tuple(buckets if buckets is not None else DEFAULT_BUCKETS)
            if type is MetricType.HISTOGRAM
            else (),
        )

    def increment(
        self, name: str, labels: Optional[dict[str, str]] = None, value: float = 1.0
    ) -> None:
        metric = self._get(name, MetricType.COUNTER)
        key = self._key(metric, labels)
        metric.values[key] = metric.values.get(key, 0.0) + value

    def set_gauge(
        self, name: str, value: float, labels: Optional[dict[str, str]] = None
    ) -> None:
        metric = self._get(name, MetricType.GAUGE)
        metric.values[self._key(metric, labels)] = float(value)

    def observe(
        self, name: str, value: float, labels: Optional[dict[str, str]] = None
    ) -> None:
        metric = self._get(name, MetricType.HISTOGRAM)
        key = self._key(metric, labels)
        metric.counts.setdefault(key, []).append(float(value))
        metric.sums[key] = metric.sums.get(key, 0.0) + float(value)
        metric.totals[key] = metric.totals.get(key, 0) + 1

    def value(self, name: str, labels: Optional[dict[str, str]] = None) -> float:
        metric = self._metrics[name]
        return metric.values.get(self._key(metric, labels), 0.0)

    def render(self) -> str:
        lines: list[str] = []
        for metric in self._metrics.values():
            lines.append(f"# HELP {metric.name} {metric.help}")
            lines.append(f"# TYPE {metric.name} {metric.type.value}")
            if metric.type is MetricType.HISTOGRAM:
                lines.extend(self._render_histogram(metric))
            else:
                for key, value in sorted(metric.values.items()):
                    lines.append(f"{metric.name}{self._labels(metric, key)} {_num(value)}")
        return "\n".join(lines) + "\n"

    # -- internals ---------------------------------------------------------

    def _render_histogram(self, metric: Metric) -> list[str]:
        lines: list[str] = []
        for key, observations in sorted(metric.counts.items()):
            for bucket in metric.buckets:
                count = sum(1 for o in observations if o <= bucket)
                # Bucket bounds keep their float form: le="1.0" rather than le="1",
                # so the boundary a scrape sees is the one that was declared.
                lines.append(
                    f"{metric.name}_bucket"
                    f"{self._labels(metric, key, extra=('le', repr(float(bucket))))} {count}"
                )
            lines.append(
                f"{metric.name}_bucket"
                f"{self._labels(metric, key, extra=('le', '+Inf'))} {len(observations)}"
            )
            lines.append(f"{metric.name}_sum{self._labels(metric, key)} {_num(metric.sums[key])}")
            lines.append(f"{metric.name}_count{self._labels(metric, key)} {metric.totals[key]}")
        return lines

    def _get(self, name: str, expected: MetricType) -> Metric:
        metric = self._metrics.get(name)
        if metric is None:
            raise KeyError(f"metric {name!r} was never declared")
        if metric.type is not expected:
            raise TypeError(f"metric {name!r} is a {metric.type.value}, not a {expected.value}")
        return metric

    @staticmethod
    def _key(metric: Metric, labels: Optional[dict[str, str]]) -> tuple[str, ...]:
        labels = labels or {}
        unknown = set(labels) - set(metric.label_names)
        if unknown:
            raise ValueError(
                f"metric {metric.name!r} does not declare labels {sorted(unknown)}"
            )
        return tuple(str(labels.get(name, "")) for name in metric.label_names)

    @staticmethod
    def _labels(
        metric: Metric, key: tuple[str, ...], extra: Optional[tuple[str, str]] = None
    ) -> str:
        pairs = [
            f'{name}="{_escape(value)}"' for name, value in zip(metric.label_names, key)
        ]
        if extra is not None:
            pairs.append(f'{extra[0]}="{extra[1]}"')
        return "{" + ",".join(pairs) + "}" if pairs else ""


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _num(value: float) -> str:
    if value in (math.inf, -math.inf) or value != value:
        return {math.inf: "+Inf", -math.inf: "-Inf"}.get(value, "NaN")
    return str(int(value)) if float(value).is_integer() else repr(float(value))


def build_registry() -> MetricsRegistry:
    """The platform's own metrics. Labels are deliberately low-cardinality and
    carry nothing that identifies a tenant, an incident or a person."""
    registry = MetricsRegistry()
    registry.declare(
        "signalforge_build_info", MetricType.GAUGE, "Build and runtime information",
        ("version", "clock_source"),
    )
    registry.declare(
        "signalforge_http_requests_total", MetricType.COUNTER,
        "HTTP requests served", ("method", "route", "status"),
    )
    registry.declare(
        "signalforge_http_request_duration_seconds", MetricType.HISTOGRAM,
        "HTTP request duration", ("method", "route"),
    )
    registry.declare(
        "signalforge_events_published_total", MetricType.COUNTER,
        "Events appended to the outbox", ("topic",),
    )
    registry.declare(
        "signalforge_events_dispatched_total", MetricType.COUNTER,
        "Events delivered to a consumer group", ("group",),
    )
    registry.declare(
        "signalforge_events_duplicate_total", MetricType.COUNTER,
        "Redeliveries suppressed by the idempotency ledger", ("group",),
    )
    registry.declare(
        "signalforge_event_handler_failures_total", MetricType.COUNTER,
        "Handler invocations that raised", ("group",),
    )
    registry.declare(
        "signalforge_dead_letters_total", MetricType.COUNTER,
        "Events parked in the dead-letter store", ("group",),
    )
    registry.declare(
        "signalforge_dead_letters_requeued_total", MetricType.COUNTER,
        "Dead letters successfully redelivered", ("group",),
    )
    registry.declare(
        "signalforge_events_consumed_total", MetricType.COUNTER,
        "Events observed by the bus metrics consumer", ("topic",),
    )
    registry.declare(
        "signalforge_event_seq", MetricType.GAUGE,
        "Highest recorded event seq", (),
    )
    registry.declare(
        "signalforge_consumer_lag", MetricType.GAUGE,
        "Events behind the head of the log, per consumer group", ("group",),
    )
    registry.declare(
        "signalforge_dead_letters", MetricType.GAUGE,
        "Dead letters currently in each state", ("state",),
    )
    registry.declare(
        "signalforge_connector_health", MetricType.GAUGE,
        "Connector health: 1 up, 0.5 degraded or unknown, 0 down", ("connector",),
    )
    registry.declare(
        # Bindings are counted, never named: a binding id belongs to one tenant
        # and /metrics has no session. Which binding is which is answered by
        # GET /connectors/health behind RBAC.
        "signalforge_connector_bindings", MetricType.GAUGE,
        "Connector bindings currently in each health state", ("state",),
    )
    registry.declare(
        "signalforge_worker_job_failures_total", MetricType.COUNTER,
        "Scheduled job runs that raised", ("job",),
    )
    registry.declare(
        # Channel and outcome only. Who was notified about what is exactly the
        # kind of thing an unauthenticated scrape must never carry.
        "signalforge_notifications_total", MetricType.COUNTER,
        "Notifications handed to a channel adapter, by delivery outcome",
        ("channel", "status"),
    )
    registry.declare(
        "signalforge_adapter_calls_total", MetricType.COUNTER,
        "Adapter capability invocations, by outcome", ("adapter", "capability", "outcome"),
    )
    registry.declare(
        "signalforge_adapter_call_duration_seconds", MetricType.HISTOGRAM,
        "Adapter capability invocation duration", ("adapter", "capability"),
    )
    registry.declare(
        "signalforge_retention_pruned_total", MetricType.COUNTER,
        "Signal-plane events removed by retention", ("plane",),
    )
    registry.declare(
        # Stream and outcome only. Which application posted what belongs to a
        # tenant, and /metrics has no session; GET /ingest-keys answers that
        # question behind manage_apps.
        "signalforge_ingest_items_total", MetricType.COUNTER,
        "Items posted to the ingest API, by outcome", ("stream", "outcome"),
    )
    registry.declare(
        # `reason` is a closed vocabulary mapped in the ingest service, so a
        # hostile sender cannot mint metric series by varying its input.
        "signalforge_ingest_refused_total", MetricType.COUNTER,
        "Ingest requests refused before their body was parsed", ("stream", "reason"),
    )
    # Detection (S4-T01). None of these can name an application: an app_id is a
    # tenant's own vocabulary - "checkout", "settlement" - and /metrics has no
    # session. The counts are install-wide and the authenticated
    # GET /apps/{app_id}/detection answers whose.
    registry.declare(
        "signalforge_detection_polls_total", MetricType.COUNTER,
        "Metric poll attempts, by outcome", ("outcome",),
    )
    registry.declare(
        "signalforge_detection_rollups_total", MetricType.COUNTER,
        "Five-second rollups, written or refused as a duplicate bucket",
        ("outcome",),
    )
    registry.declare(
        "signalforge_detection_probes_total", MetricType.COUNTER,
        "Synthetic probe runs, by verdict - unknown is not a failure",
        ("verdict",),
    )
    registry.declare(
        "signalforge_detection_signals", MetricType.GAUGE,
        "Mapped signals currently in each detection status", ("status",),
    )
    registry.declare(
        "signalforge_detection_stance", MetricType.GAUGE,
        "Whether the last evaluated application is in this stance", ("stance",),
    )
    # Incident lifecycle (S4-T02). Install-wide counts over closed vocabularies:
    # the states and severities are enums, so a scrape can show that a P0 is
    # running without being able to say whose it is.
    registry.declare(
        "signalforge_incident_transitions_total", MetricType.COUNTER,
        "Incident lifecycle transitions, by the states moved between and the "
        "kind of actor that moved them",
        ("from_state", "to_state", "actor"),
    )
    registry.declare(
        "signalforge_incidents_open", MetricType.GAUGE,
        "Incidents currently active or recovering, by severity", ("severity",),
    )
    registry.declare(
        "signalforge_incident_ticket_sync_total", MetricType.COUNTER,
        "ITSM ticket sync attempts, by outcome - uncertain is not a failure",
        ("outcome",),
    )
    registry.declare(
        "signalforge_change_windows_total", MetricType.COUNTER,
        "Change events folded into a change window, by status", ("status", "open"),
    )
    registry.declare(
        "signalforge_change_windows_open", MetricType.GAUGE,
        "Changes announced as started and never announced as completed", (),
    )
    # The war room (S4-T03). Every label is a closed vocabulary - a state, a
    # severity, a role from the frozen matrix - so an unauthenticated scrape can
    # show that a P0 has an unanswered page without being able to say whose.
    registry.declare(
        "signalforge_war_rooms_total", MetricType.COUNTER,
        "War rooms opened, by the severity they were declared at", ("severity",),
    )
    registry.declare(
        "signalforge_war_room_membership_gaps_total", MetricType.COUNTER,
        "Required roles nobody was bound to when a room opened", ("role",),
    )
    registry.declare(
        "signalforge_pages_total", MetricType.COUNTER,
        "Pages, by the state they reached and the severity they carried",
        ("state", "severity"),
    )
    registry.declare(
        "signalforge_page_escalations_total", MetricType.COUNTER,
        "Escalations, by outcome - `exhausted` means nobody was left to page",
        ("outcome",),
    )
    registry.declare(
        "signalforge_pages_overdue", MetricType.GAUGE,
        "Pages past their acknowledgement deadline and still unanswered", (),
    )
    registry.declare(
        "signalforge_incident_tasks_total", MetricType.COUNTER,
        "Incident tasks raised, by where they came from and whether an owner "
        "could be resolved",
        ("source", "assigned"),
    )
    registry.declare(
        "signalforge_incident_tasks_completed_total", MetricType.COUNTER,
        "Incident tasks completed, by where they came from", ("source",),
    )
    # The model path (S5-T02). Every label is closed: a classification, a
    # call outcome and the degraded-reason vocabulary. No purpose, no model id
    # from a response and certainly no prompt - /metrics is unauthenticated, and
    # a cardinality an upstream gateway could grow is a cardinality it can be
    # used to attack.
    registry.declare(
        "signalforge_llm_calls_total", MetricType.COUNTER,
        "Model calls, by classification and outcome - `reason` is set only when "
        "the outcome is degraded",
        ("model_class", "outcome", "reason"),
    )
    registry.declare(
        "signalforge_llm_tokens_total", MetricType.COUNTER,
        "Tokens the gateway reported, by classification and direction",
        ("model_class", "kind"),
    )
    registry.declare(
        "signalforge_llm_degraded_total", MetricType.COUNTER,
        "Calls that fell back to the deterministic answer, by reason", ("reason",),
    )
    return registry
