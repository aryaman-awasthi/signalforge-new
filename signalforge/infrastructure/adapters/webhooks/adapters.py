"""Normalisers for inbound alert and deployment webhooks (REQ-031).

Both are pure translations with no I/O, which is what lets them be exercised
against real payload shapes rather than against a server.

Three translation decisions are deliberate and documented where they are made:

- An unknown severity becomes `warning`, never `critical` and never `info`.
  Promoting an unrecognised label to critical trains people to ignore pages;
  demoting it to info hides a real one.
- Grafana's "not resolved yet" sentinel (`0001-01-01T00:00:00Z`) becomes `None`,
  not a timestamp in year one, because every downstream duration would be wrong
  in an interesting way.
- A deployment webhook missing an explicit status is inferred from its event
  name and from whether it carries a completion time - and if neither says, the
  payload is rejected. Guessing "completed" would close a change window that is
  still open.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping, Optional

from signalforge.contracts.canonical import (
    Alert,
    AlertSeverity,
    AlertStatus,
    ChangeEvent,
    ChangeStatus,
    ChangeType,
)
from signalforge.contracts.errors import AdapterError, NotSupportedError
from signalforge.contracts.envelope import Topics
from signalforge.contracts.spi import AlertsAdapter, ChangesAdapter
from signalforge.infrastructure.adapters.base import GenericAdapter

SEVERITY_ALIASES: Mapping[str, AlertSeverity] = {
    "critical": AlertSeverity.CRITICAL,
    "crit": AlertSeverity.CRITICAL,
    "fatal": AlertSeverity.CRITICAL,
    "page": AlertSeverity.CRITICAL,
    "p1": AlertSeverity.CRITICAL,
    "sev1": AlertSeverity.CRITICAL,
    "error": AlertSeverity.CRITICAL,
    "warning": AlertSeverity.WARNING,
    "warn": AlertSeverity.WARNING,
    "p2": AlertSeverity.WARNING,
    "sev2": AlertSeverity.WARNING,
    "info": AlertSeverity.INFO,
    "informational": AlertSeverity.INFO,
    "none": AlertSeverity.INFO,
    "ok": AlertSeverity.INFO,
}

CHANGE_TYPE_ALIASES: Mapping[str, ChangeType] = {
    "deployment": ChangeType.DEPLOYMENT,
    "deploy": ChangeType.DEPLOYMENT,
    "release": ChangeType.DEPLOYMENT,
    "config": ChangeType.CONFIG,
    "configuration": ChangeType.CONFIG,
    "infra": ChangeType.INFRA,
    "infrastructure": ChangeType.INFRA,
    "feature_flag": ChangeType.FEATURE_FLAG,
    "flag": ChangeType.FEATURE_FLAG,
}

CHANGE_STATUS_ALIASES: Mapping[str, ChangeStatus] = {
    "started": ChangeStatus.STARTED,
    "start": ChangeStatus.STARTED,
    "in_progress": ChangeStatus.STARTED,
    "running": ChangeStatus.STARTED,
    "completed": ChangeStatus.COMPLETED,
    "complete": ChangeStatus.COMPLETED,
    "success": ChangeStatus.COMPLETED,
    "succeeded": ChangeStatus.COMPLETED,
    "failed": ChangeStatus.FAILED,
    "failure": ChangeStatus.FAILED,
    "error": ChangeStatus.FAILED,
    "rolled_back": ChangeStatus.ROLLED_BACK,
    "rollback": ChangeStatus.ROLLED_BACK,
    "reverted": ChangeStatus.ROLLED_BACK,
}

EVENT_STATUS = {
    Topics.DEPLOYMENT_STARTED: ChangeStatus.STARTED,
    Topics.DEPLOYMENT_COMPLETED: ChangeStatus.COMPLETED,
}

GRAFANA_NULL_TIME = "0001-01-01T00:00:00Z"


class PushAdapter(GenericAdapter):
    async def ping(self) -> bool:
        """SignalForge is the server for a push binding, so readiness is a
        property of this process and is genuinely true when asked. Whether the
        sender has actually posted anything is freshness, recorded by the ingest
        API, not something a ping can discover."""
        return True


class GrafanaAlertWebhookAdapter(PushAdapter, AlertsAdapter):
    def normalize(self, raw: dict[str, Any]) -> list[Alert]:
        if not isinstance(raw, Mapping):
            raise AdapterError("a Grafana alert webhook payload must be an object")
        alerts = raw.get("alerts")
        if alerts is None:
            # Grafana's legacy webhook posts a single alert rather than a batch.
            alerts = [raw]
        if not isinstance(alerts, list) or not alerts:
            raise AdapterError("alert payload carries no alerts")
        return [self._alert(item, index) for index, item in enumerate(alerts)]

    async def fetch_active(self) -> list[Alert]:
        raise NotSupportedError(
            "grafana-alert-webhook receives alerts; it does not poll. Bind a "
            "pull-mode alerts adapter if the source must be scraped."
        )

    def _alert(self, item: Any, index: int) -> Alert:
        if not isinstance(item, Mapping):
            raise AdapterError(f"alert {index} is not an object")
        labels = {str(k): str(v) for k, v in (item.get("labels") or {}).items()}
        default_service = self.config("default_service")
        if default_service and not labels.get("service"):
            # Correlation needs a service. A source that cannot label one names
            # it on the binding instead, which is a statement by the operator
            # rather than a guess by the adapter.
            labels["service"] = str(default_service)
        annotations = {
            str(k): self.redact(str(v))
            for k, v in (item.get("annotations") or {}).items()
        }
        status = str(item.get("status", "firing")).lower()
        if status not in ("firing", "resolved"):
            raise AdapterError(
                f"alert {index} has status {status!r}; expected firing or resolved"
            )
        name = (
            labels.get("alertname")
            or item.get("labels", {}).get("alertname")
            or str(item.get("name") or item.get("ruleName") or "").strip()
        )
        if not name:
            raise AdapterError(f"alert {index} has no alertname label and no name")
        alert_id = str(
            item.get("fingerprint") or item.get("alert_id") or item.get("id") or ""
        ).strip()
        if not alert_id:
            # Without a stable id from the source the platform cannot tell a
            # re-notification from a new alert, so one is derived from the
            # identity the source does give: its name and its label set.
            alert_id = _stable_id(name, labels)
        return Alert(
            alert_id=alert_id,
            name=name,
            severity=severity_of(labels.get("severity")),
            status=AlertStatus(status),
            starts_at=_time(item.get("startsAt") or item.get("starts_at"), required=True),
            ends_at=_time(item.get("endsAt") or item.get("ends_at")),
            labels=labels,
            annotations=annotations,
        )


class DeployWebhookAdapter(PushAdapter, ChangesAdapter):
    def normalize(self, raw: dict[str, Any]) -> ChangeEvent:
        if not isinstance(raw, Mapping):
            raise AdapterError("a deployment webhook payload must be an object")
        event = str(raw.get("event") or raw.get("topic") or "").strip()
        service = str(
            raw.get("service")
            or raw.get("service_name")
            or raw.get("app")
            or self.config("default_service")
            or ""
        ).strip()
        if not service:
            raise AdapterError(
                "deployment webhook must name the service it changed "
                "('service', 'service_name' or 'app')"
            )
        change_id = str(
            raw.get("change_id") or raw.get("deployment_id") or raw.get("id") or ""
        ).strip()
        if not change_id:
            raise AdapterError(
                "deployment webhook must carry a stable id ('change_id', "
                "'deployment_id' or 'id') so a start and its completion can be paired"
            )
        started_at = _time(
            raw.get("started_at") or raw.get("timestamp") or raw.get("occurred_at"),
            required=True,
        )
        completed_at = _time(raw.get("completed_at") or raw.get("finished_at"))
        return ChangeEvent(
            change_id=change_id,
            change_type=_change_type(raw.get("change_type") or raw.get("type")),
            service=service,
            status=_change_status(raw.get("status") or raw.get("state"), event, completed_at),
            started_at=started_at,
            completed_at=completed_at,
            version=_text(raw.get("version") or raw.get("release")),
            actor=_text(raw.get("actor") or raw.get("triggered_by")),
            diff_ref=_text(raw.get("diff_ref") or raw.get("commit") or raw.get("pr")),
        )

    async def list_changes(self, start: datetime, end: datetime) -> list[ChangeEvent]:
        raise NotSupportedError(
            "deploy-webhook receives deployments; it does not poll a CI system. "
            "Bind a pull-mode changes adapter for that."
        )


# --- shared translation -------------------------------------------------------


def severity_of(raw: Any) -> AlertSeverity:
    return SEVERITY_ALIASES.get(str(raw or "").strip().lower(), AlertSeverity.WARNING)


def _change_type(raw: Any) -> ChangeType:
    if raw in (None, ""):
        return ChangeType.DEPLOYMENT
    value = CHANGE_TYPE_ALIASES.get(str(raw).strip().lower())
    if value is None:
        raise AdapterError(
            f"change_type {raw!r} is not one of {sorted(CHANGE_TYPE_ALIASES)}"
        )
    return value


def _change_status(
    raw: Any, event: str, completed_at: Optional[datetime]
) -> ChangeStatus:
    if raw not in (None, ""):
        value = CHANGE_STATUS_ALIASES.get(str(raw).strip().lower())
        if value is None:
            raise AdapterError(
                f"deployment status {raw!r} is not one of "
                f"{sorted(set(CHANGE_STATUS_ALIASES))}"
            )
        return value
    if event in EVENT_STATUS:
        return EVENT_STATUS[event]
    if completed_at is not None:
        return ChangeStatus.COMPLETED
    raise AdapterError(
        "deployment webhook has no status and no event name; refusing to guess, "
        "because guessing 'completed' would close a change window that is still open"
    )


def _time(raw: Any, *, required: bool = False) -> Optional[datetime]:
    if raw in (None, "", GRAFANA_NULL_TIME):
        if required:
            raise AdapterError("payload is missing a required timestamp")
        return None
    if isinstance(raw, datetime):
        moment = raw
    elif isinstance(raw, (int, float)):
        moment = datetime.fromtimestamp(float(raw), tz=timezone.utc)
    else:
        text = str(raw).strip().replace("Z", "+00:00")
        try:
            moment = datetime.fromisoformat(text)
        except ValueError as exc:
            raise AdapterError(f"{raw!r} is not an ISO-8601 timestamp") from exc
    if moment.tzinfo is None:
        # The envelope contract requires tz-aware timestamps. A naive one from a
        # webhook is read as UTC rather than rejected, because that is what every
        # sender that omits an offset means, and the alternative is dropping the
        # alert entirely.
        moment = moment.replace(tzinfo=timezone.utc)
    if moment.year <= 1:
        if required:
            raise AdapterError(f"{raw!r} is a null timestamp where one is required")
        return None
    return moment


def _text(raw: Any) -> Optional[str]:
    if raw in (None, ""):
        return None
    return str(raw)


def _stable_id(name: str, labels: Mapping[str, str]) -> str:
    import hashlib

    material = name + "\x00" + "\x00".join(f"{k}={v}" for k, v in sorted(labels.items()))
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]
