"""The manifest contract and its floors (REQ-030).

The registry is only as trustworthy as what it refuses to load, so most of this
file is about rejection. Each test names a rule from
domain/adapters/manifest.py and proves a manifest breaking it never becomes an
AdapterManifest at all - which is what makes every downstream consumer able to
assume the invariant instead of re-checking it.
"""

from __future__ import annotations

import pytest

from signalforge.contracts.envelope import AdapterKind
from signalforge.contracts.errors import ManifestValidationError
from signalforge.contracts.spi import ADAPTER_SPI_BY_KIND
from signalforge.domain.adapters.capabilities import (
    CAPABILITIES_BY_KIND,
    MUTATING_CAPABILITIES,
    capabilities_for,
)
from signalforge.domain.adapters.manifest import (
    MAX_TIMEOUT_S,
    AdapterMode,
    Availability,
    AuthMethod,
    CredentialScope,
    manifest_from_mapping,
)
from signalforge.tests.adapters.conftest import (
    INTERFACE_ONLY,
    INVOKE_ACTIONS,
    PULL_METRICS,
    PUSH_ALERTS,
    SIMULATED_CHAOS,
)


def variant(base: dict, **changes) -> dict:
    document = dict(base)
    document.update(changes)
    return document


@pytest.mark.req("REQ-030")
def test_capability_catalogue_is_read_off_the_frozen_spi():
    """Not a second list to maintain: every kind's capabilities are its SPI's
    abstract methods, so the contract and the registry cannot drift."""
    assert set(CAPABILITIES_BY_KIND) == set(ADAPTER_SPI_BY_KIND)
    for kind, spi in ADAPTER_SPI_BY_KIND.items():
        for capability in CAPABILITIES_BY_KIND[kind]:
            assert callable(getattr(spi, capability, None)), (
                f"{kind.value} declares {capability} which is not an SPI method"
            )
        assert "ping" not in CAPABILITIES_BY_KIND[kind], (
            "ping is required of every adapter, so it is never a declarable capability"
        )
    assert capabilities_for(AdapterKind.LOGS) == {"search", "aggregate", "sample"}
    assert capabilities_for(AdapterKind.CHAOS) == {
        "manifest", "inject", "status", "abort"
    }


@pytest.mark.req("REQ-030")
def test_a_valid_pull_manifest_loads_with_every_declared_field():
    manifest = manifest_from_mapping(PULL_METRICS)

    assert manifest.id == "probe-metrics"
    assert manifest.kind is AdapterKind.METRICS
    assert manifest.mode is AdapterMode.PULL
    assert manifest.capabilities == {"query_instant", "query_range"}
    assert manifest.credential_scopes == (CredentialScope.READ_ONLY,)
    assert manifest.auth.method is AuthMethod.BEARER
    assert manifest.auth.credential_fields == ("token",)
    assert manifest.limits.timeout_s == 5
    assert manifest.availability is Availability.BUILT_IN
    assert manifest.simulated is False
    assert manifest.config_schema["required"] == ["base_url"]
    assert manifest.is_mutating is False


@pytest.mark.req("REQ-030")
def test_capabilities_must_be_methods_of_the_declared_kind():
    with pytest.raises(ManifestValidationError) as exc:
        manifest_from_mapping(variant(PULL_METRICS, capabilities=["query_instant", "tail"]))
    assert "tail" in str(exc.value)
    assert "query_range" in str(exc.value), "the error must list what is allowed"


@pytest.mark.req("REQ-030")
def test_a_manifest_must_declare_at_least_one_capability():
    with pytest.raises(ManifestValidationError):
        manifest_from_mapping(variant(PULL_METRICS, capabilities=[]))


@pytest.mark.req("REQ-030")
def test_invoke_mode_requires_a_mutating_capability():
    manifest = manifest_from_mapping(INVOKE_ACTIONS)
    assert manifest.mode is AdapterMode.INVOKE
    assert manifest.mutating_capabilities == {"execute"}

    with pytest.raises(ManifestValidationError) as exc:
        manifest_from_mapping(
            variant(INVOKE_ACTIONS, capabilities=["manifest", "get_execution"])
        )
    assert "mutating capability" in str(exc.value)


@pytest.mark.req("REQ-030")
def test_a_reading_mode_may_not_declare_a_mutating_capability():
    """The important half: a manifest cannot smuggle a write behind pull mode."""
    with pytest.raises(ManifestValidationError) as exc:
        manifest_from_mapping(variant(INVOKE_ACTIONS, mode="pull"))
    assert "use mode 'invoke'" in str(exc.value)


@pytest.mark.req("REQ-030")
def test_push_mode_requires_normalize():
    assert manifest_from_mapping(PUSH_ALERTS).mode is AdapterMode.PUSH
    with pytest.raises(ManifestValidationError) as exc:
        manifest_from_mapping(variant(PUSH_ALERTS, capabilities=["fetch_active"]))
    assert "normalize" in str(exc.value)


@pytest.mark.req("REQ-030")
def test_pull_mode_needs_a_fetching_capability():
    with pytest.raises(ManifestValidationError):
        manifest_from_mapping(
            variant(PUSH_ALERTS, mode="pull", capabilities=["normalize"])
        )


@pytest.mark.req("REQ-030", "REQ-015")
def test_a_mutating_adapter_may_not_offer_the_read_only_scope():
    """The credential half of the policy floor, enforced before any policy
    exists: a read-only key is never offered for something that can change the
    integrated system, so it can never be chosen for one either."""
    with pytest.raises(ManifestValidationError) as exc:
        manifest_from_mapping(
            variant(INVOKE_ACTIONS, credential_scopes=["read_only", "remediation"])
        )
    assert "read_only" in str(exc.value)
    assert "execute" in str(exc.value)


@pytest.mark.req("REQ-030")
def test_chaos_adapters_offer_only_the_drill_scope():
    assert manifest_from_mapping(SIMULATED_CHAOS).credential_scopes == (
        CredentialScope.DRILL,
    )
    for scopes in (["remediation"], ["drill", "remediation"]):
        with pytest.raises(ManifestValidationError) as exc:
            manifest_from_mapping(variant(SIMULATED_CHAOS, credential_scopes=scopes))
        assert "drill" in str(exc.value)


@pytest.mark.req("REQ-030")
def test_free_text_kinds_must_state_a_redaction_policy():
    """Logs, traces, collaboration, knowledge and itsm carry customer text into
    evidence and prompts. The empty answer is allowed; not answering is not."""
    assert manifest_from_mapping(INTERFACE_ONLY).redaction.policy_id == "logs-default"

    without = dict(INTERFACE_ONLY)
    without.pop("redaction")
    with pytest.raises(ManifestValidationError) as exc:
        manifest_from_mapping(without)
    assert "redaction.policy_id" in str(exc.value)

    # Metrics carry numbers and labels, so they are not forced to.
    assert manifest_from_mapping(PULL_METRICS).redaction.policy_id is None


@pytest.mark.req("REQ-030")
def test_redaction_rules_require_a_policy_id_and_valid_patterns():
    with pytest.raises(ManifestValidationError):
        manifest_from_mapping(
            variant(PULL_METRICS, redaction={"fields": ["token"]})
        )
    with pytest.raises(ManifestValidationError) as exc:
        manifest_from_mapping(
            variant(PULL_METRICS, redaction={"policy_id": "p", "patterns": ["(unclosed"]})
        )
    assert "not a valid regex" in str(exc.value)


@pytest.mark.req("REQ-030")
def test_limits_are_bounded_by_the_domain_not_by_the_manifest():
    manifest = manifest_from_mapping(variant(PULL_METRICS, limits={"timeout_s": 1}))
    assert manifest.limits.timeout_s == 1

    with pytest.raises(ManifestValidationError) as exc:
        manifest_from_mapping(
            variant(PULL_METRICS, limits={"timeout_s": MAX_TIMEOUT_S + 1})
        )
    assert "timeout_s" in str(exc.value)
    for bad in ({"max_rows": 0}, {"max_result_bytes": -1}, {"max_calls_per_minute": 0}):
        with pytest.raises(ManifestValidationError):
            manifest_from_mapping(variant(PULL_METRICS, limits=bad))


@pytest.mark.req("REQ-030")
def test_auth_and_credential_fields_must_agree():
    with pytest.raises(ManifestValidationError):
        manifest_from_mapping(
            variant(PULL_METRICS, auth={"method": "none", "credential_fields": ["token"]})
        )
    with pytest.raises(ManifestValidationError):
        manifest_from_mapping(variant(PULL_METRICS, auth={"method": "bearer"}))
    with pytest.raises(ManifestValidationError):
        manifest_from_mapping(
            variant(PULL_METRICS, auth={"method": "bearer", "credential_fields": ["Token!"]})
        )


@pytest.mark.req("REQ-030")
def test_identity_and_unknown_fields_are_rejected():
    for bad_id in ("Probe", "p", "probe_metrics", "probe-"):
        with pytest.raises(ManifestValidationError):
            manifest_from_mapping(variant(PULL_METRICS, id=bad_id))
    with pytest.raises(ManifestValidationError):
        manifest_from_mapping(variant(PULL_METRICS, version="1.0"))
    with pytest.raises(ManifestValidationError) as exc:
        manifest_from_mapping(variant(PULL_METRICS, retries=3))
    assert "unknown manifest fields" in str(exc.value)


@pytest.mark.req("REQ-030")
def test_a_config_schema_that_is_not_a_json_schema_is_refused():
    with pytest.raises(ManifestValidationError) as exc:
        manifest_from_mapping(
            variant(PULL_METRICS, config_schema={"type": "not-a-type"})
        )
    assert "config_schema" in str(exc.value)


@pytest.mark.req("REQ-030")
def test_missing_required_fields_name_themselves():
    incomplete = {"id": "probe-x", "name": "X"}
    with pytest.raises(ManifestValidationError) as exc:
        manifest_from_mapping(incomplete)
    message = str(exc.value)
    for field in ("version", "kind", "mode", "capabilities", "credential_scopes"):
        assert field in message


@pytest.mark.req("REQ-030")
def test_every_mutating_capability_belongs_to_its_kind():
    """A typo in MUTATING_CAPABILITIES would quietly widen what a read-only
    credential is allowed to do, so the table is checked against the SPI."""
    for kind, capabilities in MUTATING_CAPABILITIES.items():
        assert capabilities <= capabilities_for(kind), (
            f"{kind.value} lists a mutating capability its SPI does not have"
        )


@pytest.mark.req("REQ-030")
def test_redaction_masks_declared_fields_on_the_way_out():
    manifest = manifest_from_mapping(
        variant(
            PULL_METRICS,
            redaction={"policy_id": "metrics-basic", "fields": ["region_label"]},
        )
    )
    masked = manifest.redact({"base_url": "http://app.internal", "region_label": "eu"})
    assert masked == {"base_url": "http://app.internal", "region_label": "***"}
