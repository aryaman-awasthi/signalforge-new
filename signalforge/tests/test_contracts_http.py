"""S0-T02: /observe/* and /api/v1/ingest/* shape contracts."""

import uuid

import pytest
from pydantic import ValidationError

from signalforge.contracts import ingest, observe
from signalforge.contracts.canonical import ChangeEvent, ChangeStatus, ChangeType
from signalforge.contracts.envelope import utc_now


def change_payload():
    return ChangeEvent(
        change_id="deploy-42",
        change_type=ChangeType.DEPLOYMENT,
        service="checkout",
        status=ChangeStatus.STARTED,
        started_at=utc_now(),
        version="1.4.2",
    )


def change_item():
    return ingest.IngestItem[ChangeEvent](
        event_id=uuid.uuid4(), occurred_at=utc_now(), payload=change_payload()
    )


@pytest.mark.req("REQ-004")
def test_observe_paths_frozen():
    assert observe.OBSERVE_PATHS["metrics"] == "/observe/metrics"
    assert observe.OBSERVE_PATHS["actions_manifest"] == "/observe/actions"
    assert (
        observe.OBSERVE_PATHS["action_execution"]
        == "/observe/actions/executions/{idempotency_key}"
    )
    assert all(p.startswith("/observe/") for p in observe.OBSERVE_PATHS.values())
    assert observe.OBSERVE_METRICS_CONTENT_TYPE.startswith("text/plain")


@pytest.mark.req("REQ-004")
def test_ingest_paths_and_hmac_headers_frozen():
    assert set(ingest.INGEST_PATHS) == {"changes", "alerts", "topology", "messages", "tickets"}
    assert all(p.startswith("/api/v1/ingest/") for p in ingest.INGEST_PATHS.values())
    assert ingest.HDR_SIGNATURE == "X-SignalForge-Signature"
    assert ingest.HDR_TIMESTAMP == "X-SignalForge-Timestamp"
    assert ingest.REPLAY_WINDOW_S == 300


@pytest.mark.req("REQ-004")
def test_ingest_batch_valid_and_bounded():
    req = ingest.IngestChangesRequest(source_system="ci", items=[change_item()])
    assert req.items[0].payload.service == "checkout"

    with pytest.raises(ValidationError):
        ingest.IngestChangesRequest(source_system="ci", items=[])

    too_many = [change_item() for _ in range(ingest.MAX_BATCH_ITEMS + 1)]
    with pytest.raises(ValidationError):
        ingest.IngestChangesRequest(source_system="ci", items=too_many)


@pytest.mark.req("REQ-004")
def test_ingest_item_requires_tz_aware_occurred_at():
    from datetime import datetime

    with pytest.raises(ValidationError):
        ingest.IngestItem[ChangeEvent](
            event_id=uuid.uuid4(),
            occurred_at=datetime(2026, 1, 1, 12, 0),
            payload=change_payload(),
        )


@pytest.mark.req("REQ-004")
def test_ingest_payload_kind_is_enforced():
    with pytest.raises(ValidationError):
        ingest.IngestAlertsRequest(source_system="ci", items=[change_item()])


@pytest.mark.req("REQ-004")
def test_ingest_ack_shape():
    ack = ingest.IngestAck(
        accepted=2,
        duplicates=1,
        rejected=[ingest.IngestRejection(index=3, reason="schema: missing service")],
    )
    assert ack.rejected[0].index == 3
    with pytest.raises(ValidationError):
        ingest.IngestAck(accepted=-1, duplicates=0)


@pytest.mark.req("REQ-004")
def test_action_execute_request_requires_idempotency_key():
    req = observe.ActionExecuteRequest(idempotency_key="k1")
    assert req.params == {}
    with pytest.raises(ValidationError):
        observe.ActionExecuteRequest(idempotency_key="")


@pytest.mark.req("REQ-004")
def test_chaos_inject_request_requires_scope():
    with pytest.raises(ValidationError):
        observe.ChaosInjectRequest(idempotency_key="k1", scope="")
    ok = observe.ChaosInjectRequest(idempotency_key="k1", scope="drill:d-7")
    assert ok.scope.startswith("drill:")
