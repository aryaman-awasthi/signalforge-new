"""The readiness score (REQ-036).

A percentage is only useful if it is honest, so three rules apply:

1. **Every point is a check somebody can act on.** Each check names the wizard
   step that fixes it and says, in one sentence, what is missing. A score with no
   remediation is decoration.
2. **The weights are fixed and sum to 100**, asserted by a test. A score that
   drifts as checks are added is not comparable between two applications.
3. **Some checks are blocking.** An application can reach 79 percent on optional
   polish while having no connector at all; `ready` is false unless every
   blocking check passes *and* the score clears the threshold, so a high number
   can never mean "watchable" while a blocking check is red.

Pure: the application layer gathers the facts, this decides what they are worth.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from signalforge.domain.onboarding.profile import WizardStep

READY_THRESHOLD = 80
"""Below this an application is onboarded but not yet trustworthy to watch."""


@dataclass(frozen=True)
class ReadinessInputs:
    """Everything the score is computed from. Gathered by the application layer
    so this module stays pure and testable without a database."""

    base_url_set: bool = False
    connectors_enabled: int = 0
    connectors_healthy: int = 0
    connectors_failing: int = 0
    ingest_keys: int = 0
    signals: int = 0
    business_signals: int = 0
    can_corroborate: bool = False
    region_label_set: bool = False
    journeys: int = 0
    probes: int = 0
    actions_imported: int = 0
    topology_version: Optional[str] = None
    slos: int = 0
    severity_configured: bool = False
    baselines: int = 0
    last_test_all_green: bool = False


@dataclass(frozen=True)
class ReadinessCheck:
    key: str
    step: WizardStep
    label: str
    weight: int
    passed: bool
    detail: str
    blocking: bool = False


@dataclass(frozen=True)
class Readiness:
    checks: tuple[ReadinessCheck, ...]

    @property
    def score(self) -> int:
        return sum(check.weight for check in self.checks if check.passed)

    @property
    def total_weight(self) -> int:
        return sum(check.weight for check in self.checks)

    @property
    def blocking_failures(self) -> tuple[ReadinessCheck, ...]:
        return tuple(
            check for check in self.checks if check.blocking and not check.passed
        )

    @property
    def ready(self) -> bool:
        return not self.blocking_failures and self.score >= READY_THRESHOLD

    @property
    def outstanding(self) -> tuple[ReadinessCheck, ...]:
        """What to do next, worst first: blocking checks, then by lost points."""
        return tuple(
            sorted(
                (check for check in self.checks if not check.passed),
                key=lambda check: (not check.blocking, -check.weight, check.key),
            )
        )

    def step_score(self, step: WizardStep) -> tuple[int, int]:
        """(earned, available) for one wizard step, so the stepper can show a
        per-step state rather than only a total."""
        checks = [check for check in self.checks if check.step is step]
        return (
            sum(check.weight for check in checks if check.passed),
            sum(check.weight for check in checks),
        )


def evaluate(inputs: ReadinessInputs) -> Readiness:
    checks = (
        ReadinessCheck(
            key="basics.base_url",
            step=WizardStep.BASICS,
            label="Application base URL",
            weight=10,
            passed=inputs.base_url_set,
            detail=(
                "set"
                if inputs.base_url_set
                else "no base URL: auto-discovery and journey probes have nothing "
                "to resolve against"
            ),
            blocking=True,
        ),
        ReadinessCheck(
            key="connect.bound",
            step=WizardStep.CONNECT,
            label="At least one integration",
            weight=10,
            passed=(inputs.connectors_enabled + inputs.ingest_keys) > 0,
            detail=(
                f"{inputs.connectors_enabled} connector(s), {inputs.ingest_keys} "
                "ingest key(s)"
            ),
            blocking=True,
        ),
        ReadinessCheck(
            key="connect.healthy",
            step=WizardStep.CONNECT,
            label="Integrations answering",
            weight=10,
            passed=inputs.connectors_healthy > 0 and inputs.connectors_failing == 0,
            detail=(
                f"{inputs.connectors_healthy} up, {inputs.connectors_failing} "
                "not answering"
            ),
            # Blocking: an application whose integrations are not answering is
            # not watchable, however much of the wizard has been filled in. A
            # score above the threshold must never read "ready" over a red
            # connector.
            blocking=True,
        ),
        ReadinessCheck(
            key="map.signals",
            step=WizardStep.MAP,
            label="Golden signals mapped",
            weight=10,
            passed=inputs.signals > 0,
            detail=f"{inputs.signals} signal(s) mapped",
            blocking=True,
        ),
        ReadinessCheck(
            key="map.business",
            step=WizardStep.MAP,
            label="Business counters mapped",
            weight=5,
            passed=inputs.business_signals > 0,
            detail=(
                f"{inputs.business_signals} business signal(s)"
                if inputs.business_signals
                else "no business counter: customer impact will be inferred from "
                "infrastructure only"
            ),
        ),
        ReadinessCheck(
            key="map.region_label",
            step=WizardStep.MAP,
            label="Region label selected",
            weight=5,
            passed=inputs.region_label_set,
            detail=(
                "set"
                if inputs.region_label_set
                else "no region label: per-region health cannot be separated"
            ),
        ),
        ReadinessCheck(
            key="map.journeys",
            step=WizardStep.MAP,
            label="Journeys with probes",
            weight=10,
            passed=inputs.journeys > 0 and inputs.probes > 0,
            detail=f"{inputs.journeys} journey(s), {inputs.probes} probe template(s)",
        ),
        ReadinessCheck(
            key="map.actions",
            step=WizardStep.MAP,
            label="Actions imported",
            weight=5,
            passed=inputs.actions_imported > 0,
            detail=f"{inputs.actions_imported} action(s) imported from the manifest",
        ),
        ReadinessCheck(
            key="topology.version",
            step=WizardStep.TOPOLOGY,
            label="Topology imported",
            weight=10,
            passed=bool(inputs.topology_version),
            detail=(
                f"version {inputs.topology_version}"
                if inputs.topology_version
                else "no topology: blast radius cannot be computed"
            ),
        ),
        ReadinessCheck(
            key="slos.targets",
            step=WizardStep.SLOS,
            label="SLO targets",
            weight=10,
            passed=inputs.slos > 0,
            detail=f"{inputs.slos} SLO(s)",
            blocking=True,
        ),
        ReadinessCheck(
            key="slos.severity",
            step=WizardStep.SLOS,
            label="Severity matrix",
            weight=5,
            passed=inputs.severity_configured,
            detail=(
                "configured" if inputs.severity_configured else "not configured"
            ),
        ),
        ReadinessCheck(
            key="baseline.learned",
            step=WizardStep.BASELINE,
            label="Baselines learned",
            weight=5,
            passed=inputs.baselines > 0,
            detail=(
                f"{inputs.baselines} baseline(s)"
                if inputs.baselines
                else "no baseline: anomalies have nothing to be anomalous against"
            ),
        ),
        ReadinessCheck(
            key="test.green",
            step=WizardStep.TEST,
            label="End-to-end test passed",
            weight=5,
            passed=inputs.last_test_all_green,
            detail=(
                "last run all green"
                if inputs.last_test_all_green
                else "the test page has not passed yet"
            ),
        ),
    )
    return Readiness(checks=checks)
