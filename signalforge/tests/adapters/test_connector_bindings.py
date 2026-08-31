"""Per-app connector bindings end to end (REQ-030, REQ-015, REQ-013).

Written failing-first for the two rules that are policy rather than plumbing:
one binding per (app, adapter, credential scope), and a binding plus its
encrypted credentials plus its audit entry are one transaction or none.
"""

from __future__ import annotations

import sqlite3

import pytest
import yaml

from signalforge.tests.adapters.conftest import (
    INVOKE_ACTIONS,
    PULL_METRICS,
    write_manifest,
)

pytestmark = pytest.mark.anyio

BASE_URL = "http://app.internal:8080"


def connector_body(**overrides) -> dict:
    body = {
        "app_id": "checkout",
        "adapter_id": "probe-metrics",
        "name": "checkout metrics",
        "credential_scope": "read_only",
        "config": {"base_url": BASE_URL},
        "credentials": {"token": "super-secret-token-value"},
    }
    body.update(overrides)
    return body


def secrets_rows(db_path) -> list[tuple]:
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute("select tenant_id, name, ciphertext from secrets").fetchall()
    finally:
        conn.close()


def binding_rows(db_path) -> list[tuple]:
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute(
            "select id, tenant_id, app_id, adapter_id, credential_scope, config "
            "from connector_bindings"
        ).fetchall()
    finally:
        conn.close()


@pytest.mark.req("REQ-030")
async def test_create_list_and_read_a_binding(adapter_api):
    owner = adapter_api.signup()
    token = owner["tokens"]["access_token"]

    created = adapter_api.client.post(
        "/connectors", json=connector_body(), headers=adapter_api.auth_header(token)
    )
    assert created.status_code == 201, created.text
    body = created.json()

    assert body["adapter_id"] == "probe-metrics"
    assert body["adapter_name"] == "Probe metrics"
    assert body["kind"] == "metrics"
    assert body["mode"] == "pull"
    assert body["direction"] == "inbound (SignalForge polls)"
    assert body["credential_scope"] == "read_only"
    assert body["config"] == {"base_url": BASE_URL}
    assert body["credential_fields"] == ["token"]
    assert body["status"] == "unknown", "nothing has polled it yet"
    assert body["installed"] is True
    assert body["simulated"] is False

    listed = adapter_api.client.get(
        "/connectors", headers=adapter_api.auth_header(token)
    ).json()
    assert [c["id"] for c in listed] == [body["id"]]

    one = adapter_api.client.get(
        f"/connectors/{body['id']}", headers=adapter_api.auth_header(token)
    )
    assert one.status_code == 200
    assert one.json()["id"] == body["id"]


@pytest.mark.req("REQ-030", "REQ-015")
async def test_credentials_are_encrypted_at_rest_and_never_returned(adapter_api):
    owner = adapter_api.signup()
    token = owner["tokens"]["access_token"]
    secret = "super-secret-token-value"

    created = adapter_api.client.post(
        "/connectors", json=connector_body(), headers=adapter_api.auth_header(token)
    ).json()

    rows = secrets_rows(adapter_api.db_path)
    assert len(rows) == 1
    tenant_id, name, ciphertext = rows[0]
    assert tenant_id == owner["org"]["id"]
    assert name == f"connector/{created['id']}/token"
    assert secret.encode() not in ciphertext, "the credential is stored Fernet-encrypted"

    # Nothing on the way out carries the value: not the create response, not the
    # list, not the binding row, not the audit chain.
    for text in (
        created,
        adapter_api.client.get("/connectors", headers=adapter_api.auth_header(token)).json(),
        binding_rows(adapter_api.db_path),
        adapter_api.audit_rows(owner["org"]["id"]),
    ):
        assert secret not in str(text)

    # But the platform can still read it back to build an adapter.
    view = await adapter_api.container.connectors.get(
        await adapter_api.container.auth.authenticate(token), created["id"]
    )
    assert await adapter_api.container.connectors.credentials_for(view.binding) == {
        "token": secret
    }


@pytest.mark.req("REQ-030")
async def test_config_is_validated_against_the_manifest_schema(adapter_api):
    owner = adapter_api.signup()
    token = owner["tokens"]["access_token"]

    missing = adapter_api.client.post(
        "/connectors",
        json=connector_body(config={}),
        headers=adapter_api.auth_header(token),
    )
    assert missing.status_code == 422
    assert "base_url" in missing.json()["detail"]

    extra = adapter_api.client.post(
        "/connectors",
        json=connector_body(config={"base_url": BASE_URL, "nope": 1}),
        headers=adapter_api.auth_header(token),
    )
    assert extra.status_code == 422
    assert binding_rows(adapter_api.db_path) == []


@pytest.mark.req("REQ-030")
async def test_credentials_must_match_the_manifests_declared_fields(adapter_api):
    owner = adapter_api.signup()
    token = owner["tokens"]["access_token"]

    for credentials in ({}, {"token": "  "}, {"token": "x", "extra": "y"}):
        response = adapter_api.client.post(
            "/connectors",
            json=connector_body(credentials=credentials),
            headers=adapter_api.auth_header(token),
        )
        assert response.status_code == 422, credentials
    assert binding_rows(adapter_api.db_path) == []


@pytest.mark.req("REQ-030")
async def test_a_second_binding_at_the_same_scope_is_a_duplicate(adapter_api):
    """Written first: (app, adapter, scope) is the uniqueness key, and a
    duplicate is the shape a wizard resubmit takes."""
    owner = adapter_api.signup()
    token = owner["tokens"]["access_token"]

    first = adapter_api.client.post(
        "/connectors", json=connector_body(), headers=adapter_api.auth_header(token)
    )
    assert first.status_code == 201

    again = adapter_api.client.post(
        "/connectors",
        json=connector_body(name="checkout metrics again"),
        headers=adapter_api.auth_header(token),
    )
    assert again.status_code == 409
    assert first.json()["id"] in again.json()["detail"]

    # A different app is a different binding, and so is a different scope.
    other_app = adapter_api.client.post(
        "/connectors",
        json=connector_body(app_id="payments"),
        headers=adapter_api.auth_header(token),
    )
    assert other_app.status_code == 201
    assert len(binding_rows(adapter_api.db_path)) == 2


@pytest.mark.req("REQ-030", "REQ-015")
async def test_read_only_and_remediation_are_separate_bindings(adapter_api):
    owner = adapter_api.signup()
    token = owner["tokens"]["access_token"]

    read = adapter_api.client.post(
        "/connectors", json=connector_body(), headers=adapter_api.auth_header(token)
    )
    remediate = adapter_api.client.post(
        "/connectors",
        json=connector_body(
            adapter_id="probe-actions",
            name="checkout actions",
            credential_scope="remediation",
            config={"base_url": BASE_URL},
            credentials={"api_key": "remediation-key"},
        ),
        headers=adapter_api.auth_header(token),
    )
    assert read.status_code == 201 and remediate.status_code == 201

    names = {name for _t, name, _c in secrets_rows(adapter_api.db_path)}
    assert names == {
        f"connector/{read.json()['id']}/token",
        f"connector/{remediate.json()['id']}/api_key",
    }, "two credentials, two secrets, no sharing"


@pytest.mark.req("REQ-030")
async def test_a_scope_the_manifest_does_not_offer_is_refused(adapter_api):
    owner = adapter_api.signup()
    token = owner["tokens"]["access_token"]

    response = adapter_api.client.post(
        "/connectors",
        json=connector_body(credential_scope="remediation"),
        headers=adapter_api.auth_header(token),
    )
    assert response.status_code == 422
    assert "does not offer" in response.json()["detail"]
    assert binding_rows(adapter_api.db_path) == []


@pytest.mark.req("REQ-030")
async def test_binding_an_unknown_adapter_is_a_404(adapter_api):
    owner = adapter_api.signup()
    token = owner["tokens"]["access_token"]
    response = adapter_api.client.post(
        "/connectors",
        json=connector_body(adapter_id="no-such-adapter"),
        headers=adapter_api.auth_header(token),
    )
    assert response.status_code == 404


@pytest.mark.req("REQ-030", "REQ-015")
async def test_a_failed_credential_write_leaves_no_binding_behind(
    adapter_api, adapters_root, monkeypatch
):
    """Partial completion (CLAUDE.md rule 6). The binding, its two credentials,
    its audit entry and its event are one transaction: if the second credential
    fails to store, none of the rest may survive - a binding whose credential
    write failed is a connector that silently cannot authenticate."""
    from signalforge.infrastructure.security.secrets import FernetSecretStore

    write_manifest(
        adapters_root,
        dict(
            INVOKE_ACTIONS,
            id="probe-two-secrets",
            credential_scopes=["remediation"],
            auth={"method": "basic", "credential_fields": ["username", "password"]},
        ),
    )
    adapter_api.container.adapter_registry.reload()

    owner = adapter_api.signup()
    token = owner["tokens"]["access_token"]

    original = FernetSecretStore.put

    async def failing_put(self, tenant_id, name, value):
        if name.endswith("/password"):
            raise RuntimeError("key management unavailable")
        return await original(self, tenant_id, name, value)

    monkeypatch.setattr(FernetSecretStore, "put", failing_put)

    with pytest.raises(RuntimeError):
        adapter_api.client.post(
            "/connectors",
            json=connector_body(
                adapter_id="probe-two-secrets",
                name="two secrets",
                credential_scope="remediation",
                config={"base_url": BASE_URL},
                credentials={"username": "svc", "password": "pw"},
            ),
            headers=adapter_api.auth_header(token),
        )

    assert binding_rows(adapter_api.db_path) == []
    assert secrets_rows(adapter_api.db_path) == []
    assert "system.connector.bound" not in adapter_api.audit_topics(owner["org"]["id"])


@pytest.mark.req("REQ-030")
async def test_update_changes_config_rotates_credentials_and_audits_what_changed(
    adapter_api,
):
    owner = adapter_api.signup()
    token = owner["tokens"]["access_token"]
    created = adapter_api.client.post(
        "/connectors", json=connector_body(), headers=adapter_api.auth_header(token)
    ).json()

    updated = adapter_api.client.patch(
        f"/connectors/{created['id']}",
        json={
            "config": {"base_url": "http://moved.internal", "region_label": "eu-west"},
            "credentials": {"token": "rotated-token"},
        },
        headers=adapter_api.auth_header(token),
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["config"]["base_url"] == "http://moved.internal"

    principal = await adapter_api.container.auth.authenticate(token)
    view = await adapter_api.container.connectors.get(principal, created["id"])
    assert await adapter_api.container.connectors.credentials_for(view.binding) == {
        "token": "rotated-token"
    }

    entries = [
        row
        for row in adapter_api.audit_rows(owner["org"]["id"])
        if row["topic"] == "system.connector.updated"
    ]
    assert len(entries) == 1
    assert entries[0]["payload"]["changed"] == ["config", "credentials"]
    assert "rotated-token" not in str(entries[0])


@pytest.mark.req("REQ-030")
async def test_an_operator_can_disable_a_connector_without_deleting_it(adapter_api):
    """Human override: a flapping integration is turned off, keeping its config
    and its history, and it leaves the health loop at once."""
    owner = adapter_api.signup()
    token = owner["tokens"]["access_token"]
    created = adapter_api.client.post(
        "/connectors", json=connector_body(), headers=adapter_api.auth_header(token)
    ).json()

    disabled = adapter_api.client.patch(
        f"/connectors/{created['id']}",
        json={"enabled": False},
        headers=adapter_api.auth_header(token),
    )
    assert disabled.status_code == 200
    assert disabled.json()["enabled"] is False

    sources = await adapter_api.container.health.providers[0]()
    assert sources == [], "a disabled binding is not polled"

    still_listed = adapter_api.client.get(
        "/connectors", headers=adapter_api.auth_header(token)
    ).json()
    assert still_listed[0]["config"] == {"base_url": BASE_URL}


@pytest.mark.req("REQ-030", "REQ-015")
async def test_delete_removes_the_binding_and_its_credentials(adapter_api):
    owner = adapter_api.signup()
    token = owner["tokens"]["access_token"]
    created = adapter_api.client.post(
        "/connectors", json=connector_body(), headers=adapter_api.auth_header(token)
    ).json()
    assert secrets_rows(adapter_api.db_path)

    deleted = adapter_api.client.delete(
        f"/connectors/{created['id']}", headers=adapter_api.auth_header(token)
    )
    assert deleted.status_code == 204
    assert binding_rows(adapter_api.db_path) == []
    assert secrets_rows(adapter_api.db_path) == [], (
        "the credential goes with the binding, not with a later sweep"
    )
    assert "system.connector.unbound" in adapter_api.audit_topics(owner["org"]["id"])


@pytest.mark.req("REQ-030")
async def test_binding_events_reach_the_bus_on_the_signal_plane(adapter_api):
    owner = adapter_api.signup()
    token = owner["tokens"]["access_token"]
    adapter_api.client.post(
        "/connectors", json=connector_body(), headers=adapter_api.auth_header(token)
    )

    async with adapter_api.container.event_uow_factory() as uow:
        events = list(await uow.events.read(tenant_id=owner["org"]["id"], limit=50))
    bound = [e for e in events if e.topic == "system.connector.bound"]
    assert len(bound) == 1
    assert bound[0].app_id == "checkout"
    assert bound[0].plane.value == "signal"
    assert bound[0].payload["adapter_id"] == "probe-metrics"
    assert "credentials" not in bound[0].payload
    assert bound[0].payload["credential_fields"] == ["token"]


@pytest.mark.req("REQ-030")
async def test_a_binding_whose_adapter_folder_disappeared_is_shown_as_orphaned(
    adapter_api, adapters_root
):
    """Uncertain external state: the adapter was uninstalled under a live
    binding. The binding stays visible and is flagged, because deleting a
    customer's configuration to tidy up a list is worse than showing red."""
    owner = adapter_api.signup()
    token = owner["tokens"]["access_token"]
    adapter_api.client.post(
        "/connectors", json=connector_body(), headers=adapter_api.auth_header(token)
    )

    (adapters_root / "probe-metrics" / "manifest.yaml").unlink()
    adapter_api.container.adapter_registry.reload()

    listed = adapter_api.client.get(
        "/connectors", headers=adapter_api.auth_header(token)
    ).json()
    assert listed[0]["installed"] is False
    assert listed[0]["kind"] is None
    assert listed[0]["config"] == {"base_url": BASE_URL}


@pytest.mark.req("REQ-030")
async def test_declared_redaction_fields_are_masked_on_the_way_out(
    adapter_api, adapters_root
):
    redacting = dict(
        PULL_METRICS,
        id="probe-redacting",
        redaction={"policy_id": "metrics-region", "fields": ["region_label"]},
    )
    write_manifest(adapters_root, redacting)
    adapter_api.container.adapter_registry.reload()

    owner = adapter_api.signup()
    token = owner["tokens"]["access_token"]
    created = adapter_api.client.post(
        "/connectors",
        json=connector_body(
            adapter_id="probe-redacting",
            config={"base_url": BASE_URL, "region_label": "eu-west"},
        ),
        headers=adapter_api.auth_header(token),
    )
    assert created.status_code == 201, created.text
    assert created.json()["config"] == {"base_url": BASE_URL, "region_label": "***"}

    stored = yaml.safe_load(
        (adapters_root / "probe-redacting" / "manifest.yaml").read_text(encoding="utf-8")
    )
    assert stored["redaction"]["fields"] == ["region_label"]
    audit = [
        row
        for row in adapter_api.audit_rows(owner["org"]["id"])
        if row["topic"] == "system.connector.bound"
    ]
    assert audit[0]["payload"]["config"]["region_label"] == "***"
