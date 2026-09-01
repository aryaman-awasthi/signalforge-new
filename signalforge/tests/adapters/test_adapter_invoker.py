"""The one path from a binding to a live adapter call (REQ-030, REQ-031).

Five checks happen on every call and none of them is a caller's responsibility
to remember. Each has a test here, and the failure-design cases - a binding
turned off mid-flow, an adapter uninstalled under a live binding, a scope
narrowed after the binding was made - are the point: they are the situations in
which a "check it once at bind time" design silently keeps working.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest

from signalforge.contracts.errors import AdapterTimeoutError, NotSupportedError
from signalforge.domain.adapters.errors import (
    AdapterNotFoundError,
    ConnectorBindingNotFoundError,
    ConnectorDisabledError,
    CredentialScopeError,
)
from signalforge.tests.adapters.conftest import ObserveHarness, connect

pytestmark = pytest.mark.anyio

AT = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def owner(api, observe):
    signup = api.signup()

    class Owner:
        token = signup["tokens"]["access_token"]
        tenant = signup["org"]["id"]
        user = signup["user"]["id"]
        container = api.container

        def bind(self, adapter_id="http-observe-metrics", **kwargs):
            return connect(api, self.token, adapter_id, **kwargs)

        async def invoke(self, binding, capability, *args, **kwargs):
            return await api.container.invoker.invoke(
                self.tenant, binding["id"], capability, *args, **kwargs
            )

    return Owner()


@pytest.mark.req("REQ-030", "REQ-013")
async def test_another_tenants_binding_is_not_found(api, owner):
    binding = owner.bind()
    other = api.signup(org="Other Corp", email="owner@other.io")
    with pytest.raises(ConnectorBindingNotFoundError) as refused:
        await api.container.invoker.invoke(
            other["org"]["id"], binding["id"], "query_instant", "", AT
        )
    assert binding["id"] in str(refused.value)
    assert "not found" in str(refused.value), (
        "the message must not confirm the id exists elsewhere"
    )


@pytest.mark.req("REQ-030")
async def test_a_disabled_binding_is_refused_and_says_why(api, owner):
    """Human override mid-flow: an operator turns the connector off and the very
    next call stops, with a message that does not send anyone to create a
    duplicate."""
    binding = owner.bind()
    await owner.invoke(binding, "query_instant", "", AT)

    api.client.patch(
        f"/connectors/{binding['id']}",
        json={"enabled": False},
        headers=api.auth_header(owner.token),
    )
    with pytest.raises(ConnectorDisabledError) as refused:
        await owner.invoke(binding, "query_instant", "", AT)
    assert "disabled" in str(refused.value)


@pytest.mark.req("REQ-030")
async def test_an_adapter_uninstalled_under_a_live_binding_is_an_orphan(api, owner):
    binding = owner.bind()
    registry = api.container.adapter_registry
    registry._manifests.pop("http-observe-metrics")  # noqa: SLF001 - simulates removal
    with pytest.raises(AdapterNotFoundError) as refused:
        await owner.invoke(binding, "query_instant", "", AT)
    assert "orphaned" in str(refused.value)


@pytest.mark.req("REQ-030")
async def test_a_binding_whose_node_has_no_runtime_is_refused_honestly(api, owner):
    binding = owner.bind()
    api.container.adapter_runtimes._factories.pop("http-observe-metrics")  # noqa: SLF001
    with pytest.raises(AdapterNotFoundError) as refused:
        await owner.invoke(binding, "query_instant", "", AT)
    assert "no runtime" in str(refused.value)
    assert "availability: built_in" in str(refused.value)


@pytest.mark.req("REQ-030", "REQ-012")
async def test_the_credential_scope_floor_is_checked_on_every_call(api, owner):
    """A permission change mid-flow: the manifest is edited to stop offering the
    scope this binding holds, and the next call is refused even though the
    binding, the runtime and the credential are all still there."""
    binding = owner.bind("http-observe-actions")
    await owner.invoke(binding, "manifest")

    manifest = api.container.adapter_registry.get("http-observe-actions")
    manifest.__dict__["capabilities"] = frozenset({"manifest", "get_execution"})
    with pytest.raises(CredentialScopeError) as refused:
        await owner.invoke(binding, "execute", "collect-diagnostics", {}, "k")
    assert "does not support 'execute'" in str(refused.value)


@pytest.mark.req("REQ-030")
async def test_chaos_runs_on_a_drill_credential_and_nothing_else(api, owner):
    """There is no way to make a chaos binding on another scope through the API,
    because the manifest refuses to offer one - so the floor underneath that is
    asserted directly, on the credential rather than on the person.
    """
    from dataclasses import replace

    from signalforge.domain.adapters.bindings import (
        ConnectorBinding,
        assert_can_invoke,
    )
    from signalforge.domain.adapters.manifest import CredentialScope

    binding = owner.bind("http-observe-chaos")
    assert binding["credential_scope"] == "drill"
    manifest = api.container.adapter_registry.get("http-observe-chaos")
    entity = ConnectorBinding(
        id=binding["id"],
        tenant_id=owner.tenant,
        app_id=binding["app_id"],
        adapter_id=binding["adapter_id"],
        name=binding["name"],
        credential_scope=CredentialScope.DRILL,
    )
    assert_can_invoke(manifest, entity, "inject")

    with pytest.raises(CredentialScopeError) as remediation:
        assert_can_invoke(
            manifest,
            replace(entity, credential_scope=CredentialScope.REMEDIATION),
            "inject",
        )
    assert "requires a drill-scoped credential" in str(remediation.value)

    with pytest.raises(CredentialScopeError) as read_only:
        assert_can_invoke(
            manifest,
            replace(entity, credential_scope=CredentialScope.READ_ONLY),
            "inject",
        )
    assert "cannot run on a read_only credential" in str(read_only.value), (
        "a read-only credential is stopped by the mutating rule first; either "
        "refusal is correct, and both are floors"
    )

    # And the mirror image: a drill credential is not a general remediation key.
    actions = api.container.adapter_registry.get("http-observe-actions")
    with pytest.raises(CredentialScopeError) as refused:
        assert_can_invoke(
            actions,
            replace(entity, adapter_id="http-observe-actions"),
            "execute",
        )
    assert "drill credential" in str(refused.value)


@pytest.mark.req("REQ-030")
async def test_a_capability_the_adapter_does_not_declare_is_refused(owner):
    binding = owner.bind("http-observe-logs")
    with pytest.raises(CredentialScopeError):
        await owner.invoke(binding, "query_instant", "", AT)


@pytest.mark.req("REQ-030")
async def test_ping_is_always_allowed_because_health_depends_on_it(owner):
    """`ping` is on the base SPI class and is never a declarable capability, so
    the capability check must not refuse it - the health loop calls nothing
    else."""
    assert await owner.invoke(owner.bind(), "ping") is True


@pytest.mark.req("REQ-030")
async def test_a_runtime_that_hangs_is_stopped_by_the_watchdog(api, owner):
    from signalforge.application.adapters.invoker import capability_watchdog_s

    binding = owner.bind()
    manifest = api.container.adapter_registry.get("http-observe-metrics")
    manifest.limits.__dict__["timeout_s"] = 0.01

    class Hanging:
        kind = manifest.kind

        def __init__(self, context):
            self.context = context

        async def ping(self):
            await asyncio.sleep(30)
            return True

        async def query_instant(self, query, at):
            await asyncio.sleep(30)
            return []

        async def query_range(self, *args):
            return []

    api.container.adapter_runtimes._factories["http-observe-metrics"] = Hanging  # noqa: SLF001
    with pytest.raises(AdapterTimeoutError) as stopped:
        await owner.invoke(binding, "query_instant", "", AT)
    assert str(capability_watchdog_s(0.01)) in str(stopped.value)


@pytest.mark.req("REQ-030")
async def test_the_watchdog_leaves_room_for_a_capability_that_makes_two_calls(api):
    """Set to a single call's timeout it would fire during the second call of
    execute() every time, and before the adapter could report UNCERTAIN."""
    from signalforge.application.adapters.invoker import capability_watchdog_s

    assert capability_watchdog_s(10.0) > 10.0


@pytest.mark.req("REQ-030")
async def test_a_declared_capability_the_runtime_lacks_is_refused_clearly(api, owner):
    """Guards a packaging fault: a manifest promising something its class does
    not define fails here with the capability named, not with an AttributeError
    from inside a poller."""
    binding = owner.bind()
    manifest = api.container.adapter_registry.get("http-observe-metrics")
    manifest.__dict__["capabilities"] = frozenset({"query_instant", "query_range"})

    class WithoutRange:
        kind = manifest.kind

        def __init__(self, context):
            self.context = context

        async def ping(self):
            return True

        async def query_instant(self, query, at):
            return []

    api.container.adapter_runtimes._factories[  # noqa: SLF001
        "http-observe-metrics"
    ] = WithoutRange
    with pytest.raises(NotSupportedError) as refused:
        await owner.invoke(binding, "query_range", "", AT, AT, 1.0)
    assert "query_range" in str(refused.value)


@pytest.mark.req("REQ-030", "REQ-025")
async def test_every_call_is_counted_and_timed(api, owner):
    binding = owner.bind()
    await owner.invoke(binding, "query_instant", "", AT)
    body = api.client.get("/metrics").text
    assert (
        'signalforge_adapter_calls_total{adapter="http-observe-metrics",'
        'capability="query_instant",outcome="ok"} 1' in body
    )
    assert "signalforge_adapter_call_duration_seconds_count" in body
    assert owner.tenant not in body, "/metrics is unauthenticated: no tenant ids"
    assert binding["id"] not in body


@pytest.mark.req("REQ-030", "REQ-025")
async def test_a_failed_call_is_counted_as_a_failure(api, owner, observe):
    binding = owner.bind()
    observe.state.fail("/observe/metrics", 500)
    with pytest.raises(Exception):
        await owner.invoke(binding, "query_instant", "", AT)
    body = api.client.get("/metrics").text
    assert 'outcome="error"' in body


@pytest.mark.req("REQ-030")
async def test_resolve_hands_the_decrypted_credential_to_the_instance_only(owner):
    binding = owner.bind()
    resolved = await owner.container.invoker.resolve(
        owner.tenant, binding["id"], "query_instant"
    )
    assert resolved.adapter.context.credentials == {"token": "app-token"}
    assert "app-token" not in repr(resolved.adapter)
    assert "app-token" not in repr(resolved.adapter.context)
    assert "app-token" not in repr(resolved.adapter.transport())
