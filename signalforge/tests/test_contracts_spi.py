"""S0-T02: adapter SPI completeness - all 13 kinds, abstract, reconcile primitive."""

import inspect

import pytest

from signalforge.contracts import spi
from signalforge.contracts.envelope import AdapterKind

EXPECTED_METHODS = {
    AdapterKind.METRICS: {"query_instant", "query_range"},
    AdapterKind.LOGS: {"search", "aggregate", "sample"},
    AdapterKind.TRACES: {"search", "get_trace"},
    AdapterKind.HEALTH: {"get_health", "run_probe"},
    AdapterKind.ALERTS: {"normalize", "fetch_active"},
    AdapterKind.CHANGES: {"normalize", "list_changes"},
    AdapterKind.TOPOLOGY: {"fetch", "normalize"},
    AdapterKind.COLLABORATION: {"post", "fetch"},
    AdapterKind.ITSM: {"create", "update", "get"},
    AdapterKind.ACTIONS: {"manifest", "execute", "get_execution"},
    AdapterKind.NOTIFICATIONS: {"send", "status"},
    AdapterKind.KNOWLEDGE: {"index", "search"},
    AdapterKind.CHAOS: {"manifest", "inject", "status", "abort"},
}


@pytest.mark.req("REQ-002")
def test_all_13_kinds_have_an_spi():
    assert set(spi.ADAPTER_SPI_BY_KIND) == set(AdapterKind)
    assert len(spi.ADAPTER_SPI_BY_KIND) == 13


@pytest.mark.req("REQ-002")
def test_spi_classes_are_abstract_and_declare_their_kind():
    for kind, cls in spi.ADAPTER_SPI_BY_KIND.items():
        assert inspect.isabstract(cls), f"{cls.__name__} must be abstract"
        assert cls.kind is kind
        assert issubclass(cls, spi.Adapter)


@pytest.mark.req("REQ-002")
def test_spi_methods_present_and_ping_universal():
    for kind, cls in spi.ADAPTER_SPI_BY_KIND.items():
        methods = {
            name
            for name, member in inspect.getmembers(cls)
            if getattr(member, "__isabstractmethod__", False)
        }
        assert "ping" in methods, f"{cls.__name__} missing ping"
        missing = EXPECTED_METHODS[kind] - methods
        assert not missing, f"{cls.__name__} missing {missing}"


@pytest.mark.req("REQ-002")
def test_pull_methods_are_async_and_normalize_is_pure_sync():
    for cls in spi.ADAPTER_SPI_BY_KIND.values():
        for name, member in inspect.getmembers(cls, inspect.isfunction):
            if not getattr(member, "__isabstractmethod__", False):
                continue
            if name == "normalize":
                assert not inspect.iscoroutinefunction(member), (
                    f"{cls.__name__}.normalize must be a pure sync translation"
                )
            else:
                assert inspect.iscoroutinefunction(member), (
                    f"{cls.__name__}.{name} must be async"
                )


@pytest.mark.req("REQ-002")
def test_actions_spi_supports_reconcile_before_retry():
    sig = inspect.signature(spi.ActionsAdapter.get_execution)
    assert "idempotency_key" in sig.parameters
    sig = inspect.signature(spi.ActionsAdapter.execute)
    assert "idempotency_key" in sig.parameters


@pytest.mark.req("REQ-002")
def test_chaos_inject_requires_scope():
    sig = inspect.signature(spi.ChaosAdapter.inject)
    assert "scope" in sig.parameters
    assert sig.parameters["scope"].default is inspect.Parameter.empty
