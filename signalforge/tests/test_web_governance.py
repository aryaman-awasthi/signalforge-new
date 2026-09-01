"""S6-T06: the governance cockpit (REQ-060..REQ-065).

Structural rules in the same spirit as the S0-S5 web tests: thin routes, logic in
`features/`, everything through the typed cookie proxy, and the cockpit's types
pinned against the API's own response models so a contract that gains a field
fails a test rather than quietly going unrendered.

Five properties here are not structural and matter more than the rest, because
each is a way the cockpit could quietly undo a guarantee the backend spent this
sprint establishing:

- **No auto-execute control exists on any action card.** Not disabled, not behind
  a confirmation - absent. The floor refuses `intent: auto` regardless, but a
  screen that offered the affordance would teach an operator that it is a thing
  the platform does.
- **Retry is not rendered beside an uncertain execution.** The server answers 409;
  the cockpit must not have a path that offers the button and lets the error come
  back, because a Retry next to a rollback whose outcome is unknown is an offer
  to apply it twice.
- **The policy editor's locks come from the server.** `locked` is computed in the
  domain and rendered here; the browser never decides which rows are the floor's.
- **A contradicted communication is not edited.** The panel shows the published
  text and offers a correction; there is no path that rewrites it.
- **There is still exactly one WebSocket store.** Approvals, ledger transitions,
  contradictions and validation verdicts reach the screen on the frame the
  cockpit was already receiving.
"""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
WEB = ROOT / "web"


def read(*parts: str) -> str:
    return (WEB.joinpath(*parts)).read_text(encoding="utf-8")


def code(*parts: str) -> str:
    """The source with comments stripped, for "this does not appear" assertions -
    the files that most deserve one are exactly those whose comments explain why
    the construct is absent."""
    source = read(*parts)
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.DOTALL)
    return re.sub(r"^\s*//.*$", "", source, flags=re.MULTILINE)


GOVERNANCE_FILES = (
    "features/governance/api.ts",
    "features/governance/hooks/useGovernance.ts",
    "features/governance/components/Chips.tsx",
    "features/governance/components/NextBestAction.tsx",
    "features/governance/components/ApprovalSheet.tsx",
    "features/governance/components/ExecutionLedger.tsx",
    "features/governance/components/PlanDiff.tsx",
    "features/governance/components/CommsPanel.tsx",
    "features/governance/components/ValidationPanel.tsx",
    "features/governance/components/PolicyEditor.tsx",
)


@pytest.mark.req("REQ-060")
def test_the_governance_feature_exists_and_routes_stay_thin():
    for path in GOVERNANCE_FILES:
        assert (WEB / path).exists(), f"{path} is missing"
    page = read("app", "apps", "[appId]", "policy", "page.tsx")
    assert "<PolicyEditor" in page
    assert page.count("\n") < 30, "a route file holds no logic"


@pytest.mark.req("REQ-060", "REQ-061")
def test_everything_goes_through_the_typed_cookie_proxy():
    api = read("features", "governance", "api.ts")
    assert "proxyApi" in api
    for path in GOVERNANCE_FILES:
        source = read(*path.split("/"))
        assert "fetch(" not in source, f"{path} must not call fetch directly"
        assert "API_BASE_URL" not in source, f"{path} must not name the API host"


@pytest.mark.req("REQ-061")
def test_no_auto_execute_control_exists_on_any_action_surface():
    """The absence is the design.

    The backend refuses `intent: auto` on a high-risk action whatever a client
    sends, so this is not the control - it is the half an operator can see, and
    an affordance here would teach them that auto-execution is a thing the
    platform does.
    """
    for path in (
        "features/governance/components/NextBestAction.tsx",
        "features/governance/components/ExecutionLedger.tsx",
        "features/governance/components/ApprovalSheet.tsx",
    ):
        source = code(*path.split("/"))
        lowered = source.lower()
        assert "auto_execute" not in lowered, f"{path} offers auto-execution"
        assert "intent" not in lowered or "auto" not in lowered.split("intent")[1][:80]
    api = code("features", "governance", "api.ts")
    assert "intent" not in api, "the client has no way to ask for auto-execution"


@pytest.mark.req("REQ-062")
def test_retry_is_not_offered_while_an_outcome_is_uncertain():
    ledger = read("features", "governance", "components", "ExecutionLedger.tsx")
    # Retry is rendered only for the one state that provably did not apply.
    assert 'execution.state === "not_applied" && !execution.retry_blocked' in ledger
    # And the uncertain branch offers reconciliation instead, in words.
    assert "Reconcile" in ledger
    assert "Retry is unavailable until this is reconciled" in ledger
    assert "retry_blocked" in read("features", "governance", "api.ts")


@pytest.mark.req("REQ-062")
def test_the_uncertain_state_is_visually_distinct_and_says_what_it_means():
    ledger = read("features", "governance", "components", "ExecutionLedger.tsx")
    assert "border-danger bg-danger/10" in ledger
    meanings = ledger.split("STATE_MEANING")[1]
    for state in (
        "uncertain",
        "completed_late",
        "not_applied",
        "partial",
        "taken_over",
    ):
        assert f"{state}:" in meanings, f"{state} must say what it means"


@pytest.mark.req("REQ-061")
def test_the_approval_sheet_shows_identity_rule_and_a_live_expiry():
    sheet = read("features", "governance", "components", "ApprovalSheet.tsx")
    assert "useSecondsLeft" in sheet, "the expiry counts down"
    assert "required_roles" in sheet, "who may approve is on the screen"
    assert "rules_hit" in sheet, "and which rule made it necessary"
    assert "typed.trim() === execution.action_id" in sheet, (
        "the confirmation asks for the action id, not YES"
    )
    assert "policy_ref" in sheet


@pytest.mark.req("REQ-063")
def test_no_score_is_computed_in_the_browser():
    """Every number on the action card is the server's arithmetic. A score
    computed here would be a second opinion nobody audited."""
    card = code("features", "governance", "components", "NextBestAction.tsx")
    assert "urgency *" not in card
    assert "* impact" not in card
    for term in ("urgency", "impact", "information_gain", "risk", "score"):
        assert term in read("features", "governance", "api.ts")


@pytest.mark.req("REQ-063")
def test_an_ineligible_action_is_shown_with_its_reason_and_cannot_be_primary():
    card = read("features", "governance", "components", "NextBestAction.tsx")
    assert "ineligible" in card
    assert "eligibility_reason" in card
    assert "disabled={!canCommand || !candidate.eligible || busy}" in card


@pytest.mark.req("REQ-063")
def test_the_plan_diff_renders_the_triad_and_keeps_paused_rows():
    plan = read("features", "governance", "components", "PlanDiff.tsx")
    for disposition in ("kept", "paused", "added"):
        assert f"{disposition}:" in plan or f'"{disposition}"' in plan
    assert "changed_assumption" in plan
    assert "line-through" in plan, "a paused action stays visible rather than vanishing"


@pytest.mark.req("REQ-064")
def test_the_comms_panel_has_no_send_and_never_rewrites_issued_text():
    panel = code("features", "governance", "components", "CommsPanel.tsx")
    for forbidden in ("sendCommunication", "autoSend", "auto_send", "schedule("):
        assert forbidden not in panel, f"{forbidden} must not exist"
    assert "issueCommunication" in panel, "a person issues it"
    assert "draftCorrection" in panel, "and corrects it with a new statement"
    source = read("features", "governance", "components", "CommsPanel.tsx")
    assert "has not been changed" in source, (
        "the panel says the published text is untouched"
    )
    assert "redline" in source


@pytest.mark.req("REQ-065")
def test_validation_shows_four_layers_and_names_the_system_each_read():
    panel = read("features", "governance", "components", "ValidationPanel.tsx")
    for layer in ("technical", "service", "customer", "business"):
        assert layer in panel
    assert "read from" in panel, "each check names its source on the screen"
    assert "closure_lock_reason" in panel
    assert "Closure is locked" in panel
    assert "regressions" in panel, "a reset window says how often it reset"


@pytest.mark.req("REQ-060")
def test_the_policy_editor_locks_the_high_risk_rows_from_the_server():
    editor = read("features", "governance", "components", "PolicyEditor.tsx")
    assert "row.locked" in editor
    assert "locked_reason" in editor
    assert "Cannot auto-execute" in editor
    # And it says out loud that the lock is not this screen's doing.
    assert "enforced in the domain" in editor
    assert "dryRunPolicy" in editor


@pytest.mark.req("REQ-033", "REQ-061")
def test_there_is_still_exactly_one_websocket_store():
    hook = read("features", "governance", "hooks", "useGovernance.ts")
    assert "useLiveRoom" in hook
    assert "new WebSocket" not in hook
    for path in GOVERNANCE_FILES:
        assert "new WebSocket" not in read(*path.split("/")), path


@pytest.mark.req("REQ-045", "REQ-061")
def test_the_sprint_six_panels_are_filled_in_the_places_sprint_four_reserved():
    """The layout did not move: Next Best Action is where it was, with the same
    title, and the new surfaces sit beside the grid rather than inside it."""
    shell = read("features", "warroom", "components", "WarRoomShell.tsx")
    block = shell.split('title="Next Best Action"')[1].split("</Panel>")[0]
    assert "pending=" not in block, "Next Best Action is built"
    assert "<NextBestAction" in block
    for title, component in (
        ("Execution Ledger", "<ExecutionLedger"),
        ("Recovery Validation", "<ValidationPanel"),
        ("Communications", "<CommsPanel"),
        ("Response Plan", "<PlanDiff"),
    ):
        assert f'title="{title}"' in shell, f"{title} is missing"
        assert component in shell


@pytest.mark.req("REQ-061")
def test_the_cockpit_types_match_the_api_response_models():
    """A contract that gains a field fails a test rather than quietly going
    unrendered."""
    from signalforge.api.schemas.governance import (
        CandidateOut,
        DecisionOut,
        ExecutionOut,
        LayerOut,
    )

    api = read("features", "governance", "api.ts")
    for model in (ExecutionOut, DecisionOut, CandidateOut, LayerOut):
        for field in model.model_fields:
            assert field in api, f"{model.__name__}.{field} is not in the client"
