"""Onboarding profile <-> plain mappings.

In the domain for the same reason `manifest_from_mapping` is: the *rules* about
what a profile may contain belong here, so anything that reads a profile back -
the SQL row, a seed file, a future export - reconstructs it through the same
constructors and therefore through the same validation. A row written by an
older build that would violate today's floor fails to load rather than loading
as something the floor would have refused.

Nothing here does I/O and nothing here knows about SQL or HTTP.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping, Optional, Sequence

from signalforge.contracts.canonical import ActionClass, RiskLevel
from signalforge.contracts.roles import Role
from signalforge.domain.onboarding.errors import OnboardingValidationError
from signalforge.domain.onboarding.profile import (
    Baseline,
    ExecutionMode,
    GoldenSignal,
    Journey,
    MappedAction,
    OnboardingProfile,
    PolicyDefaults,
    ProbeTemplate,
    Severity,
    SeverityMatrix,
    SeverityRule,
    SignalDirection,
    SignalMapping,
    SloTarget,
    TestSummary,
    TopologyRef,
)


def _dt(value: Any) -> Optional[datetime]:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError as exc:
        raise OnboardingValidationError(f"{value!r} is not an ISO timestamp") from exc
    if parsed.tzinfo is None:
        raise OnboardingValidationError("timestamps must be timezone-aware")
    return parsed


def _iso(value: Optional[datetime]) -> Optional[str]:
    return value.isoformat() if value is not None else None


def _enum(cls, value: Any, what: str):
    try:
        return cls(value)
    except ValueError as exc:
        raise OnboardingValidationError(
            f"{value!r} is not a valid {what}; allowed values are "
            f"{[member.value for member in cls]}"
        ) from exc


# --- signals, journeys, probes ----------------------------------------------


def signal_to_mapping(signal: SignalMapping) -> dict[str, Any]:
    return {
        "key": signal.key,
        "signal": signal.signal.value,
        "selector": signal.selector,
        "direction": signal.direction.value,
        "unit": signal.unit,
        "description": signal.description,
        "business_counter": signal.business_counter,
    }


def signal_from_mapping(raw: Mapping[str, Any]) -> SignalMapping:
    return SignalMapping(
        key=str(raw.get("key", "")),
        signal=_enum(GoldenSignal, raw.get("signal"), "golden signal"),
        selector=str(raw.get("selector", "")),
        direction=_enum(SignalDirection, raw.get("direction"), "signal direction"),
        unit=str(raw.get("unit", "") or ""),
        description=str(raw.get("description", "") or ""),
        business_counter=bool(raw.get("business_counter", False)),
    )


def journey_to_mapping(journey: Journey) -> dict[str, Any]:
    return {
        "journey_id": journey.journey_id,
        "name": journey.name,
        "tier": journey.tier,
        "description": journey.description,
    }


def journey_from_mapping(raw: Mapping[str, Any]) -> Journey:
    return Journey(
        journey_id=str(raw.get("journey_id", "")),
        name=str(raw.get("name", "")),
        tier=int(raw.get("tier", 2)),
        description=str(raw.get("description", "") or ""),
    )


def probe_to_mapping(probe: ProbeTemplate) -> dict[str, Any]:
    return {
        "probe_id": probe.probe_id,
        "path": probe.path,
        "method": probe.method,
        "expected_status": probe.expected_status,
        "timeout_s": probe.timeout_s,
        "journey_id": probe.journey_id,
        "per_region": probe.per_region,
        "synthetic_header": probe.synthetic_header,
    }


def probe_from_mapping(raw: Mapping[str, Any]) -> ProbeTemplate:
    return ProbeTemplate(
        probe_id=str(raw.get("probe_id", "")),
        path=str(raw.get("path", "")),
        method=str(raw.get("method", "GET") or "GET"),
        expected_status=int(raw.get("expected_status", 200)),
        timeout_s=float(raw.get("timeout_s", 5.0)),
        journey_id=(
            str(raw["journey_id"]) if raw.get("journey_id") not in (None, "") else None
        ),
        per_region=bool(raw.get("per_region", True)),
        synthetic_header=str(
            raw.get("synthetic_header") or "X-SignalForge-Synthetic"
        ),
    )


# --- actions ------------------------------------------------------------------


def action_to_mapping(action: MappedAction) -> dict[str, Any]:
    return {
        "action_id": action.action_id,
        "name": action.name,
        "action_class": action.action_class.value,
        "manifest_risk": action.manifest_risk.value,
        "risk_override": (
            action.risk_override.value if action.risk_override else None
        ),
        "approver_roles": [role.value for role in action.approver_roles],
        "execution_mode": action.execution_mode.value,
        "reversible": action.reversible,
        "reverse_action_id": action.reverse_action_id,
        "idempotent": action.idempotent,
        "enabled": action.enabled,
        "description": action.description,
        "params_schema": dict(action.params_schema),
        "verification": list(action.verification),
        "preconditions": list(action.preconditions),
        "imported_at": _iso(action.imported_at),
    }


def action_from_mapping(raw: Mapping[str, Any]) -> MappedAction:
    return MappedAction(
        action_id=str(raw.get("action_id", "")),
        name=str(raw.get("name", "")),
        action_class=_enum(ActionClass, raw.get("action_class"), "action class"),
        manifest_risk=_enum(RiskLevel, raw.get("manifest_risk"), "risk level"),
        risk_override=(
            _enum(RiskLevel, raw["risk_override"], "risk level")
            if raw.get("risk_override")
            else None
        ),
        approver_roles=tuple(
            _enum(Role, value, "role") for value in raw.get("approver_roles") or ()
        ),
        execution_mode=_enum(
            ExecutionMode, raw.get("execution_mode", "manual"), "execution mode"
        ),
        reversible=bool(raw.get("reversible", False)),
        reverse_action_id=raw.get("reverse_action_id") or None,
        idempotent=bool(raw.get("idempotent", False)),
        enabled=bool(raw.get("enabled", True)),
        description=str(raw.get("description", "") or ""),
        # Both default empty so a profile stored before S6 still loads.
        params_schema=dict(raw.get("params_schema") or {}),
        verification=tuple(raw.get("verification") or ()),
        preconditions=tuple(raw.get("preconditions") or ()),
        imported_at=_dt(raw.get("imported_at")),
    )


# --- SLOs, severity, policy ---------------------------------------------------


def slo_to_mapping(slo: SloTarget) -> dict[str, Any]:
    return {
        "slo_id": slo.slo_id,
        "name": slo.name,
        "objective_pct": slo.objective_pct,
        "window_days": slo.window_days,
        "journey_id": slo.journey_id,
        "indicator": slo.indicator,
        "fast_burn_multiplier": slo.fast_burn_multiplier,
        "slow_burn_multiplier": slo.slow_burn_multiplier,
    }


def slo_from_mapping(raw: Mapping[str, Any]) -> SloTarget:
    return SloTarget(
        slo_id=str(raw.get("slo_id", "")),
        name=str(raw.get("name", "")),
        objective_pct=float(raw.get("objective_pct", 0)),
        window_days=int(raw.get("window_days", 30)),
        journey_id=(
            str(raw["journey_id"]) if raw.get("journey_id") not in (None, "") else None
        ),
        indicator=str(raw.get("indicator", "") or ""),
        fast_burn_multiplier=float(raw.get("fast_burn_multiplier", 14.4)),
        slow_burn_multiplier=float(raw.get("slow_burn_multiplier", 6.0)),
    )


def severity_rule_to_mapping(rule: SeverityRule) -> dict[str, Any]:
    return {
        "severity": rule.severity.value,
        "max_journey_tier": rule.max_journey_tier,
        "min_regions_affected": rule.min_regions_affected,
        "success_rate_below_pct": rule.success_rate_below_pct,
        "customer_impacting": rule.customer_impacting,
        "description": rule.description,
    }


def severity_rule_from_mapping(raw: Mapping[str, Any]) -> SeverityRule:
    return SeverityRule(
        severity=_enum(Severity, raw.get("severity"), "severity"),
        max_journey_tier=(
            int(raw["max_journey_tier"])
            if raw.get("max_journey_tier") is not None
            else None
        ),
        min_regions_affected=(
            int(raw["min_regions_affected"])
            if raw.get("min_regions_affected") is not None
            else None
        ),
        success_rate_below_pct=(
            float(raw["success_rate_below_pct"])
            if raw.get("success_rate_below_pct") is not None
            else None
        ),
        customer_impacting=(
            bool(raw["customer_impacting"])
            if raw.get("customer_impacting") is not None
            else None
        ),
        description=str(raw.get("description", "") or ""),
    )


def severity_matrix_to_mapping(matrix: SeverityMatrix) -> dict[str, Any]:
    return {
        "rules": [severity_rule_to_mapping(rule) for rule in matrix.rules],
        "default": matrix.default.value,
    }


def severity_matrix_from_mapping(raw: Mapping[str, Any]) -> SeverityMatrix:
    return SeverityMatrix(
        rules=tuple(
            severity_rule_from_mapping(rule) for rule in raw.get("rules") or ()
        ),
        default=_enum(Severity, raw.get("default", Severity.P3.value), "severity"),
    )


def policy_to_mapping(policy: PolicyDefaults) -> dict[str, Any]:
    return {
        "approval_ttl_s": policy.approval_ttl_s,
        "stability_window_s": policy.stability_window_s,
        "max_action_retries": policy.max_action_retries,
        "auto_execute_low_risk": policy.auto_execute_low_risk,
        "require_approval_classes": sorted(
            action_class.value for action_class in policy.require_approval_classes
        ),
        "confidence_threshold": policy.confidence_threshold,
        "revalidation_cycles_before_escalation": (
            policy.revalidation_cycles_before_escalation
        ),
    }


def policy_from_mapping(raw: Mapping[str, Any]) -> PolicyDefaults:
    return PolicyDefaults(
        approval_ttl_s=int(raw.get("approval_ttl_s", 900)),
        stability_window_s=int(raw.get("stability_window_s", 900)),
        max_action_retries=int(raw.get("max_action_retries", 1)),
        auto_execute_low_risk=bool(raw.get("auto_execute_low_risk", False)),
        require_approval_classes=frozenset(
            _enum(ActionClass, value, "action class")
            for value in raw.get("require_approval_classes") or ()
        ),
        confidence_threshold=float(raw.get("confidence_threshold", 0.7)),
        revalidation_cycles_before_escalation=int(
            raw.get("revalidation_cycles_before_escalation", 3)
        ),
    )


# --- baselines, topology, test ------------------------------------------------


def baseline_to_mapping(baseline: Baseline) -> dict[str, Any]:
    return {
        "signal_key": baseline.signal_key,
        "samples": baseline.samples,
        "window_s": baseline.window_s,
        "mean": baseline.mean,
        "stddev": baseline.stddev,
        "minimum": baseline.minimum,
        "maximum": baseline.maximum,
        "p95": baseline.p95,
        "series_count": baseline.series_count,
        "learned_at": _iso(baseline.learned_at),
        "source": baseline.source,
    }


def baseline_from_mapping(raw: Mapping[str, Any]) -> Baseline:
    return Baseline(
        signal_key=str(raw.get("signal_key", "")),
        samples=int(raw.get("samples", 0)),
        window_s=float(raw.get("window_s", 0.0)),
        mean=float(raw.get("mean", 0.0)),
        stddev=float(raw.get("stddev", 0.0)),
        minimum=float(raw.get("minimum", 0.0)),
        maximum=float(raw.get("maximum", 0.0)),
        p95=float(raw.get("p95", 0.0)),
        series_count=int(raw.get("series_count", 0)),
        learned_at=_dt(raw.get("learned_at")),
        source=str(raw.get("source", "") or ""),
    )


def topology_to_mapping(topology: TopologyRef) -> dict[str, Any]:
    return {
        "version": topology.version,
        "captured_at": _iso(topology.captured_at),
        "node_count": topology.node_count,
        "edge_count": topology.edge_count,
        "imported_at": _iso(topology.imported_at),
        "normalized_by": topology.normalized_by,
        "source": topology.source,
    }


def topology_from_mapping(raw: Mapping[str, Any]) -> TopologyRef:
    captured_at = _dt(raw.get("captured_at"))
    if captured_at is None:
        raise OnboardingValidationError("a topology reference needs captured_at")
    return TopologyRef(
        version=str(raw.get("version", "")),
        captured_at=captured_at,
        node_count=int(raw.get("node_count", 0)),
        edge_count=int(raw.get("edge_count", 0)),
        imported_at=_dt(raw.get("imported_at")),
        normalized_by=str(raw.get("normalized_by", "") or ""),
        source=str(raw.get("source", "upload") or "upload"),
    )


def test_summary_to_mapping(summary: TestSummary) -> dict[str, Any]:
    return {
        "ran_at": _iso(summary.ran_at),
        "passed": summary.passed,
        "failed": summary.failed,
        "pending": summary.pending,
        "skipped": summary.skipped,
    }


def test_summary_from_mapping(raw: Mapping[str, Any]) -> TestSummary:
    ran_at = _dt(raw.get("ran_at"))
    if ran_at is None:
        raise OnboardingValidationError("a test summary needs ran_at")
    return TestSummary(
        ran_at=ran_at,
        passed=int(raw.get("passed", 0)),
        failed=int(raw.get("failed", 0)),
        pending=int(raw.get("pending", 0)),
        skipped=int(raw.get("skipped", 0)),
    )


# --- the whole profile --------------------------------------------------------


def profile_to_sections(profile: OnboardingProfile) -> dict[str, Any]:
    return {
        "signals": [signal_to_mapping(item) for item in profile.signals],
        "region_label": profile.region_label,
        "regions": list(profile.regions),
        "journeys": [journey_to_mapping(item) for item in profile.journeys],
        "probes": [probe_to_mapping(item) for item in profile.probes],
        "actions": [action_to_mapping(item) for item in profile.actions],
        "slos": [slo_to_mapping(item) for item in profile.slos],
        "severity_matrix": severity_matrix_to_mapping(profile.severity_matrix),
        "policy": policy_to_mapping(profile.policy),
        "baselines": [baseline_to_mapping(item) for item in profile.baselines],
        "topology": (
            topology_to_mapping(profile.topology) if profile.topology else None
        ),
        "last_test": (
            test_summary_to_mapping(profile.last_test) if profile.last_test else None
        ),
    }


def profile_from_sections(
    tenant_id: str,
    app_id: str,
    sections: Mapping[str, Any],
    *,
    updated_at: Optional[datetime] = None,
) -> OnboardingProfile:
    return OnboardingProfile(
        tenant_id=tenant_id,
        app_id=app_id,
        signals=tuple(
            signal_from_mapping(item) for item in sections.get("signals") or ()
        ),
        region_label=sections.get("region_label") or None,
        regions=tuple(sections.get("regions") or ()),
        journeys=tuple(
            journey_from_mapping(item) for item in sections.get("journeys") or ()
        ),
        probes=tuple(
            probe_from_mapping(item) for item in sections.get("probes") or ()
        ),
        actions=tuple(
            action_from_mapping(item) for item in sections.get("actions") or ()
        ),
        slos=tuple(slo_from_mapping(item) for item in sections.get("slos") or ()),
        severity_matrix=severity_matrix_from_mapping(
            sections.get("severity_matrix") or {}
        ),
        policy=policy_from_mapping(sections.get("policy") or {}),
        baselines=tuple(
            baseline_from_mapping(item) for item in sections.get("baselines") or ()
        ),
        topology=(
            topology_from_mapping(sections["topology"])
            if sections.get("topology")
            else None
        ),
        last_test=(
            test_summary_from_mapping(sections["last_test"])
            if sections.get("last_test")
            else None
        ),
        updated_at=updated_at,
    )


def parse_signals(raw: Sequence[Mapping[str, Any]]) -> tuple[SignalMapping, ...]:
    return tuple(signal_from_mapping(item) for item in raw)


def parse_journeys(raw: Sequence[Mapping[str, Any]]) -> tuple[Journey, ...]:
    return tuple(journey_from_mapping(item) for item in raw)


def parse_probes(raw: Sequence[Mapping[str, Any]]) -> tuple[ProbeTemplate, ...]:
    return tuple(probe_from_mapping(item) for item in raw)


def parse_slos(raw: Sequence[Mapping[str, Any]]) -> tuple[SloTarget, ...]:
    return tuple(slo_from_mapping(item) for item in raw)
