"""Inbound webhook normalisation (REQ-031).

These adapters have no I/O, so the tests are about the translation and about the
three places it deliberately refuses to guess. A normaliser that mislabels a
severity, dates an unresolved alert to the year one, or decides a deployment
"completed" because the payload was quiet is a misled responder at 3am.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from signalforge.contracts.canonical import (
    AlertSeverity,
    AlertStatus,
    ChangeStatus,
    ChangeType,
)
from signalforge.contracts.errors import AdapterError, NotSupportedError
from signalforge.tests.adapters.conftest import connect

pytestmark = pytest.mark.anyio


@pytest.fixture
def hooks(api):
    signup = api.signup()
    token = signup["tokens"]["access_token"]
    tenant_id = signup["org"]["id"]

    class Hooks:
        async def adapter(self, adapter_id, **kwargs):
            binding = connect(
                api,
                token,
                adapter_id,
                credentials={"ingest_secret": "s3cr3t"},
                **kwargs,
            )
            resolved = await api.container.invoker.resolve(
                tenant_id, binding["id"], "normalize"
            )
            return resolved.adapter

    return Hooks()


GRAFANA_BATCH = {
    "receiver": "signalforge",
    "status": "firing",
    "alerts": [
        {
            "status": "firing",
            "fingerprint": "abc123",
            "labels": {
                "alertname": "CheckoutErrorRate",
                "severity": "critical",
                "service": "checkout",
            },
            "annotations": {"summary": "error rate 12% for ada@example.com"},
            "startsAt": "2026-09-01T11:58:00Z",
            "endsAt": "0001-01-01T00:00:00Z",
        },
        {
            "status": "resolved",
            "fingerprint": "def456",
            "labels": {"alertname": "CartLatency", "severity": "sev2"},
            "annotations": {},
            "startsAt": "2026-09-01T11:50:00Z",
            "endsAt": "2026-09-01T11:55:00Z",
        },
    ],
}


@pytest.mark.req("REQ-031")
async def test_a_grafana_batch_becomes_canonical_alerts(hooks):
    adapter = await hooks.adapter("grafana-alert-webhook")
    alerts = adapter.normalize(GRAFANA_BATCH)

    assert [alert.alert_id for alert in alerts] == ["abc123", "def456"]
    firing, resolved = alerts
    assert firing.severity is AlertSeverity.CRITICAL
    assert firing.status is AlertStatus.FIRING
    assert firing.starts_at == datetime(2026, 9, 1, 11, 58, tzinfo=timezone.utc)
    assert firing.ends_at is None, "Grafana's unresolved sentinel is not a real time"
    assert resolved.severity is AlertSeverity.WARNING, "sev2 is an alias for warning"
    assert resolved.ends_at == datetime(2026, 9, 1, 11, 55, tzinfo=timezone.utc)


@pytest.mark.req("REQ-031")
async def test_alert_annotations_are_redacted(hooks):
    adapter = await hooks.adapter("grafana-alert-webhook")
    alerts = adapter.normalize(GRAFANA_BATCH)
    assert "ada@example.com" not in alerts[0].annotations["summary"]


@pytest.mark.req("REQ-031")
async def test_an_unknown_severity_becomes_warning_not_critical(hooks):
    """Promoting an unrecognised label to critical trains people to ignore
    pages; demoting it to info hides a real one."""
    adapter = await hooks.adapter("grafana-alert-webhook")
    payload = {
        "alerts": [
            {
                "status": "firing",
                "fingerprint": "x",
                "labels": {"alertname": "Odd", "severity": "spicy"},
                "startsAt": "2026-09-01T11:58:00Z",
            }
        ]
    }
    assert adapter.normalize(payload)[0].severity is AlertSeverity.WARNING


@pytest.mark.req("REQ-031")
async def test_an_alert_without_a_fingerprint_gets_a_stable_derived_id(hooks):
    """Without a stable id the platform cannot tell a re-notification from a
    new alert, so one is derived from the identity the source does give."""
    adapter = await hooks.adapter("grafana-alert-webhook")
    payload = {
        "status": "firing",
        "labels": {"alertname": "NoFingerprint", "severity": "critical"},
        "startsAt": "2026-09-01T11:58:00Z",
    }
    first = adapter.normalize(dict(payload))[0]
    second = adapter.normalize(dict(payload))[0]
    assert first.alert_id == second.alert_id
    assert len(first.alert_id) == 32

    different = adapter.normalize(
        {**payload, "labels": {"alertname": "NoFingerprint", "severity": "critical",
                               "region": "eu"}}
    )[0]
    assert different.alert_id != first.alert_id


@pytest.mark.req("REQ-031")
async def test_a_default_service_named_on_the_binding_labels_the_alert(hooks):
    adapter = await hooks.adapter(
        "grafana-alert-webhook", config={"default_service": "checkout"}
    )
    alerts = adapter.normalize(
        {
            "alerts": [
                {
                    "status": "firing",
                    "fingerprint": "x",
                    "labels": {"alertname": "NoService", "severity": "critical"},
                    "startsAt": "2026-09-01T11:58:00Z",
                }
            ]
        }
    )
    assert alerts[0].labels["service"] == "checkout"


@pytest.mark.req("REQ-031")
async def test_a_payload_with_no_alerts_is_refused(hooks):
    adapter = await hooks.adapter("grafana-alert-webhook")
    with pytest.raises(AdapterError):
        adapter.normalize({"alerts": []})
    with pytest.raises(AdapterError) as refused:
        adapter.normalize({"alerts": [{"status": "firing",
                                       "startsAt": "2026-09-01T11:58:00Z"}]})
    assert "alertname" in str(refused.value)


@pytest.mark.req("REQ-031")
async def test_a_push_alerts_adapter_does_not_pretend_to_poll(hooks):
    adapter = await hooks.adapter("grafana-alert-webhook")
    with pytest.raises(NotSupportedError):
        await adapter.fetch_active()


# --- deployments --------------------------------------------------------------


@pytest.mark.req("REQ-031")
async def test_a_deployment_started_envelope_becomes_a_change_event(hooks):
    adapter = await hooks.adapter("deploy-webhook")
    change = adapter.normalize(
        {
            "event": "deployment.started",
            "deployment_id": "dep-42",
            "service": "billing",
            "version": "2026.9.1",
            "actor": "release-bot",
            "commit": "abc..def",
            "timestamp": "2026-09-01T11:57:00Z",
        }
    )
    assert change.change_id == "dep-42"
    assert change.change_type is ChangeType.DEPLOYMENT
    assert change.status is ChangeStatus.STARTED
    assert change.completed_at is None
    assert change.diff_ref == "abc..def"


@pytest.mark.req("REQ-031")
async def test_a_completion_pairs_with_its_start_by_change_id(hooks):
    adapter = await hooks.adapter("deploy-webhook")
    completion = adapter.normalize(
        {
            "event": "deployment.completed",
            "deployment_id": "dep-42",
            "service": "billing",
            "started_at": "2026-09-01T11:57:00Z",
            "completed_at": "2026-09-01T11:59:00Z",
        }
    )
    assert completion.change_id == "dep-42"
    assert completion.status is ChangeStatus.COMPLETED
    assert completion.completed_at == datetime(2026, 9, 1, 11, 59, tzinfo=timezone.utc)


@pytest.mark.req("REQ-031")
async def test_status_aliases_and_change_types(hooks):
    adapter = await hooks.adapter("deploy-webhook")
    rolled_back = adapter.normalize(
        {
            "id": "dep-43",
            "app": "checkout",
            "state": "reverted",
            "type": "feature_flag",
            "timestamp": "2026-09-01T11:57:00Z",
        }
    )
    assert rolled_back.status is ChangeStatus.ROLLED_BACK
    assert rolled_back.change_type is ChangeType.FEATURE_FLAG


@pytest.mark.req("REQ-031")
async def test_a_deployment_with_neither_status_nor_event_is_refused(hooks):
    """Guessing 'completed' would close a change window that is still open,
    and a change window is how a responder finds the cause."""
    adapter = await hooks.adapter("deploy-webhook")
    with pytest.raises(AdapterError) as refused:
        adapter.normalize(
            {"id": "dep-44", "service": "billing", "timestamp": "2026-09-01T11:57:00Z"}
        )
    assert "refusing to guess" in str(refused.value)


@pytest.mark.req("REQ-031")
async def test_a_deployment_without_an_id_or_a_service_is_refused(hooks):
    adapter = await hooks.adapter("deploy-webhook")
    with pytest.raises(AdapterError) as no_service:
        adapter.normalize({"id": "d", "status": "started",
                           "timestamp": "2026-09-01T11:57:00Z"})
    assert "service" in str(no_service.value)

    with pytest.raises(AdapterError) as no_id:
        adapter.normalize({"service": "billing", "status": "started",
                           "timestamp": "2026-09-01T11:57:00Z"})
    assert "stable id" in str(no_id.value)


@pytest.mark.req("REQ-031")
async def test_a_naive_timestamp_is_read_as_utc_rather_than_dropped(hooks):
    """The envelope contract needs tz-aware times. Rejecting the change would
    lose it; every sender that omits an offset means UTC."""
    adapter = await hooks.adapter("deploy-webhook")
    change = adapter.normalize(
        {
            "id": "dep-45",
            "service": "billing",
            "status": "started",
            "started_at": "2026-09-01T11:57:00",
        }
    )
    assert change.started_at.tzinfo is not None


@pytest.mark.req("REQ-031")
async def test_a_push_changes_adapter_does_not_pretend_to_poll(hooks):
    adapter = await hooks.adapter("deploy-webhook")
    with pytest.raises(NotSupportedError):
        await adapter.list_changes(
            datetime(2026, 9, 1, tzinfo=timezone.utc),
            datetime(2026, 9, 2, tzinfo=timezone.utc),
        )


@pytest.mark.req("REQ-031")
async def test_a_push_binding_reports_ready_because_signalforge_is_the_server(hooks):
    adapter = await hooks.adapter("deploy-webhook")
    assert await adapter.ping() is True
