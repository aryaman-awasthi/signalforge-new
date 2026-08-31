"""Discovery, isolation of broken manifests, and the capability matrix (REQ-030).

The registry reads a real directory here. Two failure rules carry most of the
weight: one bad folder must not cost the others, and two folders claiming the
same id must not be resolved by whichever the filesystem walked first.
"""

from __future__ import annotations

import pytest

from signalforge.contracts.envelope import AdapterKind
from signalforge.domain.adapters.capabilities import capabilities_for
from signalforge.domain.adapters.errors import AdapterNotFoundError
from signalforge.domain.adapters.manifest import (
    AdapterMode,
    Availability,
    CredentialScope,
)
from signalforge.infrastructure.adapters.registry import FileAdapterRegistry
from signalforge.tests.adapters.conftest import (
    ALL_FIXTURE_MANIFESTS,
    PULL_METRICS,
    write_manifest,
)


@pytest.mark.req("REQ-030")
def test_discovery_finds_every_manifest_in_the_tree(registry):
    ids = [manifest.id for manifest in registry.all()]
    assert ids == sorted(document["id"] for document in ALL_FIXTURE_MANIFESTS)
    assert registry.errors == ()


@pytest.mark.req("REQ-030")
def test_manifests_are_found_at_any_depth(tmp_path):
    root = tmp_path / "adapters"
    write_manifest(root / "vendor" / "group", PULL_METRICS, folder="probe-metrics")
    registry = FileAdapterRegistry(root)
    assert [m.id for m in registry.all()] == ["probe-metrics"]


@pytest.mark.req("REQ-030")
def test_an_empty_or_missing_root_is_an_empty_registry_not_a_failure(tmp_path):
    """A deployment before its first adapter is installed still has to boot."""
    registry = FileAdapterRegistry(tmp_path / "never-created")
    assert registry.all() == ()
    assert registry.errors == ()


@pytest.mark.req("REQ-030")
def test_a_broken_manifest_is_recorded_and_the_others_still_load(adapters_root):
    (adapters_root / "probe-broken").mkdir()
    (adapters_root / "probe-broken" / "manifest.yaml").write_text(
        "id: probe-broken\nkind: [unclosed\n", encoding="utf-8"
    )
    write_manifest(
        adapters_root, dict(PULL_METRICS, id="probe-invalid", capabilities=["nope"])
    )

    registry = FileAdapterRegistry(adapters_root)

    assert len(registry.all()) == len(ALL_FIXTURE_MANIFESTS), (
        "one bad folder must not cost the working adapters"
    )
    paths = {error.path for error in registry.errors}
    assert paths == {"probe-broken/manifest.yaml", "probe-invalid/manifest.yaml"}
    assert any("nope" in error.error for error in registry.errors)


@pytest.mark.req("REQ-030")
def test_an_empty_manifest_file_is_an_error_not_a_crash(adapters_root):
    (adapters_root / "probe-empty").mkdir()
    (adapters_root / "probe-empty" / "manifest.yaml").write_text("", encoding="utf-8")
    registry = FileAdapterRegistry(adapters_root)
    assert [e.path for e in registry.errors] == ["probe-empty/manifest.yaml"]


@pytest.mark.req("REQ-030")
def test_a_duplicate_adapter_id_is_refused_rather_than_silently_winning(adapters_root):
    write_manifest(adapters_root, PULL_METRICS, folder="probe-metrics-copy")
    registry = FileAdapterRegistry(adapters_root)

    assert len([m for m in registry.all() if m.id == "probe-metrics"]) == 1
    assert any("duplicate adapter id" in error.error for error in registry.errors)


@pytest.mark.req("REQ-030")
def test_lookup_and_filtering(registry):
    assert registry.get("probe-metrics").kind is AdapterKind.METRICS
    assert registry.find("nothing-here") is None
    with pytest.raises(AdapterNotFoundError):
        registry.get("nothing-here")

    assert [m.id for m in registry.list(kind=AdapterKind.ACTIONS)] == ["probe-actions"]
    assert [m.id for m in registry.list(mode=AdapterMode.PUSH)] == ["probe-alert-hook"]
    assert [m.id for m in registry.list(scope=CredentialScope.DRILL)] == ["probe-chaos"]
    assert [m.id for m in registry.list(simulated=True)] == ["probe-chaos"]
    assert "probe-chaos" not in [m.id for m in registry.list(simulated=False)]


@pytest.mark.req("REQ-030")
def test_reload_picks_up_an_adapter_installed_after_startup(adapters_root, registry):
    assert registry.find("probe-late") is None
    write_manifest(adapters_root, dict(PULL_METRICS, id="probe-late"))
    registry.reload()
    assert registry.get("probe-late").id == "probe-late"


@pytest.mark.req("REQ-030")
def test_capability_matrix_shows_supported_and_missing_per_adapter(registry):
    matrix = registry.matrix()

    row = matrix.row("probe-health")
    assert row is not None
    assert row.supported == ("get_health",)
    assert row.missing == ("run_probe",), (
        "the matrix has to show the gap, not only the coverage"
    )
    assert {cell.capability for cell in row.cells} == capabilities_for(AdapterKind.HEALTH)

    actions = matrix.row("probe-actions")
    mutating = {cell.capability for cell in actions.cells if cell.mutating}
    assert mutating == {"execute"}


@pytest.mark.req("REQ-030")
def test_capability_matrix_coverage_counts_only_runnable_adapters(registry):
    matrix = registry.matrix()

    logs = matrix.for_kind(AdapterKind.LOGS)
    assert logs.adapters == ("probe-enterprise-logs",)
    assert logs.covered == (), (
        "an interface-only entry is visible in the registry but is not coverage"
    )
    assert logs.complete is False

    metrics = matrix.for_kind(AdapterKind.METRICS)
    assert metrics.complete is True
    assert set(metrics.covered) == capabilities_for(AdapterKind.METRICS)

    traces = matrix.for_kind(AdapterKind.TRACES)
    assert traces.adapters == ()
    assert set(traces.uncovered) == capabilities_for(AdapterKind.TRACES)
    assert {c.kind for c in matrix.coverage} == set(AdapterKind), (
        "every kind gets a row so a gap is visible, not absent"
    )


@pytest.mark.req("REQ-030")
def test_availability_is_reported_as_declared(registry):
    assert registry.get("probe-enterprise-logs").availability is Availability.AVAILABLE
    assert registry.get("probe-metrics").availability is Availability.BUILT_IN
