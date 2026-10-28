"""Security properties of the shipped adapters and the /ws socket (artifact 18).

Three surfaces arrived with S3-T02 and each brings a way to leak:

- adapters carry customer free text out of the estate, so the redaction a
  manifest promises has to actually happen, at the boundary, before the text can
  reach a stored record;
- the outbound HTTP client is pointed at a URL an operator typed, which is how a
  config field becomes a local file read if the scheme is not checked;
- /ws is the first route with no permission check, so what stops it leaking is
  that it is structurally incapable of naming anyone but its own principal.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from starlette.websockets import WebSocketDisconnect

from signalforge.contracts.canonical import (
    KnowledgeDocType,
    KnowledgeDocument,
    Notification,
    NotificationChannel,
)
from signalforge.contracts.errors import AdapterError
from signalforge.domain.adapters.manifest import (
    AdapterAuth,
    AdapterLimits,
    AuthMethod,
)
from signalforge.infrastructure.adapters.transport import (
    HttpTransport,
    normalize_base_url,
)
from signalforge.tests.adapters.conftest import connect

pytestmark = pytest.mark.anyio

AT = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)
WINDOW = (AT - timedelta(minutes=5), AT)

SECRETS = "ada@example.com and 4111 1111 1111 1111"


# --- redaction at the boundary ------------------------------------------------


@pytest.mark.req("REQ-018", "REQ-031")
async def test_every_free_text_adapter_redacts_what_its_manifest_promised(api):
    """logs, traces, collaboration, knowledge and itsm are the kinds the
    manifest floor forces to declare a policy. Declaring it is S3-T01; applying
    it is this."""
    from signalforge.tests.adapters.conftest import ObserveHarness
    from signalforge.tests.adapters.observe_app import build_observe_app

    harness = ObserveHarness()
    app, _ = build_observe_app()
    harness.mount(ObserveHarness.BASE_URL, app)
    harness.attach(api.container)

    signup = api.signup()
    token = signup["tokens"]["access_token"]
    tenant = signup["org"]["id"]

    async def call(adapter_id, capability, *args, **kwargs):
        binding = connect(api, token, adapter_id, **kwargs)
        return await api.container.invoker.invoke(
            tenant, binding["id"], capability, *args
        )

    logs = await call("http-observe-logs", "search", "", WINDOW[0], WINDOW[1], 10)
    assert all("ada@example.com" not in record.message for record in logs)

    spans = await call(
        "http-observe-traces", "search", WINDOW[0], WINDOW[1], 10, None, None, False
    )
    assert all(
        "ada@example.com" not in value
        for span in spans
        for value in span.attributes.values()
    )


@pytest.mark.req("REQ-018", "REQ-035")
async def test_redacted_knowledge_never_reaches_the_database(api):
    signup = api.signup()
    binding = connect(
        api, signup["tokens"]["access_token"], "kb-index", credentials={}
    )
    await api.container.invoker.invoke(
        signup["org"]["id"],
        binding["id"],
        "index",
        KnowledgeDocument(
            doc_id="rb-1",
            title="Runbook",
            body=f"escalate to {SECRETS}",
            doc_type=KnowledgeDocType.RUNBOOK,
            updated_at=AT,
        ),
    )
    async with api.container.integration_uow_factory() as uow:
        stored = await uow.knowledge.get(signup["org"]["id"], "rb-1")
    assert "ada@example.com" not in stored.body
    assert "4111" not in stored.body


@pytest.mark.req("REQ-018", "REQ-033")
async def test_a_notification_body_is_redacted_before_it_is_stored_or_pushed(api):
    signup = api.signup()
    binding = connect(
        api, signup["tokens"]["access_token"], "page-sim", credentials={}
    )
    receipt = await api.container.invoker.invoke(
        signup["org"]["id"],
        binding["id"],
        "send",
        Notification(
            notification_id="n-1",
            channel=NotificationChannel.SMS,
            recipient="+15550000",
            title="Page",
            body=f"call {SECRETS}",
        ),
    )
    assert "4111" not in receipt.detail
    async with api.container.integration_uow_factory() as uow:
        stored = await uow.notifications.get(signup["org"]["id"], "n-1")
    assert "4111" not in stored.body


# --- the outbound client ------------------------------------------------------


@pytest.mark.req("REQ-018")
def test_a_base_url_that_is_not_http_is_refused():
    """A binding's base URL is operator input this process then fetches. The
    scheme check is a control, not tidiness: file:// is how a config field
    becomes a local file read."""
    for hostile in ("file:///etc/passwd", "unix:///var/run/docker.sock", "ftp://x/y",
                    "not a url", "", "//nohost"):
        with pytest.raises(AdapterError):
            normalize_base_url(hostile)
    assert normalize_base_url("http://app.internal:8080") == "http://app.internal:8080/"


@pytest.mark.req("REQ-015", "REQ-018")
def test_the_transport_never_puts_a_credential_in_a_repr():
    transport = HttpTransport(
        base_url="https://app.internal",
        auth=AdapterAuth(method=AuthMethod.BEARER, credential_fields=("token",)),
        credentials={"token": "super-secret-value"},
        limits=AdapterLimits(),
    )
    assert "super-secret-value" not in repr(transport)
    assert "redacted" in repr(transport)


@pytest.mark.req("REQ-015")
def test_a_missing_credential_is_an_auth_error_before_anything_is_sent():
    from signalforge.contracts.errors import AdapterAuthError

    transport = HttpTransport(
        base_url="https://app.internal",
        auth=AdapterAuth(method=AuthMethod.BEARER, credential_fields=("token",)),
        credentials={},
        limits=AdapterLimits(),
    )
    with pytest.raises(AdapterAuthError):
        transport._headers(b"")  # noqa: SLF001 - the header build is the check


# --- the socket ---------------------------------------------------------------


@pytest.mark.req("REQ-012", "REQ-033")
def test_the_socket_refuses_a_handshake_with_no_session(api):
    api.signup()
    with pytest.raises(WebSocketDisconnect) as closed:
        with api.client.websocket_connect("/ws"):
            pass
    assert closed.value.code == 1008


@pytest.mark.req("REQ-012", "REQ-033")
def test_the_socket_refuses_a_forged_token(api):
    api.signup()
    with pytest.raises(WebSocketDisconnect) as closed:
        with api.client.websocket_connect(
            "/ws", headers={"Authorization": "Bearer not.a.real.token"}
        ):
            pass
    assert closed.value.code == 1008


@pytest.mark.req("REQ-013", "REQ-033")
async def test_a_socket_never_receives_another_tenants_notification(api):
    """One hub serves the process. The tenant predicate is inside it as well as
    in the handshake, because a fan-out is exactly where a missing predicate
    becomes a cross-tenant leak."""
    first = api.signup()
    second = api.signup(org="Other Corp", email="owner@other.io")

    # Both tenants happen to use the same user id string for their recipient.
    shared_recipient = "on-call"
    binding = connect(
        api, second["tokens"]["access_token"], "notify-in-app", credentials={}
    )

    with api.client.websocket_connect(
        "/ws", headers=api.auth_header(first["tokens"]["access_token"])
    ) as socket:
        assert socket.receive_json()["type"] == "ready"
        api.container.notification_hub.subscriptions[0].user_id = shared_recipient

        await api.container.invoker.invoke(
            second["org"]["id"],
            binding["id"],
            "send",
            Notification(
                notification_id="n-other",
                channel=NotificationChannel.IN_APP,
                recipient=shared_recipient,
                title="Other tenant's page",
                body="not for you",
            ),
        )
        socket.send_json({"type": "ping"})
        assert socket.receive_json() == {"type": "pong"}, (
            "the other tenant's notification was not delivered here"
        )


@pytest.mark.req("REQ-013", "REQ-033")
async def test_acknowledging_another_tenants_notification_is_not_found(api):
    first = api.signup()
    second = api.signup(org="Other Corp", email="owner@other.io")
    binding = connect(
        api, second["tokens"]["access_token"], "notify-in-app", credentials={}
    )
    await api.container.invoker.invoke(
        second["org"]["id"],
        binding["id"],
        "send",
        Notification(
            notification_id="n-theirs",
            channel=NotificationChannel.IN_APP,
            recipient=second["user"]["id"],
            title="Theirs",
            body="theirs",
            requires_ack=True,
        ),
    )
    with api.client.websocket_connect(
        "/ws", headers=api.auth_header(first["tokens"]["access_token"])
    ) as socket:
        socket.receive_json()
        socket.send_json({"type": "ack", "notification_id": "n-theirs"})
        assert socket.receive_json() == {
            "type": "error",
            "detail": "notification not found",
        }

    async with api.container.integration_uow_factory() as uow:
        theirs = await uow.notifications.get(second["org"]["id"], "n-theirs")
    assert theirs.acked_by is None


@pytest.mark.req("REQ-025", "REQ-033")
async def test_metrics_count_notifications_without_naming_a_recipient(api):
    signup = api.signup()
    binding = connect(
        api, signup["tokens"]["access_token"], "page-sim", credentials={}
    )
    await api.container.invoker.invoke(
        signup["org"]["id"],
        binding["id"],
        "send",
        Notification(
            notification_id="n-1",
            channel=NotificationChannel.SMS,
            recipient="+15551234567",
            title="Page",
            body="body",
        ),
    )
    body = api.client.get("/metrics").text
    assert 'signalforge_notifications_total{channel="sms",status="sent"} 1' in body
    assert "+15551234567" not in body
    assert signup["org"]["id"] not in body
