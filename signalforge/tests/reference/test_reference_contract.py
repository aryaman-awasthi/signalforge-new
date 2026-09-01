"""The reference harness conforms to the frozen contracts (REQ-038).

These tests read the harness's own sources and published manifests. They exist
because the sprint's claim is not "an application can be onboarded" but "an
application that serves *these* contracts can be onboarded with no code written
for it", and the only way to check the first half of that is against the
application's own declarations.

Every one of them fails if the harness drifts: a path renamed, a manifest action
without an implementation, a metrics content type quietly given a charset, a
credential scope loosened, or the deployment pipeline learning about a
SignalForge path that is not frozen.
"""

from __future__ import annotations

import re
import subprocess

import pytest

from signalforge.contracts.canonical import (
    ActionClass,
    ActionDefinition,
    ChaosScenario,
    HIGH_RISK_ACTION_CLASSES,
    RiskLevel,
)
from signalforge.contracts.ingest import HDR_KEY_ID, HDR_SIGNATURE, HDR_TIMESTAMP, INGEST_PATHS
from signalforge.contracts.manifests import validate_action_manifest, validate_chaos_manifest
from signalforge.contracts.observe import OBSERVE_METRICS_CONTENT_TYPE, OBSERVE_PATHS
from signalforge.contracts.roles import ROLE_PERMISSIONS, Permission, Role
from signalforge.tests.reference.conftest import (
    ACTION_HANDLERS,
    API_KEY_FILTER,
    CHAOS_ENGINE,
    DEPLOY_SCRIPT,
    FABRIC,
    HARNESS,
    OBSERVE_CONTROLLER,
    ROOT,
    SCHEMA_SQL,
    action_manifest,
    chaos_manifest,
    mapped_paths,
    read,
    registered_ids,
)

APPROVERS = {
    role.value for role, granted in ROLE_PERMISSIONS.items()
    if Permission.APPROVE_HIGH_RISK in granted
}


# --- the observe surface -----------------------------------------------------


@pytest.mark.req("REQ-038")
def test_the_harness_maps_exactly_the_frozen_observe_paths():
    """Not a subset and not a superset.

    A missing path means an adapter that cannot be bound; an extra path under
    /observe means a surface the contract does not describe and no adapter will
    ever call, which is the kind of thing that quietly becomes a private API.
    """
    mapped = {path for path in mapped_paths(read(OBSERVE_CONTROLLER))}
    assert mapped == set(OBSERVE_PATHS.values()), {
        "missing": sorted(set(OBSERVE_PATHS.values()) - mapped),
        "unexpected": sorted(mapped - set(OBSERVE_PATHS.values())),
    }


@pytest.mark.req("REQ-038")
def test_the_metrics_endpoint_declares_the_frozen_content_type():
    source = read(OBSERVE_CONTROLLER)
    assert f'"{OBSERVE_METRICS_CONTENT_TYPE}"' in source
    # Set as a literal header rather than through a MediaType, because Spring
    # appends a charset to a parsed text/plain and the frozen constant has none.
    assert "HttpHeaders.CONTENT_TYPE, METRICS_CONTENT_TYPE" in source


# --- the action manifest -----------------------------------------------------


@pytest.mark.req("REQ-038")
def test_the_action_manifest_validates_against_the_frozen_schema():
    document = action_manifest()
    validate_action_manifest(document)
    actions = [ActionDefinition.model_validate(item) for item in document["actions"]]
    assert len(actions) >= 5
    assert len({action.action_id for action in actions}) == len(actions)


@pytest.mark.req("REQ-038")
def test_every_high_risk_action_names_approvers_who_can_actually_approve():
    """A manifest may name any role string; only some of them hold the
    permission. Naming a role that cannot approve makes an action unapprovable
    at exactly the moment it is needed, so the harness is held to the frozen
    matrix rather than to plausible-looking role names."""
    for item in action_manifest()["actions"]:
        action = ActionDefinition.model_validate(item)
        if action.risk is not RiskLevel.HIGH and action.action_class not in HIGH_RISK_ACTION_CLASSES:
            continue
        assert action.approver_roles, f"{action.action_id} is high risk and names no approver"
        for role in action.approver_roles:
            assert role in APPROVERS, (
                f"{action.action_id} names {role!r} as an approver, but the frozen roles matrix "
                f"gives approve_high_risk only to {sorted(APPROVERS)}"
            )
            Role(role)


@pytest.mark.req("REQ-038")
def test_the_manifest_covers_the_high_risk_classes_the_policy_floor_exists_for():
    """The harness is the thing the floor is demonstrated against, so it has to
    publish actions the floor will actually refuse to automate."""
    published = {ActionClass(item["action_class"]) for item in action_manifest()["actions"]}
    assert published & HIGH_RISK_ACTION_CLASSES, "no high-risk action to refuse"
    assert ActionClass.RESTART in published, "no restart action for policy to block"
    assert ActionClass.ROLLBACK in published, "no rollback action to reconcile"
    assert ActionClass.DIAGNOSTIC in published, "nothing safe enough to run without approval"


@pytest.mark.req("REQ-038")
def test_a_reversible_action_names_a_reverse_that_is_published():
    ids = {item["action_id"] for item in action_manifest()["actions"]}
    for item in action_manifest()["actions"]:
        if item.get("reversible"):
            reverse = item.get("reverse_action_id")
            assert reverse in ids, (
                f"{item['action_id']} is reversible by {reverse!r}, which this application "
                "does not publish"
            )


@pytest.mark.req("REQ-038")
def test_every_published_action_has_an_implementation_and_vice_versa():
    """The harness refuses to start when these disagree; this asserts the same
    property without a JVM, so the drift is caught by the suite rather than by a
    boot failure during a demo."""
    published = {item["action_id"] for item in action_manifest()["actions"]}
    implemented = registered_ids(read(ACTION_HANDLERS), "handlers.put")
    assert published == implemented, {
        "published with no handler": sorted(published - implemented),
        "implemented but not published": sorted(implemented - published),
    }


# --- the chaos manifest ------------------------------------------------------


@pytest.mark.req("REQ-038")
def test_the_chaos_manifest_validates_against_the_frozen_schema():
    document = chaos_manifest()
    validate_chaos_manifest(document)
    scenarios = [ChaosScenario.model_validate(item) for item in document["scenarios"]]
    assert scenarios
    for scenario in scenarios:
        assert scenario.simulated, f"{scenario.scenario_id} is not marked simulated"
        assert 0 < scenario.max_duration_s <= 3600, (
            f"{scenario.scenario_id} declares a maximum duration that is not a bound"
        )
        assert scenario.blast_radius, f"{scenario.scenario_id} states no blast radius"
        assert scenario.abort_conditions, f"{scenario.scenario_id} states no abort condition"


@pytest.mark.req("REQ-038")
def test_every_published_scenario_has_a_fault_and_vice_versa():
    published = {item["scenario_id"] for item in chaos_manifest()["scenarios"]}
    implemented = registered_ids(read(CHAOS_ENGINE), "faults.put")
    assert published == implemented, {
        "published with no fault": sorted(published - implemented),
        "implemented but not published": sorted(implemented - published),
    }


@pytest.mark.req("REQ-038")
def test_an_injection_needs_a_drill_scope_and_expires_on_its_own():
    source = read(CHAOS_ENGINE)
    assert 'DRILL_SCOPE_PREFIX = "drill:"' in source
    assert "assertDrillScope(scope)" in source
    # An injection that outlives its declared maximum duration is a fault left
    # behind by whoever walked away, so the harness reverts it itself.
    assert "@Scheduled" in source and "expire()" in source
    assert "closeInjectionsFromAPreviousProcess" in source


# --- credential scopes -------------------------------------------------------


@pytest.mark.req("REQ-038")
def test_the_harness_enforces_the_credential_scopes_the_adapters_declare():
    """The platform refuses chaos on a non-drill credential. This asserts the
    application refuses it too, which is what turns a policy into a control."""
    source = read(API_KEY_FILTER)
    assert 'path.startsWith("/observe/chaos")' in source and "Scope.DRILL" in source
    assert 'path.startsWith("/observe/actions")' in source and "Scope.REMEDIATION" in source
    assert 'path.startsWith("/api/deploy")' in source and "Scope.DEPLOY" in source
    # 401 for no credential, 403 for a valid credential with the wrong scope.
    # SignalForge's auto-discovery reads either as "served, and it wants a
    # credential", which is exactly what is true of a guarded observe path.
    assert re.search(r"refuse\(\s*response,\s*401", source)
    assert re.search(r"refuse\(\s*response,\s*403", source)


# --- the ledger --------------------------------------------------------------


@pytest.mark.req("REQ-038")
def test_the_executions_table_is_keyed_by_the_idempotency_key():
    """This is what makes reconcile-before-retry possible from the outside: the
    key the caller chose is the primary key, so a retry cannot become a second
    execution and a lost response is still findable."""
    schema = read(SCHEMA_SQL)
    executions = re.search(r"create table if not exists executions \((.*?)\);", schema, re.S)
    assert executions, "the harness declares no executions table"
    body = executions.group(1)
    assert re.search(r"idempotency_key\s+varchar\(\d+\)\s+primary key", body)
    assert "execution_id" in body and "state" in body
    assert "requested_at" in body and "started_at" in body and "finished_at" in body
    assert "create unique index if not exists idx_executions_execution_id" in schema


@pytest.mark.req("REQ-038")
def test_a_payment_lost_at_the_rail_has_a_state_of_its_own():
    """UNKNOWN is the reason the harness can demonstrate anything about
    uncertain external state. Folding it into FAILED would make every
    reconciliation story unreachable."""
    states = read(HARNESS / "src/main/java/com/novapay/paysphere/ledger/PaymentState.java")
    for state in ("INITIATED", "PENDING", "SUCCESS", "FAILED", "UNKNOWN"):
        assert state in states
    service = read(HARNESS / "src/main/java/com/novapay/paysphere/payments/PaymentService.java")
    assert "transit.uncertain()" in service and "PaymentState.UNKNOWN" in service


# --- the fabric --------------------------------------------------------------


@pytest.mark.req("REQ-038")
def test_the_fabric_declares_its_seven_links_and_two_routes():
    source = read(FABRIC)
    constants = dict(re.findall(r'public static final String (\w+) = "([^"]+)";', source))
    names = {"GW_A", "GW_B", "AGG_A", "AGG_B", "FIB_7", "CORE", "RAIL"}
    links = {value for name, value in constants.items() if name in names}
    assert links == {"GW-A", "GW-B", "AGG-A", "AGG-B", "FIB-7", "CORE", "RAIL"}
    assert "List.of(GW_A, GW_B, AGG_A, AGG_B, FIB_7, CORE, RAIL)" in source
    assert "ROUTE_PRIMARY" in source and "ROUTE_SECONDARY" in source
    # region-b's primary route crosses the shared fibre span and its secondary
    # does not; that asymmetry is the whole reason a single-region incident is
    # possible here, and a failover is worth performing.
    assert "List.of(GW_B, AGG_B, FIB_7, CORE, RAIL)" in source
    assert "List.of(GW_B, AGG_A, CORE, RAIL)" in source


@pytest.mark.req("REQ-038")
def test_the_region_label_is_a_metric_label_and_a_request_header():
    controller = read(
        HARNESS / "src/main/java/com/novapay/paysphere/payments/PaymentsController.java"
    )
    assert 'REGION_HEADER = "X-Region"' in controller
    assert 'SYNTHETIC_HEADER = "X-SignalForge-Synthetic"' in controller
    publisher = read(HARNESS / "src/main/java/com/novapay/paysphere/observe/MetricsPublisher.java")
    assert '"region"' in publisher and '"journey"' in publisher
    # Synthetic traffic is counted separately, never in the business counters.
    assert "synthetic_requests_total" in publisher
    service = read(HARNESS / "src/main/java/com/novapay/paysphere/payments/PaymentService.java")
    assert 'metrics.increment("synthetic_requests_total", outcomeLabels, 1);' in service


@pytest.mark.req("REQ-038")
def test_the_traffic_generator_is_seeded_and_covers_both_regions():
    source = read(HARNESS / "src/main/java/com/novapay/paysphere/trafficgen/TrafficGenerator.java")
    assert "@Scheduled(fixedDelayString" in source
    assert "new Random(properties.getTrafficgen().getSeed()" in source
    assert "regions.get(random.nextInt(regions.size()))" in source


# --- the deployment pipeline -------------------------------------------------


@pytest.mark.req("REQ-038", "REQ-032")
def test_the_deploy_script_speaks_only_the_frozen_ingest_contract():
    script = read(DEPLOY_SCRIPT)
    assert INGEST_PATHS["changes"] in script
    for header in (HDR_TIMESTAMP, HDR_SIGNATURE, HDR_KEY_ID):
        assert header in script, f"{header} is not sent by the pipeline"
    # Signed over "<timestamp>.<raw body>", streamed rather than re-serialised.
    assert """{ printf '%s.' "$ts"; cat "$body_file"; } | openssl dgst -sha256 -hmac""" in script
    # No other SignalForge surface is reachable from the pipeline: the platform
    # is told things, never asked for them and never given a way in.
    other_paths = [path for key, path in INGEST_PATHS.items() if key != "changes"]
    for path in other_paths:
        assert path not in script
    for reachable in ("/apps", "/connectors", "/ingest-keys", "/events"):
        assert f'"{reachable}' not in script


@pytest.mark.req("REQ-038", "REQ-032")
def test_a_re_run_of_the_pipeline_reuses_the_same_event_ids():
    """The event_id is derived from (release, region, status), so re-triggering a
    pipeline is a duplicate the platform acknowledges rather than a second
    deployment in the timeline - which matters because "what changed just before
    this incident" is answered from exactly that timeline."""
    first = _dry_run("region-b")
    second = _dry_run("region-b")
    assert first == second, "the pipeline invented a new event_id for the same deployment"

    other_region = _dry_run("region-a")
    assert other_region["started"] != first["started"], (
        "two regions of the same release share an event_id, so one would be swallowed"
    )
    assert first["started"] != first["completed"], (
        "started and completed share an event_id, so the completion would be a duplicate"
    )
    for value in first.values():
        assert re.fullmatch(
            r"[0-9a-f]{8}-[0-9a-f]{4}-5[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}", value
        ), f"{value} is not a well-formed UUID the ingest contract will accept"


def _dry_run(region: str) -> dict[str, str]:
    result = subprocess.run(
        ["bash", str(DEPLOY_SCRIPT), "--region", region, "--dry-run"],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    ids = dict(
        re.findall(r"event_id\((started|completed)\)\s*=\s*([0-9a-f-]{36})", result.stdout)
    )
    assert set(ids) == {"started", "completed"}, result.stdout
    return ids


# --- the app-agnostic core ---------------------------------------------------


@pytest.mark.req("REQ-038")
def test_the_core_still_names_no_application_with_the_harness_present():
    """Half of REQ-038's acceptance. The harness exists, is onboarded, and is
    still invisible to every line of product code."""
    result = subprocess.run(
        ["bash", str(ROOT / "scripts" / "check_clean.sh")],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.req("REQ-038")
def test_the_harness_imports_nothing_from_the_platform():
    """Wired only via public contracts: no dependency, no generated client, no
    shared package. Naming the platform in a comment is fine and honest; taking
    a build-time dependency on it would make the onboarding claim worthless.
    """
    java_files = sorted(HARNESS.rglob("*.java"))
    assert java_files, "the harness has no sources"
    for path in java_files:
        for line in read(path).splitlines():
            stripped = line.strip()
            if stripped.startswith(("import ", "package ")):
                assert "signalforge" not in stripped.lower(), f"{path}: {stripped}"
    pom = read(HARNESS / "pom.xml")
    assert not re.search(r"<(groupId|artifactId)>[^<]*signalforge", pom, re.I)
