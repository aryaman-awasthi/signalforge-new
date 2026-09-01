"""Schema validation, the five streams and vendor normalisation (REQ-032, REQ-031).

The ingest API speaks the canonical contract: an application that implements
`contracts/canonical.py` is ingestible with no platform code written for it.
When a sender speaks its vendor's shape instead, the key's connector binding
names the push adapter that translates it - which is what the normalisers built
in S3-T02 exist for, and what makes the two paths one endpoint rather than two.
"""

from __future__ import annotations

import uuid

import pytest

from signalforge.contracts.envelope import utc_now
from signalforge.contracts.ingest import MAX_BATCH_ITEMS
from signalforge.tests.adapters.conftest import connect
from signalforge.tests.ingest.conftest import (
    Sender,
    alert,
    batch,
    change,
    events_of,
    issue_key,
    message,
    ticket,
    topology,
)


def sender_for(api, owner, stream: str, **kwargs) -> Sender:
    return Sender(api=api, key=issue_key(api, owner["token"], stream=stream, **kwargs))


# --- the frozen paths ---------------------------------------------------------


@pytest.mark.req("REQ-032", "REQ-004")
def test_the_app_serves_exactly_the_frozen_ingest_paths():
    """Five literal routes, not one template. `/api/v1/ingest/alerts` is a real
    endpoint in the OpenAPI document, and an unknown stream is a 404 from the
    router rather than a validation error from a handler."""
    from signalforge.api.main import app
    from signalforge.contracts.ingest import INGEST_PATHS
    from signalforge.domain.ingest.streams import IngestStream
    from signalforge.tests.security.test_authz_matrix import _walk_api_routes

    served = {
        route.path
        for route in _walk_api_routes(app.routes)
        if getattr(route, "path", "").startswith("/api/v1/ingest")
    }
    assert served == set(INGEST_PATHS.values())
    assert {stream.value for stream in IngestStream} == set(INGEST_PATHS)
    assert all(stream.path == INGEST_PATHS[stream.value] for stream in IngestStream)


@pytest.mark.req("REQ-032")
def test_an_unknown_stream_is_a_404_from_the_router(api, changes_sender):
    assert changes_sender.api.client.post(
        "/api/v1/ingest/invoices", json={}
    ).status_code == 404


# --- the batch envelope -------------------------------------------------------


@pytest.mark.req("REQ-032", "REQ-004")
def test_a_broken_envelope_is_422_and_records_nothing(api, owner, changes_sender):
    for body, fragment in [
        ({"items": [{}]}, "source_system"),
        ({"source_system": "ci"}, "items"),
        ({"source_system": "ci", "items": []}, "non-empty"),
        ({"source_system": " ", "items": [{}]}, "source_system"),
        ({"source_system": "ci", "items": [{}], "extra": 1}, "unknown"),
    ]:
        response = changes_sender.post(body)
        assert response.status_code == 422, body
        assert fragment in response.json()["detail"], body
    assert events_of(api, owner["token"], ("deployment.started",)) == []


@pytest.mark.req("REQ-032", "REQ-004")
def test_a_batch_over_the_contract_limit_is_refused(changes_sender):
    body = batch(*[change(f"deploy-{n}") for n in range(3)])
    body["items"] = body["items"] * (MAX_BATCH_ITEMS // 3 + 1)
    response = changes_sender.post(body)
    assert response.status_code == 422
    assert str(MAX_BATCH_ITEMS) in response.json()["detail"]


@pytest.mark.req("REQ-032")
def test_a_body_that_is_not_json_is_422(changes_sender):
    response = changes_sender.post(b"not json at all")
    assert response.status_code == 422
    assert "JSON" in response.json()["detail"]


@pytest.mark.req("REQ-032", "REQ-004")
def test_an_item_must_carry_a_tz_aware_occurred_at(changes_sender):
    body = batch(change())
    body["items"][0]["occurred_at"] = "2026-09-01T12:00:00"
    ack = changes_sender.post(body).json()
    assert ack["accepted"] == 0
    assert "timezone-aware" in ack["rejected"][0]["reason"]


@pytest.mark.req("REQ-032", "REQ-004")
def test_a_payload_of_the_wrong_kind_is_rejected(alerts_sender):
    """The alerts endpoint takes alerts. A change event posted to it is a
    misconfigured sender, and it is named as one rather than half-accepted."""
    ack = alerts_sender.post(batch(change())).json()
    assert ack["accepted"] == 0
    assert ack["rejected"][0]["index"] == 0


# --- the five streams ---------------------------------------------------------


@pytest.mark.req("REQ-032")
def test_changes_land_on_the_deployment_topics(api, owner, changes_sender):
    changes_sender.post(
        batch(
            change("d-1", status="started"),
            change("d-1", status="completed", completed_at=utc_now().isoformat()),
            change("c-1", change_type="config", status="completed"),
        )
    )
    topics = [event["topic"] for event in events_of(api, owner["token"])]
    assert "deployment.started" in topics
    assert "deployment.completed" in topics
    assert "change.completed" in topics


@pytest.mark.req("REQ-032")
def test_alerts_land_fired_or_resolved(api, owner, alerts_sender):
    alerts_sender.post(
        batch(
            alert("a-1", status="firing"),
            alert("a-1", status="resolved", ends_at=utc_now().isoformat()),
        )
    )
    topics = [event["topic"] for event in events_of(api, owner["token"])]
    assert "alert.fired" in topics and "alert.resolved" in topics


@pytest.mark.req("REQ-032")
def test_a_topology_with_a_dangling_edge_is_refused_whole(api, owner):
    """The pull importer's rule, applied to the push direction: a graph missing
    half its edges looks complete and understates blast radius."""
    sender = sender_for(api, owner, "topology")
    broken = topology()
    broken["edges"].append({"source": "checkout", "target": "ghost"})
    ack = sender.post(batch(broken)).json()
    assert ack["accepted"] == 0
    assert "not declared" in ack["rejected"][0]["reason"]
    assert events_of(api, owner["token"], ("topology.updated",)) == []

    assert sender.post(batch(topology())).json()["accepted"] == 1
    assert len(events_of(api, owner["token"], ("topology.updated",))) == 1


@pytest.mark.req("REQ-032", "REQ-033")
def test_messages_and_tickets_are_ingestible_without_a_push_adapter(api, owner):
    """The frozen SPI defines `normalize` only on alerts, changes and topology,
    so collaboration and itsm can never have a push-mode adapter. Both streams
    still work, because the ingest contract is canonical: the key is the
    credential and the adapter is only ever an optional translator."""
    messages = sender_for(api, owner, "messages")
    tickets = sender_for(api, owner, "tickets")
    assert messages.post(batch(message())).json()["accepted"] == 1
    assert tickets.post(batch(ticket())).json()["accepted"] == 1

    topics = [event["topic"] for event in events_of(api, owner["token"])]
    assert "collaboration.message.received" in topics
    assert "itsm.ticket.updated" in topics


@pytest.mark.req("REQ-032", "REQ-014")
def test_free_text_is_redacted_at_the_ingest_boundary(api, owner):
    """Collaboration and itsm carry text a human typed. There is no manifest to
    read a policy from on these streams, so the floor lives in the domain and is
    applied before the text can reach an event payload."""
    messages = sender_for(api, owner, "messages")
    messages.post(
        batch(message(text="page ada@example.com, card 4111 1111 1111 1111"))
    )
    event = events_of(api, owner["token"], ("collaboration.message.received",))[0]
    assert "ada@example.com" not in event["payload"]["text"]
    assert "4111" not in event["payload"]["text"]
    assert event["redaction"] == {
        "applied": True,
        "fields": ["text"],
        "policy_id": "ingest-default",
    }


@pytest.mark.req("REQ-032", "REQ-014")
def test_redaction_is_not_claimed_when_nothing_was_removed(api, owner):
    messages = sender_for(api, owner, "messages")
    messages.post(batch(message(text="checkout is throwing 500s")))
    event = events_of(api, owner["token"], ("collaboration.message.received",))[0]
    assert event["redaction"]["applied"] is False
    assert event["redaction"]["fields"] == []


@pytest.mark.req("REQ-032")
def test_an_accepted_event_carries_its_provenance(api, owner, changes_sender):
    changes_sender.post(batch(change("d-9"), source_system="jenkins", simulated=True))
    event = events_of(api, owner["token"], ("deployment.started",))[0]
    assert event["source"]["system"] == "jenkins"
    assert event["source"]["kind"] == "changes"
    assert event["source"]["authority"] == "observed"
    assert event["source"]["simulated"] is True
    assert event["source"]["freshness_s"] >= 0
    assert event["actor"] == {"type": "external", "id": "jenkins", "display": changes_sender.key["name"]}
    assert event["app_id"] == "checkout"
    assert event["plane"] == "signal"


# --- vendor payloads through a push adapter ----------------------------------


@pytest.mark.req("REQ-032", "REQ-031")
def test_a_grafana_shaped_payload_is_normalised_by_the_bound_adapter(api, owner):
    binding = connect(
        api, owner["token"], "grafana-alert-webhook", config={}, credentials={"ingest_secret": "s"}
    )
    sender = sender_for(api, owner, "alerts", binding_id=binding["id"])

    vendor = {
        "status": "firing",
        "labels": {"alertname": "CheckoutErrorRate", "severity": "unheard-of"},
        "annotations": {"summary": "errors up"},
        "startsAt": utc_now().isoformat(),
        "endsAt": "0001-01-01T00:00:00Z",
        "fingerprint": "abc123",
    }
    ack = sender.post(batch(vendor)).json()
    assert ack["accepted"] == 1, ack

    event = events_of(api, owner["token"], ("alert.fired",))[0]
    # The normaliser's own rules still hold on this path: an unrecognised
    # severity becomes warning, and the unresolved sentinel becomes null.
    assert event["payload"]["severity"] == "warning"
    assert event["payload"]["ends_at"] is None
    assert event["payload"]["alert_id"] == "abc123"


@pytest.mark.req("REQ-032", "REQ-031")
def test_canonical_and_vendor_payloads_share_one_endpoint(api, owner):
    binding = connect(
        api, owner["token"], "grafana-alert-webhook", config={}, credentials={"ingest_secret": "s"}
    )
    sender = sender_for(api, owner, "alerts", binding_id=binding["id"])
    ack = sender.post(
        batch(
            alert("canonical-1"),
            {
                "status": "firing",
                "labels": {"alertname": "VendorShaped", "severity": "critical"},
                "startsAt": utc_now().isoformat(),
                "fingerprint": "vendor-1",
            },
        )
    ).json()
    assert (ack["accepted"], ack["rejected"]) == (2, [])
    ids = {e["payload"]["alert_id"] for e in events_of(api, owner["token"], ("alert.fired",))}
    assert ids == {"canonical-1", "vendor-1"}


@pytest.mark.req("REQ-032", "REQ-031")
def test_one_vendor_payload_may_become_several_alerts(api, owner):
    binding = connect(
        api, owner["token"], "grafana-alert-webhook", config={}, credentials={"ingest_secret": "s"}
    )
    sender = sender_for(api, owner, "alerts", binding_id=binding["id"])
    event_id = str(uuid.uuid4())
    grafana_batch = {
        "alerts": [
            {
                "status": "firing",
                "labels": {"alertname": f"Rule{n}", "severity": "critical"},
                "startsAt": utc_now().isoformat(),
                "fingerprint": f"fp-{n}",
            }
            for n in range(3)
        ]
    }
    ack = sender.post(batch({**grafana_batch, "_event_id": event_id})).json()
    assert ack["accepted"] == 1, "one posted item is one idempotency unit"
    assert len(events_of(api, owner["token"], ("alert.fired",))) == 3

    # And the retry of that one item is still one duplicate, not three.
    repeat = sender.post(batch({**grafana_batch, "_event_id": event_id})).json()
    assert (repeat["accepted"], repeat["duplicates"]) == (0, 1)
    assert len(events_of(api, owner["token"], ("alert.fired",))) == 3


@pytest.mark.req("REQ-032", "REQ-031")
def test_a_payload_the_adapter_refuses_names_both_reasons(api, owner):
    binding = connect(
        api, owner["token"], "grafana-alert-webhook", config={}, credentials={"ingest_secret": "s"}
    )
    sender = sender_for(api, owner, "alerts", binding_id=binding["id"])
    ack = sender.post(batch({"status": "firing", "labels": {}})).json()
    assert ack["accepted"] == 0
    reason = ack["rejected"][0]["reason"]
    assert "normaliser:" in reason
    assert "alertname" in reason


@pytest.mark.req("REQ-032", "REQ-030")
def test_disabling_the_binding_mid_flow_keeps_canonical_ingest_working(api, owner):
    """Permission change mid-flow. The translator disappears; the contract does
    not, so a canonical sender is unaffected and a vendor one is told why."""
    binding = connect(
        api, owner["token"], "grafana-alert-webhook", config={}, credentials={"ingest_secret": "s"}
    )
    sender = sender_for(api, owner, "alerts", binding_id=binding["id"])
    vendor = {
        "status": "firing",
        "labels": {"alertname": "Rule", "severity": "critical"},
        "startsAt": utc_now().isoformat(),
        "fingerprint": "fp-1",
    }
    assert sender.post(batch(vendor)).json()["accepted"] == 1

    disabled = api.client.patch(
        f"/connectors/{binding['id']}",
        json={"enabled": False},
        headers=api.auth_header(owner["token"]),
    )
    assert disabled.status_code == 200, disabled.text

    ack = sender.post(batch(alert("still-canonical"), {**vendor, "fingerprint": "fp-2"})).json()
    assert ack["accepted"] == 1
    assert "disabled" in ack["rejected"][0]["reason"]


@pytest.mark.req("REQ-032", "REQ-030")
def test_a_binding_that_cannot_normalise_the_stream_is_refused_at_issue(api, owner):
    """Found when the operator wires it, not at 3am when the first alert arrives."""
    metrics_binding = connect(api, owner["token"], "http-observe-metrics")
    response = api.client.post(
        "/ingest-keys",
        json={
            "app_id": "checkout",
            "stream": "alerts",
            "name": "wrong kind",
            "binding_id": metrics_binding["id"],
        },
        headers=api.auth_header(owner["token"]),
    )
    assert response.status_code == 422
    assert "metrics adapter" in response.json()["detail"]

    topo = connect(api, owner["token"], "topo-import")
    pull_mode = api.client.post(
        "/ingest-keys",
        json={
            "app_id": "checkout",
            "stream": "topology",
            "name": "wrong mode",
            "binding_id": topo["id"],
        },
        headers=api.auth_header(owner["token"]),
    )
    assert pull_mode.status_code == 422
    assert "push adapter" in pull_mode.json()["detail"]
