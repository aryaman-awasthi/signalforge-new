"""S3-T01: the credential-scope floor and connector tenant isolation
(REQ-030, REQ-015, REQ-013). Artifact 18.

Written failing-first. Three properties have to be permanent (CLAUDE.md rule 4),
and each is asserted at the layer that owns it rather than only at the edge:

1. A read-only credential can never drive a mutating capability. Enforced twice
   - the manifest is not allowed to *offer* read_only for a mutating adapter,
     and assert_can_invoke refuses the call even if a binding somehow held one.
2. Chaos runs only on a drill credential, and a drill credential runs only chaos.
3. A binding, its config and its credentials belong to one tenant, and the
   repository - not the handler - is what refuses to cross.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from signalforge.contracts.canonical import HealthStatus
from signalforge.contracts.envelope import AdapterKind
from signalforge.domain.adapters.bindings import (
    ConnectorBinding,
    assert_can_invoke,
    assert_scope_offered,
    secret_name,
)
from signalforge.domain.adapters.errors import CredentialScopeError
from signalforge.domain.adapters.manifest import CredentialScope, manifest_from_mapping
from signalforge.infrastructure.persistence.connectors import (
    SqlConnectorBindingRepository,
)
from signalforge.infrastructure.security.secrets import FernetSecretStore
from signalforge.tests.adapters.conftest import (
    INVOKE_ACTIONS,
    PULL_METRICS,
    SIMULATED_CHAOS,
)

pytestmark = pytest.mark.anyio

NOW = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)


def binding(adapter_id: str, scope: CredentialScope, tenant_id="t1", app_id="checkout"):
    return ConnectorBinding(
        id=f"b-{tenant_id}-{adapter_id}-{scope.value}",
        tenant_id=tenant_id,
        app_id=app_id,
        adapter_id=adapter_id,
        name=f"{app_id} {adapter_id}",
        credential_scope=scope,
        config={"base_url": "http://app.internal"},
        credential_fields=("token",),
        created_at=NOW,
        created_by="u1",
        updated_at=NOW,
    )


@pytest.mark.req("REQ-030", "REQ-015")
def test_a_read_only_credential_can_never_execute_an_action():
    """The floor, stated once: even holding a binding at read_only scope - which
    the manifest rules already make unreachable - the call is refused."""
    manifest = manifest_from_mapping(INVOKE_ACTIONS)
    forged = binding("probe-actions", CredentialScope.READ_ONLY)

    with pytest.raises(CredentialScopeError) as exc:
        assert_can_invoke(manifest, forged, "execute")
    assert "read_only" in str(exc.value)

    allowed = binding("probe-actions", CredentialScope.REMEDIATION)
    assert_can_invoke(manifest, allowed, "execute")


@pytest.mark.req("REQ-030")
def test_a_capability_the_adapter_does_not_declare_is_refused():
    manifest = manifest_from_mapping(INVOKE_ACTIONS)
    with pytest.raises(CredentialScopeError):
        assert_can_invoke(
            manifest, binding("probe-actions", CredentialScope.REMEDIATION), "rollback"
        )


@pytest.mark.req("REQ-030")
def test_chaos_needs_a_drill_credential_and_a_drill_credential_stays_in_chaos():
    chaos = manifest_from_mapping(SIMULATED_CHAOS)
    assert_can_invoke(chaos, binding("probe-chaos", CredentialScope.DRILL), "inject")

    with pytest.raises(CredentialScopeError) as exc:
        assert_can_invoke(
            chaos, binding("probe-chaos", CredentialScope.REMEDIATION), "inject"
        )
    assert "drill-scoped" in str(exc.value)

    actions = manifest_from_mapping(INVOKE_ACTIONS)
    with pytest.raises(CredentialScopeError) as exc:
        assert_can_invoke(
            actions, binding("probe-actions", CredentialScope.DRILL), "execute"
        )
    assert "drill credential" in str(exc.value)


@pytest.mark.req("REQ-030")
def test_a_reading_adapter_cannot_be_asked_to_change_anything():
    metrics = manifest_from_mapping(PULL_METRICS)
    with pytest.raises(CredentialScopeError):
        assert_can_invoke(
            metrics, binding("probe-metrics", CredentialScope.READ_ONLY), "execute"
        )


@pytest.mark.req("REQ-030")
def test_a_binding_cannot_claim_a_scope_the_manifest_does_not_offer():
    metrics = manifest_from_mapping(PULL_METRICS)
    assert_scope_offered(metrics, CredentialScope.READ_ONLY)
    for scope in (CredentialScope.REMEDIATION, CredentialScope.DRILL):
        with pytest.raises(CredentialScopeError):
            assert_scope_offered(metrics, scope)


@pytest.mark.req("REQ-030")
def test_every_mutating_kind_refuses_read_only():
    """Swept rather than sampled: if a future kind gains a mutating capability,
    this fails unless its manifest rules keep read_only away from it."""
    from signalforge.domain.adapters.capabilities import MUTATING_CAPABILITIES

    for kind, capabilities in MUTATING_CAPABILITIES.items():
        manifest = manifest_from_mapping(
            {
                "id": f"probe-{kind.value}",
                "name": kind.value,
                "version": "1.0.0",
                "kind": kind.value,
                "mode": "invoke",
                "capabilities": sorted(capabilities),
                "credential_scopes": (
                    ["drill"] if kind is AdapterKind.CHAOS else ["remediation"]
                ),
                "redaction": {"policy_id": "test-none"},
            }
        )
        assert CredentialScope.READ_ONLY not in manifest.credential_scopes
        for capability in capabilities:
            with pytest.raises(CredentialScopeError):
                assert_can_invoke(
                    manifest,
                    binding(manifest.id, CredentialScope.READ_ONLY),
                    capability,
                )


@pytest.mark.req("REQ-030", "REQ-013")
async def test_the_repository_refuses_to_cross_tenants(db_session):
    repository = SqlConnectorBindingRepository(db_session)
    mine = binding("probe-metrics", CredentialScope.READ_ONLY, tenant_id="t1")
    theirs = binding("probe-metrics", CredentialScope.READ_ONLY, tenant_id="t2")
    await repository.add(mine)
    await repository.add(theirs)

    assert await repository.get("t1", mine.id) is not None
    assert await repository.get("t2", mine.id) is None, (
        "the tenant predicate is inside the repository, not in a handler"
    )
    assert [b.tenant_id for b in await repository.list("t1")] == ["t1"]
    assert await repository.delete("t2", mine.id) is False
    assert await repository.get("t1", mine.id) is not None

    # The uniqueness key is per tenant: the same app name in two orgs is two
    # bindings, not a conflict.
    assert await repository.find_conflict(
        "t1", app_id="checkout", adapter_id="probe-metrics",
        scope=CredentialScope.READ_ONLY,
    ) is not None
    assert await repository.find_conflict(
        "t3", app_id="checkout", adapter_id="probe-metrics",
        scope=CredentialScope.READ_ONLY,
    ) is None


@pytest.mark.req("REQ-030", "REQ-025")
async def test_the_health_work_list_is_cross_tenant_and_enabled_only(db_session):
    """The one documented cross-tenant read: one loop serves the install. Every
    row it returns still carries its own tenant_id, which is what everything
    downstream scopes by."""
    repository = SqlConnectorBindingRepository(db_session)
    await repository.add(binding("probe-metrics", CredentialScope.READ_ONLY, tenant_id="t1"))
    await repository.add(binding("probe-metrics", CredentialScope.READ_ONLY, tenant_id="t2"))
    disabled = binding(
        "probe-actions", CredentialScope.REMEDIATION, tenant_id="t1", app_id="payments"
    )
    await repository.add(disabled)
    await repository.update(disabled.with_enabled(False, at=NOW))

    work = await repository.list_enabled_for_health()
    assert {b.tenant_id for b in work} == {"t1", "t2"}
    assert all(b.enabled for b in work)
    assert disabled.id not in {b.id for b in work}


@pytest.mark.req("REQ-030", "REQ-015")
async def test_connector_credentials_are_ciphertext_in_the_row(db_session):
    """The secret store is shared with S1, but the naming is what makes a
    binding's credentials deletable with it and unreachable without it."""
    from sqlalchemy import select

    from cryptography.fernet import Fernet
    from signalforge.infrastructure.persistence.models import SecretRow

    store = FernetSecretStore(db_session, key=Fernet.generate_key().decode())
    name = secret_name("binding-1", "token")
    await store.put("t1", name, "read-only-token")

    row = (await db_session.execute(select(SecretRow))).scalars().one()
    assert row.name == "connector/binding-1/token"
    assert b"read-only-token" not in row.ciphertext
    assert await store.get("t1", name) == "read-only-token"
    assert await store.get("t2", name) is None
    assert "redacted" in repr(store)


@pytest.mark.req("REQ-030")
def test_health_transitions_never_invent_a_verdict():
    """UNKNOWN is a real answer and must not decay into UP: an adapter that
    could not be measured is not an adapter that is fine."""
    b = binding("probe-metrics", CredentialScope.READ_ONLY)
    assert b.status is HealthStatus.UNKNOWN

    unmeasured = b.observed_unknown(NOW, "no runtime on this node")
    assert unmeasured.status is HealthStatus.UNKNOWN
    assert unmeasured.last_ok_at is None
    assert unmeasured.freshness_s(NOW) is None

    failing = b
    for _ in range(2):
        failing = failing.observed_failure(NOW, "connection refused")
        assert failing.status is HealthStatus.DEGRADED
    failing = failing.observed_failure(NOW, "connection refused")
    assert failing.status is HealthStatus.DOWN

    assert b.observed_failure(NOW, "401", terminal=True).status is HealthStatus.DOWN
    healed = failing.observed_ok(NOW)
    assert healed.status is HealthStatus.UP
    assert healed.consecutive_failures == 0
    assert healed.last_error is None
