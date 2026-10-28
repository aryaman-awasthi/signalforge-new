"""S0-T02: action and chaos manifest JSON-schema validation."""

import pytest

from signalforge.contracts.errors import ManifestValidationError
from signalforge.contracts.manifests import (
    validate_action_manifest,
    validate_chaos_manifest,
)


def valid_action_manifest():
    return {
        "schema_version": 1,
        "actions": [
            {
                "action_id": "restart-checkout",
                "name": "Restart checkout service",
                "description": "Rolling restart of the checkout pods",
                "action_class": "restart",
                "risk": "high",
                "approver_roles": ["incident_commander"],
                "timeout_s": 120,
                "idempotent": True,
            },
            {
                "action_id": "dump-threads",
                "name": "Dump JVM threads",
                "description": "Collect a thread dump for diagnosis",
                "action_class": "diagnostic",
                "risk": "low",
            },
        ],
    }


def valid_chaos_manifest():
    return {
        "schema_version": 1,
        "scenarios": [
            {
                "scenario_id": "db-latency",
                "name": "Database latency",
                "description": "Inject latency on the primary DB pool",
                "fault_type": "latency",
                "target": "db-primary",
                "max_duration_s": 300,
                "abort_conditions": ["error_rate > 5%"],
            }
        ],
    }


@pytest.mark.req("REQ-003")
def test_valid_manifests_pass():
    validate_action_manifest(valid_action_manifest())
    validate_chaos_manifest(valid_chaos_manifest())


@pytest.mark.req("REQ-003")
def test_high_risk_action_requires_approver_roles():
    doc = valid_action_manifest()
    del doc["actions"][0]["approver_roles"]
    with pytest.raises(ManifestValidationError):
        validate_action_manifest(doc)
    doc["actions"][0]["approver_roles"] = []
    with pytest.raises(ManifestValidationError):
        validate_action_manifest(doc)


@pytest.mark.req("REQ-003")
def test_reversible_action_requires_reverse_action_id():
    doc = valid_action_manifest()
    doc["actions"][1]["reversible"] = True
    with pytest.raises(ManifestValidationError):
        validate_action_manifest(doc)
    doc["actions"][1]["reverse_action_id"] = "restore-threads"
    validate_action_manifest(doc)


@pytest.mark.req("REQ-003")
def test_unknown_fields_and_bad_enums_rejected():
    doc = valid_action_manifest()
    doc["actions"][0]["surprise"] = True
    with pytest.raises(ManifestValidationError):
        validate_action_manifest(doc)

    doc = valid_action_manifest()
    doc["actions"][0]["action_class"] = "detonate"
    with pytest.raises(ManifestValidationError):
        validate_action_manifest(doc)


@pytest.mark.req("REQ-003")
def test_chaos_scenario_requires_bounded_duration():
    doc = valid_chaos_manifest()
    del doc["scenarios"][0]["max_duration_s"]
    with pytest.raises(ManifestValidationError):
        validate_chaos_manifest(doc)
    doc["scenarios"][0]["max_duration_s"] = 0
    with pytest.raises(ManifestValidationError):
        validate_chaos_manifest(doc)


@pytest.mark.req("REQ-003")
def test_empty_manifests_rejected():
    with pytest.raises(ManifestValidationError):
        validate_action_manifest({"schema_version": 1, "actions": []})
    with pytest.raises(ManifestValidationError):
        validate_chaos_manifest({"schema_version": 1, "scenarios": []})
