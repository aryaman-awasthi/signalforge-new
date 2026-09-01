"""The twelve final-demonstration checks, verified rather than narrated.

    python scripts/demo_check.py            # run every check
    python scripts/demo_check.py --list     # print the mapping and stop
    python scripts/demo_check.py --only 6   # one check

Each check names the automated tests that prove it. This script runs those tests
and prints PASS or FAIL per check, so "the demo passes" is a command somebody can
run rather than a claim in a document. It is the machine-checkable half of
docs/demo-script.md, which is the half a person performs in the browser.

WHERE THE TWELVE COME FROM
--------------------------
The engineering guide's section 16 is the authority and its text is not in this
repository. The twelve below are derived from the guide's compliance map in
playbook/00-INDEX.md (sections 03, 05, 06, 07, 08, 09+10, 11, 12, 13, 14, 16,
17), together with the two checks the repository names explicitly: the live
traceability challenge (`make evidence REQ-xxx`) and a control shown actively
blocking an unsafe action, which docs/08 records as the twelfth.

That derivation is stated here rather than buried, because it is the one thing in
this sprint a reviewer should check against the guide itself before the
demonstration.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PY = sys.executable


@dataclass(frozen=True)
class Check:
    number: int
    title: str
    guide_section: str
    action: str
    surface: str
    expected: str
    reqs: tuple[str, ...]
    tests: tuple[str, ...]
    fallback: str
    artifacts: tuple[str, ...] = field(default_factory=tuple)


CHECKS: tuple[Check, ...] = (
    Check(
        number=1,
        title="The whole product boots from a clean machine, with no Docker",
        guide_section="17 - end-to-end product demoable",
        action="bash reset.sh && bash run.sh",
        surface="terminal, then http://127.0.0.1:3000 and :8000/health",
        expected=(
            "run.sh reports the harness, the API and the cockpit up, having waited "
            "for each to answer a real request; /health is ok"
        ),
        reqs=("REQ-000", "REQ-073"),
        tests=("signalforge/tests/test_repo_layout.py",),
        fallback=(
            "If a port is busy: SIGNALFORGE_PORT / SIGNALFORGE_WEB_PORT override it. "
            "If the harness will not build, run.sh says so and continues - the "
            "platform and every check below except 3 still work."
        ),
        artifacts=("docs/09-deployment.md", "docs/22-release-package.md"),
    ),
    Check(
        number=2,
        title="Any application onboards through public contracts only",
        guide_section="05 - complete product layers",
        action=(
            "Onboarding wizard: connect, test (including a deliberate failure), "
            "auto-discover, map golden signals and journeys, topology, SLOs, "
            "readiness score"
        ),
        surface="/apps/new wizard, POST /apps, POST /connectors, PUT /apps/{id}/mapping",
        expected=(
            "The reference harness is fully onboarded with no seeds and no "
            "app-specific code in the core; scripts/check_clean.sh stays green"
        ),
        reqs=("REQ-030", "REQ-036", "REQ-038"),
        tests=(
            "signalforge/tests/onboarding",
            "signalforge/tests/reference/test_reference_onboarding.py",
            "signalforge/tests/test_repo_layout.py::test_check_clean_passes_on_clean_tree",
        ),
        fallback=(
            "If the wizard is mid-step, POST the same bodies over the API - the "
            "wizard has no private endpoints. drills/recovery-collision.yaml "
            "steps 2-14 are exactly those calls."
        ),
        artifacts=("docs/04-functional-spec.md", "docs/11-openapi.json"),
    ),
    Check(
        number=3,
        title="Detection corroborates, proposes a severity, and declares nothing",
        guide_section="03 - human, AI and software collaboration",
        action=(
            "Drive steady traffic, inject the published fault, watch Steady -> "
            "Watch -> Proposed"
        ),
        surface="GET /apps/{id}/detection, the app cockpit",
        expected=(
            "A P0 proposal corroborated by two independent families, one "
            "customer-facing, carrying requires_human: true and declared: false. "
            "The incident exists only after a person presses Declare."
        ),
        reqs=("REQ-040", "REQ-041", "REQ-042", "REQ-046"),
        tests=(
            "signalforge/tests/detection/test_detection_flow.py",
            "signalforge/tests/drills/test_recovery_collision.py",
        ),
        fallback=(
            "If the live detector has not reached Proposed yet, the deterministic "
            "drill reaches it in seconds: pytest "
            "signalforge/tests/drills/test_recovery_collision.py"
        ),
    ),
    Check(
        number=4,
        title="War room, paging, escalation, and a late joiner who catches up",
        guide_section="12 - state and resumability",
        action=(
            "Declare, watch the ack board count down, let one page go unanswered, "
            "then join as a second responder and read the brief"
        ),
        surface="/incidents/{id} war room, /my/pages, GET /incidents/{id}/brief",
        expected=(
            "The unanswered page escalates to the next person in the rotation; a "
            "different user with the right role resumes the incident and the brief "
            "tells them what they missed"
        ),
        reqs=("REQ-043", "REQ-044", "REQ-045"),
        tests=(
            "signalforge/tests/warroom",
            "signalforge/tests/drills/test_recovery_collision.py",
        ),
        fallback=(
            "Escalation can be forced without waiting: POST "
            "/incidents/{id}/pages/{page_id}/escalate"
        ),
    ),
    Check(
        number=5,
        title="Incident Brain: three hypotheses, moving on evidence, every number explained",
        guide_section="08 - AI controls and accountability",
        action=(
            "Refresh the brain, post a measurement in the room, refresh again, then "
            "post a contradicting statement and refresh once more"
        ),
        surface="GET /incidents/{id}/brain, /brain/guidance, /brain/topology",
        expected=(
            "Confidence moves with the arithmetic shown and a discriminating test "
            "beside it; the contradiction is surfaced rather than averaged away; "
            "blast radius separates CONFIRMED from PREDICTED"
        ),
        reqs=("REQ-050", "REQ-052", "REQ-053", "REQ-054"),
        tests=(
            "signalforge/tests/brain/test_sprint5_acceptance.py",
            "signalforge/tests/brain/test_confidence.py",
        ),
        fallback=(
            "With no model the brain still ranks and explains; the badge says "
            "degraded. That is the intended demonstration, not a failure."
        ),
        artifacts=("docs/08-agent-architecture.md", "docs/15-ai-eval.md"),
    ),
    Check(
        number=6,
        title="A control actively blocks an unsafe action",
        guide_section="07 - security by design (the guide's twelfth check)",
        action=(
            "Ask for the high-risk rollback with intent=auto; then, as an org owner "
            "holding every permission, try to configure it to auto-execute"
        ),
        surface=(
            "POST /incidents/{id}/executions, POST /apps/{id}/policy, "
            "the policy editor's locked rows, /metrics"
        ),
        expected=(
            "The execution is BLOCKED naming FLOOR-AUTO-EXECUTE-REFUSED and nothing "
            "reaches the application; the policy edit is refused 422 naming "
            "FLOOR-HIGH-RISK-NO-AUTO, with policy.floor.rejected in the audit chain "
            "and signalforge_policy_floor_rejections_total incremented"
        ),
        reqs=("REQ-060", "REQ-016"),
        tests=(
            "signalforge/tests/governance/test_policy_floor.py",
            "signalforge/tests/governance/test_sprint6_acceptance.py::test_a_control_actively_prevents_an_unsafe_configuration",
        ),
        fallback=(
            "The refusal is visible three ways: the 422 body, the audit entry, and "
            "the metric. If the UI is not to hand, curl the policy POST."
        ),
        artifacts=("docs/13-controls-matrix.md", "docs/12-threat-model.md"),
    ),
    Check(
        number=7,
        title="A human approves, and it is a different human",
        guide_section="03 - human authority over consequential action",
        action="Request the rollback properly; approve it as a second commander",
        surface="POST /incidents/{id}/executions, then .../approve",
        expected=(
            "approval_required with the floor rule named; the approval is refused "
            "for the requester and granted for a second person, recording which "
            "permission was exercised and when it expires"
        ),
        reqs=("REQ-061",),
        tests=(
            "signalforge/tests/governance/test_tool_gateway.py",
            "signalforge/tests/drills/test_drill_engine.py::test_virtual_time_expires_a_real_approval",
        ),
        fallback=(
            "If the approval has expired mid-demo, that is itself the "
            "demonstration - request it again and approve within the TTL."
        ),
    ),
    Check(
        number=8,
        title="The API succeeded but the response was lost",
        guide_section="06 and 11 - architecture defence, exception handling",
        action=(
            "Run the approved rollback against a target that stops answering; try "
            "to retry; then reconcile; then submit the same action again"
        ),
        surface="the execution ledger, POST .../run, .../retry, .../reconcile",
        expected=(
            "UNCERTAIN with error_category timeout; the retry is refused 409 telling "
            "you to reconcile; reconciliation returns COMPLETED_LATE under the "
            "derived idempotency key; the duplicate submission sends nothing"
        ),
        reqs=("REQ-062",),
        tests=(
            "signalforge/tests/governance/test_tool_gateway.py",
            "signalforge/tests/nfr/test_resilience.py::test_an_adapter_timeout_lands_uncertain_and_blocks_the_retry",
        ),
        fallback=(
            "Every transition is in the ledger and the audit chain, so the story "
            "can be told from GET /incidents/{id}/executions alone."
        ),
        artifacts=("docs/06-solution-architecture.md",),
    ),
    Check(
        number=9,
        title="Independent validation refuses to close on infrastructure health",
        guide_section="11 - exception handling",
        action=(
            "Recover the estate but leave the customer journey failing, run "
            "validation, then try to close"
        ),
        surface="POST /incidents/{id}/validation, POST /incidents/{id}/close",
        expected=(
            "technical passes, service passes, customer fails; verdict "
            "not_recovered; closure blocked naming the customer layer; the failure "
            "becomes evidence on the graph with a result_ref"
        ),
        reqs=("REQ-065",),
        tests=(
            "signalforge/tests/governance/test_layered_validation.py",
            "signalforge/tests/nfr/test_resilience.py::test_a_healthy_estate_with_a_failing_customer_journey_cannot_close",
        ),
        fallback=(
            "The closure lock is enforced by the lifecycle guard, so the 409 is "
            "reproducible from curl without the UI."
        ),
    ),
    Check(
        number=10,
        title="Communications are governed: nothing auto-sends, corrections need approval",
        guide_section="08 - AI controls and accountability",
        action=(
            "Draft a customer statement from the graph, approve and issue it, let "
            "the evidence move, then correct it"
        ),
        surface="/incidents/{id}/communications and its approve / issue / correction",
        expected=(
            "The draft cites the nodes it was written from; a different person "
            "approves it; when its grounding is superseded it is marked contradicted "
            "with the published text untouched; the correction carries a redline and "
            "needs its own approval"
        ),
        reqs=("REQ-064",),
        tests=(
            "signalforge/tests/governance/test_communications.py",
            "signalforge/tests/drills/test_recovery_collision.py",
        ),
        fallback=(
            "Contradiction is detected when the comms board is read, so refresh the "
            "board if the flag has not appeared yet."
        ),
    ),
    Check(
        number=11,
        title="Audit replay and PIR - and tampering is detected",
        guide_section="14 - observability and audit evidence",
        action=(
            "Replay the closed incident as an auditor, generate the PIR, then edit "
            "one stored audit payload and replay again"
        ),
        surface="GET /incidents/{id}/replay, POST /incidents/{id}/pir",
        expected=(
            "The replay reconstructs the incident with actor, justification, effect "
            "and references, and reports the chain valid; the PIR generates "
            "deterministically with verified and proposed lessons separated; after "
            "the edit the replay reports invalid and names the first broken record, "
            "and the PIR refuses to generate at all"
        ),
        reqs=("REQ-072", "REQ-016"),
        tests=(
            "signalforge/tests/audit/test_audit_replay.py",
            "signalforge/tests/audit/test_pir.py",
        ),
        fallback=(
            "The tamper demonstration runs against a scratch database inside the "
            "test; never edit a live audit log to show it."
        ),
        artifacts=("docs/20-RTM.md",),
    ),
    Check(
        number=12,
        title="Live traceability: a requirement, its tests and its runtime evidence",
        guide_section="09 and 10 - traceability and SPECS/DSL",
        action=(
            "Pick any REQ id from specs/requirements.yaml and run "
            "`make evidence REQ=REQ-062`, then `make rtm && make report`"
        ),
        surface="terminal, docs/20-RTM.md, docs/21-test-report.md",
        expected=(
            "The requirement resolves to its implementation, its REQ-tagged tests "
            "and live events pulled from the running platform; the RTM and the test "
            "report regenerate from the actual run rather than being edited"
        ),
        reqs=("REQ-006",),
        tests=("signalforge/tests/test_traceability.py",),
        fallback=(
            "If no incident is live, `make evidence` still resolves the "
            "requirement, its tests and its outcome - only the runtime events are "
            "empty, and it says so."
        ),
        artifacts=("docs/20-RTM.md", "docs/21-test-report.md", "specs/requirements.yaml"),
    ),
)


def run_check(check: Check, *, quiet: bool) -> tuple[bool, float, str]:
    started = time.monotonic()
    command = [PY, "-m", "pytest", "-q", "--no-header", "-p", "no:cacheprovider", *check.tests]
    completed = subprocess.run(
        command, cwd=ROOT, capture_output=True, text=True
    )
    elapsed = time.monotonic() - started
    tail = (completed.stdout or completed.stderr or "").strip().splitlines()
    summary = tail[-1] if tail else "no output"
    if not quiet and completed.returncode != 0:
        print(completed.stdout[-4000:])
    return completed.returncode == 0, elapsed, summary


def print_mapping() -> None:
    for check in CHECKS:
        print(f"\n{check.number:>2}. {check.title}")
        print(f"    guide     {check.guide_section}")
        print(f"    action    {check.action}")
        print(f"    surface   {check.surface}")
        print(f"    expected  {check.expected}")
        print(f"    reqs      {', '.join(check.reqs)}")
        print(f"    tests     {', '.join(check.tests)}")
        print(f"    fallback  {check.fallback}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--list", action="store_true", help="print the mapping and stop")
    parser.add_argument("--only", type=int, default=None, help="run one check by number")
    parser.add_argument("--quiet", action="store_true", help="do not echo pytest output")
    args = parser.parse_args()

    if args.list:
        print_mapping()
        return 0

    selected = [c for c in CHECKS if args.only is None or c.number == args.only]
    if not selected:
        print(f"no such check: {args.only}")
        return 2

    print("SignalForge - final demonstration checks\n")
    results: list[tuple[Check, bool, float, str]] = []
    for check in selected:
        print(f"[{check.number:>2}/12] {check.title} ... ", end="", flush=True)
        ok, elapsed, summary = run_check(check, quiet=args.quiet)
        print(f"{'PASS' if ok else 'FAIL'}  ({elapsed:.1f}s)  {summary}")
        results.append((check, ok, elapsed, summary))

    failed = [c for c, ok, _e, _s in results if not ok]
    print("\n" + "-" * 72)
    print(f"{len(results) - len(failed)}/{len(results)} checks passed")
    if failed:
        print("failed: " + ", ".join(str(c.number) for c in failed))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
