"""What this build actually ships (REQ-031, REQ-033, REQ-035).

The registry's rules were proven in S3-T01 against fixture manifests. These
tests are about the real tree: that every shipped manifest satisfies those rules,
that a runtime exists for exactly the adapters that claim one, and that the five
enterprise connectors are visible as interfaces without ever being counted as
working integrations.

The last one matters most. "Datadog appears in the registry" and "this
deployment can query Datadog" must never be the same statement, and the
capability matrix is where that distinction is enforced.
"""

from __future__ import annotations

import pytest

from signalforge.contracts.envelope import AdapterKind
from signalforge.domain.adapters.bindings import ConnectorBinding
from signalforge.domain.adapters.manifest import (
    AdapterMode,
    Availability,
    CredentialScope,
)
from signalforge.domain.adapters.ports import AdapterContext
from signalforge.infrastructure.adapters.builtin import BUILTIN_RUNTIMES

SHIPPED = {
    "http-observe-metrics", "http-observe-logs", "http-observe-traces",
    "http-observe-health", "http-observe-actions", "http-observe-chaos",
    "prometheus", "grafana-alert-webhook", "deploy-webhook", "topo-import",
    "itsm-lite", "teams-sim", "teams-sim-dm", "notify-in-app",
    "notify-browser-push", "page-sim", "kb-index",
}

INTERFACE_ONLY = {"datadog", "splunk", "servicenow", "jenkins", "github"}


@pytest.fixture
def registry(api):
    return api.container.adapter_registry


@pytest.mark.req("REQ-031", "REQ-033", "REQ-035")
def test_every_shipped_manifest_loads(registry):
    assert registry.errors == (), (
        f"a shipped manifest was rejected at load: {registry.errors}"
    )
    installed = {manifest.id for manifest in registry.all()}
    assert SHIPPED | INTERFACE_ONLY <= installed, (
        f"missing from the shipped tree: {sorted((SHIPPED | INTERFACE_ONLY) - installed)}"
    )


@pytest.mark.req("REQ-031")
def test_a_runtime_exists_for_exactly_the_built_in_adapters(api, registry):
    runtimes = api.container.adapter_runtimes
    for manifest in registry.all():
        has_runtime = runtimes.has(manifest.id)
        if manifest.availability is Availability.BUILT_IN:
            assert has_runtime, f"{manifest.id} claims built_in but ships no runtime"
        else:
            assert not has_runtime, (
                f"{manifest.id} is declared available yet a runtime is installed; "
                "flip its availability to built_in or the registry is lying"
            )


@pytest.mark.req("REQ-031")
def test_each_runtime_speaks_the_kind_its_manifest_declares(api, registry):
    """A manifest is what the platform reasons about, so a runtime whose SPI
    kind disagrees with it is a wiring bug that must not reach a poller."""
    runtimes = api.container.adapter_runtimes
    for adapter_id in BUILTIN_RUNTIMES:
        manifest = registry.get(adapter_id)
        binding = ConnectorBinding(
            id="b1",
            tenant_id="t1",
            app_id="checkout",
            adapter_id=adapter_id,
            name="probe",
            credential_scope=manifest.credential_scopes[0],
        )
        adapter = runtimes.build(
            AdapterContext(manifest=manifest, binding=binding, credentials={})
        )
        assert adapter.kind is manifest.kind


@pytest.mark.req("REQ-031")
def test_every_shipped_capability_is_implemented_by_its_runtime(api, registry):
    """A manifest promising a capability its class does not define would fail at
    3am with an AttributeError instead of at install time."""
    runtimes = api.container.adapter_runtimes
    missing: list[str] = []
    for adapter_id in BUILTIN_RUNTIMES:
        manifest = registry.get(adapter_id)
        binding = ConnectorBinding(
            id="b1", tenant_id="t1", app_id="checkout", adapter_id=adapter_id,
            name="probe", credential_scope=manifest.credential_scopes[0],
        )
        adapter = runtimes.build(
            AdapterContext(manifest=manifest, binding=binding, credentials={})
        )
        for capability in sorted(manifest.capabilities):
            if not callable(getattr(adapter, capability, None)):
                missing.append(f"{adapter_id}.{capability}")
    assert not missing, f"manifests promise what their runtimes do not implement: {missing}"


@pytest.mark.req("REQ-031")
def test_the_enterprise_connectors_are_available_not_installed(api):
    owner = api.signup()
    token = owner["tokens"]["access_token"]
    listing = api.client.get("/adapters", headers=api.auth_header(token)).json()
    by_id = {entry["id"]: entry for entry in listing["adapters"]}
    assert listing["errors"] == []
    for adapter_id in INTERFACE_ONLY:
        entry = by_id[adapter_id]
        assert entry["availability"] == "available"
        assert entry["status"] == "available", (
            "an interface-only connector must never read as installed"
        )
        assert entry["runnable"] is False


@pytest.mark.req("REQ-031")
def test_interface_only_connectors_are_never_counted_as_coverage(api):
    """datadog declares both metrics capabilities. If it counted, removing the
    two real metrics adapters would still show metrics as covered."""
    from signalforge.domain.adapters.matrix import capability_matrix

    registry = api.container.adapter_registry
    only_enterprise = [m for m in registry.all() if m.id in INTERFACE_ONLY]
    matrix = capability_matrix(only_enterprise)
    for coverage in matrix.coverage:
        assert coverage.covered == (), (
            f"{coverage.kind.value} counted an interface-only adapter as coverage"
        )
    assert {row.adapter_id for row in matrix.rows} == INTERFACE_ONLY, (
        "they must still be listed - hiding them would be the opposite lie"
    )


@pytest.mark.req("REQ-031", "REQ-033", "REQ-035")
def test_the_shipped_set_covers_every_kind_it_claims_to(api):
    matrix = api.container.adapters.matrix()
    complete = {c.kind for c in matrix.coverage if c.complete}
    # Eleven of thirteen. The two gaps are real and named rather than papered
    # over: the observe contract has no endpoint for polling active alerts or
    # listing changes, so no shipped adapter can honestly claim either. Both
    # arrive by push (grafana-alert-webhook, deploy-webhook), and the pull-mode
    # interfaces are declared by the jenkins entry.
    assert AdapterKind.ALERTS not in complete
    assert AdapterKind.CHANGES not in complete
    assert complete == set(AdapterKind) - {AdapterKind.ALERTS, AdapterKind.CHANGES}

    alerts = matrix.for_kind(AdapterKind.ALERTS)
    assert alerts.uncovered == ("fetch_active",)
    changes = matrix.for_kind(AdapterKind.CHANGES)
    assert changes.uncovered == ("list_changes",)


@pytest.mark.req("REQ-033")
def test_every_simulated_adapter_says_so_in_its_manifest(registry):
    simulated = {m.id for m in registry.all() if m.simulated}
    assert simulated == {"itsm-lite", "teams-sim", "teams-sim-dm", "page-sim"}, (
        "the SIMULATED badge is driven by this flag; anything faking an "
        "integrated system must carry it"
    )


@pytest.mark.req("REQ-031")
def test_no_shipped_mutating_adapter_offers_a_read_only_credential(registry):
    for manifest in registry.all():
        if manifest.is_mutating:
            assert CredentialScope.READ_ONLY not in manifest.credential_scopes, (
                f"{manifest.id} can mutate and must not offer read_only"
            )
        if manifest.kind is AdapterKind.CHAOS:
            assert list(manifest.credential_scopes) == [CredentialScope.DRILL]


@pytest.mark.req("REQ-031")
def test_free_text_adapters_declare_a_redaction_policy(registry):
    """Every shipped manifest declares one, including the kinds that are not
    required to - a policy that redacts nothing is still a written promise."""
    undeclared = [m.id for m in registry.all() if not m.redaction.declared]
    assert not undeclared, f"shipped adapters with no redaction policy: {undeclared}"


@pytest.mark.req("REQ-031")
def test_push_adapters_are_push_and_pull_adapters_are_pull(registry):
    modes = {m.id: m.mode for m in registry.all()}
    assert modes["grafana-alert-webhook"] is AdapterMode.PUSH
    assert modes["deploy-webhook"] is AdapterMode.PUSH
    assert modes["github"] is AdapterMode.PUSH
    assert modes["prometheus"] is AdapterMode.PULL
    assert modes["kb-index"] is AdapterMode.PULL
    assert modes["http-observe-actions"] is AdapterMode.INVOKE
    assert modes["http-observe-chaos"] is AdapterMode.INVOKE
