"""Wire shapes for the onboarding wizard (REQ-036).

Output models are built from the same mappings the profile is *stored* as
(`domain/onboarding/codec.py`), so what the wizard renders and what the platform
holds cannot drift. Input models are separate and deliberately partial: every
step saves only its own section, and a field left out means "unchanged" rather
than "clear it".

No credential value appears anywhere in this module. The connection test reports
whether a credential worked, never what it was.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field

from signalforge.application.onboarding.apps import ApplicationView
from signalforge.application.onboarding.discovery import (
    DiscoveryCandidate,
    DiscoveryFinding,
    DiscoveryReport,
)
from signalforge.application.onboarding.testing import (
    ConnectionTestResult,
    ProbeRun,
    TestPageReport,
)
from signalforge.application.onboarding.wizard import ActionImport, BaselineRun
from signalforge.contracts.canonical import ActionClass, RiskLevel, TopologySnapshot
from signalforge.contracts.roles import Role
from signalforge.domain.onboarding.applications import Application, AppTier, Environment
from signalforge.domain.onboarding.codec import (
    action_to_mapping,
    baseline_to_mapping,
    journey_to_mapping,
    policy_to_mapping,
    probe_to_mapping,
    severity_matrix_to_mapping,
    signal_to_mapping,
    slo_to_mapping,
    test_summary_to_mapping,
    topology_to_mapping,
)
from signalforge.domain.onboarding.ports import TopologyVersion
from signalforge.domain.onboarding.profile import (
    ExecutionMode,
    GoldenSignal,
    OnboardingProfile,
    Severity,
    SignalDirection,
    WizardStep,
)
from signalforge.domain.onboarding.readiness import READY_THRESHOLD, Readiness

# --- inputs -------------------------------------------------------------------


class ApplicationIn(BaseModel):
    app_id: str = Field(min_length=3, max_length=64)
    name: str = Field(min_length=1, max_length=80)
    tier: AppTier = AppTier.TIER_2
    environment: Environment = Environment.PRODUCTION
    base_url: Optional[str] = Field(
        default=None,
        description="Root URL of the application. Auto-discovery walks it for "
        "the /observe/* contract and it pre-fills every adapter config that "
        "declares a base_url.",
    )
    description: str = ""
    owner_team: str = ""


class ApplicationPatchIn(BaseModel):
    name: Optional[str] = Field(default=None, max_length=80)
    tier: Optional[AppTier] = None
    environment: Optional[Environment] = None
    base_url: Optional[str] = None
    description: Optional[str] = None
    owner_team: Optional[str] = None
    clear_base_url: bool = False


class SignalIn(BaseModel):
    key: str
    signal: GoldenSignal
    selector: str
    direction: SignalDirection
    unit: str = ""
    description: str = ""
    business_counter: bool = False


class JourneyIn(BaseModel):
    journey_id: str
    name: str
    tier: int = 2
    description: str = ""


class ProbeIn(BaseModel):
    probe_id: str
    path: str
    method: str = "GET"
    expected_status: int = 200
    timeout_s: float = 5.0
    journey_id: Optional[str] = None
    per_region: bool = True
    synthetic_header: str = "X-SignalForge-Synthetic"


class MappingIn(BaseModel):
    signals: Optional[list[SignalIn]] = None
    region_label: Optional[str] = None
    regions: Optional[list[str]] = None
    journeys: Optional[list[JourneyIn]] = None
    probes: Optional[list[ProbeIn]] = None
    clear_region_label: bool = False


class ActionsImportIn(BaseModel):
    binding_id: Optional[str] = None


class ActionPatchIn(BaseModel):
    risk_override: Optional[RiskLevel] = None
    clear_risk_override: bool = False
    approver_roles: Optional[list[Role]] = None
    execution_mode: Optional[ExecutionMode] = None
    enabled: Optional[bool] = None


class TopologyIn(BaseModel):
    document: dict[str, Any]
    binding_id: Optional[str] = None


class SloIn(BaseModel):
    slo_id: str
    name: str
    objective_pct: float
    window_days: int = 30
    journey_id: Optional[str] = None
    indicator: str = ""
    fast_burn_multiplier: float = 14.4
    slow_burn_multiplier: float = 6.0


class SeverityRuleIn(BaseModel):
    severity: Severity
    max_journey_tier: Optional[int] = None
    min_regions_affected: Optional[int] = None
    success_rate_below_pct: Optional[float] = None
    customer_impacting: Optional[bool] = None
    description: str = ""


class SeverityMatrixIn(BaseModel):
    rules: list[SeverityRuleIn]
    default: Severity = Severity.P3


class PolicyIn(BaseModel):
    """Partial by design: the floor is enforced on the *merged* policy, so a
    field left out keeps its current value rather than silently resetting it."""

    approval_ttl_s: Optional[int] = None
    stability_window_s: Optional[int] = None
    max_action_retries: Optional[int] = None
    auto_execute_low_risk: Optional[bool] = None
    require_approval_classes: Optional[list[ActionClass]] = None
    confidence_threshold: Optional[float] = None
    revalidation_cycles_before_escalation: Optional[int] = None


class GovernanceIn(BaseModel):
    slos: Optional[list[SloIn]] = None
    severity_matrix: Optional[SeverityMatrixIn] = None
    policy: Optional[PolicyIn] = None


class BaselineIn(BaseModel):
    samples: int = 5
    binding_id: Optional[str] = None


class ConnectionTestIn(BaseModel):
    binding_id: Optional[str] = None


class DiscoverIn(BaseModel):
    """No fields today; a body is required so the route is a POST that can grow
    options (a different base URL to probe, a narrower target list) without
    becoming a different route."""


# --- outputs ------------------------------------------------------------------


class SignalOut(BaseModel):
    key: str
    signal: str
    selector: str
    direction: str
    unit: str
    description: str
    business_counter: bool


class JourneyOut(BaseModel):
    journey_id: str
    name: str
    tier: int
    description: str


class ProbeOut(BaseModel):
    probe_id: str
    path: str
    method: str
    expected_status: int
    timeout_s: float
    journey_id: Optional[str] = None
    per_region: bool
    synthetic_header: str


class ActionOut(BaseModel):
    action_id: str
    name: str
    action_class: str
    manifest_risk: str
    risk_override: Optional[str] = None
    approver_roles: list[str]
    execution_mode: str
    reversible: bool
    reverse_action_id: Optional[str] = None
    idempotent: bool
    enabled: bool
    description: str
    imported_at: Optional[datetime] = None
    high_risk: bool
    effective_risk: str


class SloOut(BaseModel):
    slo_id: str
    name: str
    objective_pct: float
    window_days: int
    journey_id: Optional[str] = None
    indicator: str
    fast_burn_multiplier: float
    slow_burn_multiplier: float
    error_budget_pct: float


class SeverityRuleOut(BaseModel):
    severity: str
    max_journey_tier: Optional[int] = None
    min_regions_affected: Optional[int] = None
    success_rate_below_pct: Optional[float] = None
    customer_impacting: Optional[bool] = None
    description: str


class SeverityMatrixOut(BaseModel):
    rules: list[SeverityRuleOut]
    default: str


class PolicyOut(BaseModel):
    approval_ttl_s: int
    stability_window_s: int
    max_action_retries: int
    auto_execute_low_risk: bool
    require_approval_classes: list[str]
    confidence_threshold: float
    revalidation_cycles_before_escalation: int


class BaselineOut(BaseModel):
    signal_key: str
    samples: int
    window_s: float
    mean: float
    stddev: float
    minimum: float
    maximum: float
    p95: float
    series_count: int
    learned_at: Optional[datetime] = None
    source: str


class TopologyRefOut(BaseModel):
    version: str
    captured_at: Optional[datetime] = None
    node_count: int
    edge_count: int
    imported_at: Optional[datetime] = None
    normalized_by: str
    source: str


class TestSummaryOut(BaseModel):
    ran_at: Optional[datetime] = None
    passed: int
    failed: int
    pending: int
    skipped: int


class ProfileOut(BaseModel):
    signals: list[SignalOut]
    region_label: Optional[str] = None
    regions: list[str]
    journeys: list[JourneyOut]
    probes: list[ProbeOut]
    actions: list[ActionOut]
    slos: list[SloOut]
    severity_matrix: SeverityMatrixOut
    policy: PolicyOut
    baselines: list[BaselineOut]
    topology: Optional[TopologyRefOut] = None
    last_test: Optional[TestSummaryOut] = None
    can_corroborate: bool
    updated_at: Optional[datetime] = None


class ApplicationOut(BaseModel):
    app_id: str
    name: str
    tier: str
    environment: str
    base_url: Optional[str] = None
    description: str
    owner_team: str
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class ReadinessCheckOut(BaseModel):
    key: str
    step: str
    label: str
    weight: int
    passed: bool
    detail: str
    blocking: bool


class ReadinessOut(BaseModel):
    app_id: str
    score: int
    total_weight: int
    threshold: int
    ready: bool
    checks: list[ReadinessCheckOut]
    outstanding: list[str]
    steps: dict[str, list[int]]


class ApplicationSummaryOut(ApplicationOut):
    readiness_score: int
    ready: bool


class ApplicationDetailOut(BaseModel):
    application: ApplicationOut
    profile: ProfileOut
    readiness: ReadinessOut


class DiscoveryCandidateOut(BaseModel):
    adapter_id: str
    adapter_name: str
    kind: str
    mode: str
    direction: str
    availability: str
    runnable: bool
    simulated: bool
    credential_scope: str
    credential_fields: list[str]
    suggested_config: dict[str, Any]
    action: str
    already_bound: bool
    reason: str
    stream: Optional[str] = None
    ingest_path: Optional[str] = None


class DiscoveryFindingOut(BaseModel):
    key: str
    source: str
    label: str
    url: str
    present: bool
    guarded: bool
    status_code: Optional[int] = None
    latency_ms: float
    detail: str
    candidates: list[DiscoveryCandidateOut]


class DiscoveryOut(BaseModel):
    app_id: str
    base_url: Optional[str] = None
    probed: int
    found: int
    findings: list[DiscoveryFindingOut]


class ConnectionCheckOut(BaseModel):
    name: str
    outcome: str
    detail: str
    latency_ms: float


class ConnectionTestOut(BaseModel):
    binding_id: str
    adapter_id: str
    adapter_name: str
    kind: Optional[str] = None
    mode: Optional[str] = None
    direction: Optional[str] = None
    credential_scope: str
    simulated: bool
    outcome: str
    detail: str
    latency_ms: float
    checks: list[ConnectionCheckOut]


class ActionsImportOut(BaseModel):
    app_id: str
    adapter_id: str
    binding_id: str
    imported: int
    refreshed: int
    withdrawn: int
    actions: list[ActionOut]


class TopologyUploadOut(BaseModel):
    app_id: str
    version: str
    unchanged: bool
    node_count: int
    edge_count: int
    normalized_by: str
    imported_at: Optional[datetime] = None


class TopologyVersionOut(BaseModel):
    version: str
    node_count: int
    edge_count: int
    imported_at: Optional[datetime] = None
    source: str
    normalized_by: str


class TopologyOut(BaseModel):
    app_id: str
    version: Optional[str] = None
    snapshot: Optional[TopologySnapshot] = None
    versions: list[TopologyVersionOut]


class BaselineMissOut(BaseModel):
    signal_key: str
    reason: str


class BaselineRunOut(BaseModel):
    app_id: str
    binding_id: str
    samples: int
    window_s: float
    learned: list[BaselineOut]
    missed: list[BaselineMissOut]


class ProbeRunOut(BaseModel):
    probe_id: str
    journey_id: Optional[str] = None
    target: str
    outcome: str
    status_code: Optional[int] = None
    latency_ms: float
    detail: str


class TestPageOut(BaseModel):
    app_id: str
    summary: TestSummaryOut
    connectors: list[ConnectionTestOut]
    probes: list[ProbeRunOut]
    readiness: ReadinessOut


# --- mappers ------------------------------------------------------------------


def application_out(application: Application) -> ApplicationOut:
    return ApplicationOut(
        app_id=application.app_id,
        name=application.name,
        tier=application.tier.value,
        environment=application.environment.value,
        base_url=application.base_url,
        description=application.description,
        owner_team=application.owner_team,
        created_at=application.created_at,
        updated_at=application.updated_at,
    )


def action_out(action) -> ActionOut:
    return ActionOut(
        **action_to_mapping(action),
        high_risk=action.high_risk,
        effective_risk=action.effective_risk.value,
    )


def profile_out(profile: OnboardingProfile) -> ProfileOut:
    return ProfileOut(
        signals=[SignalOut(**signal_to_mapping(item)) for item in profile.signals],
        region_label=profile.region_label,
        regions=list(profile.regions),
        journeys=[JourneyOut(**journey_to_mapping(item)) for item in profile.journeys],
        probes=[ProbeOut(**probe_to_mapping(item)) for item in profile.probes],
        actions=[action_out(item) for item in profile.actions],
        slos=[
            SloOut(**slo_to_mapping(item), error_budget_pct=item.error_budget_pct)
            for item in profile.slos
        ],
        severity_matrix=SeverityMatrixOut(
            **severity_matrix_to_mapping(profile.severity_matrix)
        ),
        policy=PolicyOut(**policy_to_mapping(profile.policy)),
        baselines=[
            BaselineOut(**baseline_to_mapping(item)) for item in profile.baselines
        ],
        topology=(
            TopologyRefOut(**topology_to_mapping(profile.topology))
            if profile.topology
            else None
        ),
        last_test=(
            TestSummaryOut(**test_summary_to_mapping(profile.last_test))
            if profile.last_test
            else None
        ),
        can_corroborate=profile.can_corroborate,
        updated_at=profile.updated_at,
    )


def readiness_out(app_id: str, readiness: Readiness) -> ReadinessOut:
    return ReadinessOut(
        app_id=app_id,
        score=readiness.score,
        total_weight=readiness.total_weight,
        threshold=READY_THRESHOLD,
        ready=readiness.ready,
        checks=[
            ReadinessCheckOut(
                key=check.key,
                step=check.step.value,
                label=check.label,
                weight=check.weight,
                passed=check.passed,
                detail=check.detail,
                blocking=check.blocking,
            )
            for check in readiness.checks
        ],
        outstanding=[check.key for check in readiness.outstanding],
        steps={
            step.value: list(readiness.step_score(step)) for step in WizardStep
        },
    )


def application_detail_out(
    view: ApplicationView, readiness: Readiness
) -> ApplicationDetailOut:
    return ApplicationDetailOut(
        application=application_out(view.application),
        profile=profile_out(view.profile),
        readiness=readiness_out(view.application.app_id, readiness),
    )


def application_summary_out(
    view: ApplicationView, readiness: Readiness
) -> ApplicationSummaryOut:
    return ApplicationSummaryOut(
        **application_out(view.application).model_dump(),
        readiness_score=readiness.score,
        ready=readiness.ready,
    )


def discovery_candidate_out(candidate: DiscoveryCandidate) -> DiscoveryCandidateOut:
    return DiscoveryCandidateOut(
        adapter_id=candidate.adapter_id,
        adapter_name=candidate.adapter_name,
        kind=candidate.kind,
        mode=candidate.mode,
        direction=candidate.direction,
        availability=candidate.availability,
        runnable=candidate.runnable,
        simulated=candidate.simulated,
        credential_scope=candidate.credential_scope,
        credential_fields=list(candidate.credential_fields),
        suggested_config=dict(candidate.suggested_config),
        action=candidate.action,
        already_bound=candidate.already_bound,
        reason=candidate.reason,
        stream=candidate.stream,
        ingest_path=candidate.ingest_path,
    )


def discovery_finding_out(finding: DiscoveryFinding) -> DiscoveryFindingOut:
    return DiscoveryFindingOut(
        key=finding.key,
        source=finding.source,
        label=finding.label,
        url=finding.url,
        present=finding.present,
        guarded=finding.guarded,
        status_code=finding.status_code,
        latency_ms=finding.latency_ms,
        detail=finding.detail,
        candidates=[
            discovery_candidate_out(candidate) for candidate in finding.candidates
        ],
    )


def discovery_out(report: DiscoveryReport) -> DiscoveryOut:
    return DiscoveryOut(
        app_id=report.app_id,
        base_url=report.base_url,
        probed=len(report.findings),
        found=report.endpoints_found,
        findings=[discovery_finding_out(finding) for finding in report.findings],
    )


def connection_test_out(result: ConnectionTestResult) -> ConnectionTestOut:
    return ConnectionTestOut(
        binding_id=result.binding_id,
        adapter_id=result.adapter_id,
        adapter_name=result.adapter_name,
        kind=result.kind,
        mode=result.mode,
        direction=result.direction,
        credential_scope=result.credential_scope,
        simulated=result.simulated,
        outcome=result.outcome.value,
        detail=result.detail,
        latency_ms=result.latency_ms,
        checks=[
            ConnectionCheckOut(
                name=check.name,
                outcome=check.outcome.value,
                detail=check.detail,
                latency_ms=check.latency_ms,
            )
            for check in result.checks
        ],
    )


def actions_import_out(app_id: str, result: ActionImport) -> ActionsImportOut:
    return ActionsImportOut(
        app_id=app_id,
        adapter_id=result.adapter_id,
        binding_id=result.binding_id,
        imported=result.imported,
        refreshed=result.updated,
        withdrawn=result.removed,
        actions=[action_out(action) for action in result.profile.actions],
    )


def topology_version_out(version: TopologyVersion) -> TopologyVersionOut:
    return TopologyVersionOut(
        version=version.version,
        node_count=version.node_count,
        edge_count=version.edge_count,
        imported_at=version.imported_at,
        source=version.source,
        normalized_by=version.normalized_by,
    )


def baseline_run_out(app_id: str, run: BaselineRun) -> BaselineRunOut:
    return BaselineRunOut(
        app_id=app_id,
        binding_id=run.binding_id,
        samples=run.samples,
        window_s=run.window_s,
        learned=[BaselineOut(**baseline_to_mapping(item)) for item in run.learned],
        missed=[
            BaselineMissOut(signal_key=miss.signal_key, reason=miss.reason)
            for miss in run.missed
        ],
    )


def probe_run_out(run: ProbeRun) -> ProbeRunOut:
    return ProbeRunOut(
        probe_id=run.probe_id,
        journey_id=run.journey_id,
        target=run.target,
        outcome=run.outcome.value,
        status_code=run.status_code,
        latency_ms=run.latency_ms,
        detail=run.detail,
    )


def test_page_out(report: TestPageReport) -> TestPageOut:
    return TestPageOut(
        app_id=report.app_id,
        summary=TestSummaryOut(**test_summary_to_mapping(report.summary)),
        connectors=[connection_test_out(result) for result in report.connectors],
        probes=[probe_run_out(run) for run in report.probes],
        readiness=readiness_out(report.app_id, report.readiness),
    )
