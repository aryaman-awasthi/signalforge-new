"""S5-T05: the Incident Brain screens (REQ-050, REQ-053, REQ-055).

Structural rules in the same spirit as the S0-S4 web tests: thin routes, logic in
`features/`, everything through the typed cookie proxy, and the cockpit's types
pinned against the API's own response models so a contract that gains a field
fails a test rather than quietly going unrendered.

Four properties here are not structural and matter more than the rest, because
each is a way the cockpit could quietly undo a guarantee the server spent a
sprint establishing:

- **CONFIRMED and PREDICTED are never merged.** Two lists, two colours, and a
  legend that spells out what each means in words. A predicted downstream outage
  rendered like an observed one is how a war room pages four teams for an outage
  that never happened.
- **No confidence is computed in the browser.** Every percentage on the screen is
  the server's arithmetic over weighted evidence. A number computed here would be
  a second opinion nobody audited.
- **An ungrounded copilot answer is never displayed.** The server discards it;
  the cockpit must not have a path that shows it with a warning.
- **There is still exactly one WebSocket store.** The brain reaches the screen on
  the frame the cockpit was already receiving.
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


BRAIN_FILES = (
    "features/brain/api.ts",
    "features/brain/hooks/useBrain.ts",
    "features/brain/components/BrainPanel.tsx",
    "features/brain/components/HypothesisCard.tsx",
    "features/brain/components/Ledger.tsx",
    "features/brain/components/Sparkline.tsx",
    "features/brain/components/TopologyPanel.tsx",
    "features/brain/components/GuidancePanel.tsx",
    "features/brain/components/CopilotPanel.tsx",
)


@pytest.mark.req("REQ-050")
def test_the_brain_feature_exists_and_follows_the_feature_layout():
    for path in BRAIN_FILES:
        assert (WEB / path).exists(), f"missing {path}"
    # Route files stay thin: no brain logic under app/.
    for route in WEB.glob("app/**/*.tsx"):
        source = route.read_text(encoding="utf-8")
        assert "getBrain(" not in source and "askCopilot(" not in source, (
            f"{route} calls the brain API directly; logic belongs in features/"
        )


@pytest.mark.req("REQ-050")
def test_every_brain_call_goes_through_the_typed_cookie_proxy():
    api = read("features", "brain", "api.ts")
    assert 'from "@/lib/api/client"' in api
    assert "proxyApi" in api
    for forbidden in ("fetch(", "axios", "localhost:8000", "process.env"):
        assert forbidden not in api, (
            f"the brain client must not use {forbidden}: every read goes through "
            "the one proxy that holds the session cookie"
        )


@pytest.mark.req("REQ-050")
def test_the_cockpit_types_carry_the_confirmed_predicted_distinction():
    api = read("features", "brain", "api.ts")
    assert 'export type Confirmation = "confirmed" | "predicted";' in api
    assert "confirmation: Confirmation | null;" in api
    # And the two lists are separate fields on the topology, not one list with a
    # flag a renderer could forget to read.
    assert "confirmed: string[];" in api
    assert "predicted: string[];" in api


@pytest.mark.req("REQ-050")
def test_the_topology_panel_renders_two_lists_a_legend_and_a_version_switcher():
    panel = read("features", "brain", "components", "TopologyPanel.tsx")
    assert 'title="CONFIRMED"' in panel and 'title="PREDICTED"' in panel
    assert "something was measured on these" in panel
    assert "nothing has been measured on them" in panel
    assert "topology-version" in panel, "the version switcher is required"
    assert "Changed since" in panel, "the changed-since list is required"
    # The graph is rendered, and it is a graph rather than a list of names.
    assert "<svg" in panel and "polyline" not in panel
    assert "<line" in panel and "<rect" in panel


@pytest.mark.req("REQ-053")
def test_no_confidence_is_computed_in_the_browser():
    """Every percentage is the server's arithmetic. The cockpit may round it for
    display and may not derive it."""
    for component in ("HypothesisCard.tsx", "BrainPanel.tsx", "Sparkline.tsx"):
        source = code("features", "brain", "components", component)
        for forbidden in ("Math.exp", "Math.log", "netWeight", "* weight"):
            assert forbidden not in source, (
                f"{component} contains {forbidden!r}; confidence is computed on "
                "the server from weighted evidence and never in the browser"
            )


@pytest.mark.req("REQ-053")
def test_the_hypothesis_card_answers_why_the_number_moved():
    """The question an incident commander asks out loud."""
    card = read("features", "brain", "components", "HypothesisCard.tsx")
    assert "<Sparkline" in card
    assert "Evidence for" in card and "Evidence against" in card
    assert "discriminating_test" in card and "Next test" in card
    assert "Confidence history" in card
    # The deterministic explanation is always shown; the model's narrative sits
    # beside it and is labelled.
    assert "hypothesis.explain" in card
    assert "written by a model" in card


@pytest.mark.req("REQ-050")
def test_the_ledger_separates_facts_from_assumptions_and_opens_version_history():
    ledger = read("features", "brain", "components", "Ledger.tsx")
    for tab in ("Facts", "Assumptions", "Evidence", "Impact", "Risks", "Actions"):
        assert f'label: "{tab}"' in ledger
    assert "getNodeHistory" in ledger
    assert "superseded or retracted" in ledger, (
        "nothing is deleted, and the ledger must be able to show the old versions"
    )
    assert "provenance.authority" in ledger, "every row carries where it came from"


@pytest.mark.req("REQ-055")
def test_the_copilot_shows_a_verdict_badge_and_citation_chips():
    panel = read("features", "brain", "components", "CopilotPanel.tsx")
    for verdict in ("grounded", "insufficient_evidence", "ungrounded", "degraded"):
        assert verdict in panel, f"the copilot must render the {verdict} state"
    assert "answer.citations" in panel, "citations are chips, not prose"
    assert "answer discarded" in panel
    assert "injection_observed" in panel
    assert "holds\n              no tools" in panel or "no tools" in panel


@pytest.mark.req("REQ-055")
def test_the_copilot_has_no_path_that_renders_a_discarded_answer():
    """The server replaces an ungrounded answer with a refusal. The cockpit must
    not have a branch that would show the original text if one ever arrived."""
    panel = code("features", "brain", "components", "CopilotPanel.tsx")
    # There is exactly one place the answer body is rendered, and it is the
    # server's `answer` field - never a raw model field.
    assert panel.count("{answer.answer}") == 1
    for forbidden in ("raw_answer", "original", "dangerouslySetInnerHTML"):
        assert forbidden not in panel


@pytest.mark.req("REQ-052")
def test_guidance_renders_the_caveat_from_the_server_rather_than_typing_one():
    """A client that could render the rows without the caveat would eventually
    render the rows without the caveat."""
    panel = read("features", "brain", "components", "GuidancePanel.tsx")
    assert "{guidance.caveat}" in panel
    assert "Pinned mistake warnings" in panel
    assert "Similar prior incidents" in panel
    assert "Contradictions" in panel
    assert "relevance" in panel and "movement" in panel


@pytest.mark.req("REQ-033", "REQ-050")
def test_the_brain_shares_the_one_websocket_store():
    """One socket, one store. A second connection would be a second
    authorisation surface and a second thing to get wrong."""
    hook = read("features", "brain", "hooks", "useBrain.ts")
    assert "useLiveRoom" in hook
    for forbidden in ("new WebSocket", "wsStore.connect", "/ws?"):
        assert forbidden not in hook, (
            f"useBrain must not {forbidden}: it shares the cockpit's socket"
        )
    stores = [
        path
        for path in WEB.glob("**/*.ts")
        if "node_modules" not in str(path)
        and "new WebSocket(" in path.read_text(encoding="utf-8")
    ]
    assert len(stores) == 1, f"there must be exactly one WebSocket store, found {stores}"


@pytest.mark.req("REQ-051")
def test_the_degraded_state_says_what_still_works():
    """"The model is unavailable" alone reads as "this panel is broken"."""
    panel = read("features", "brain", "components", "BrainPanel.tsx")
    assert "AI degraded" in panel
    assert "Everything below was still computed" in panel
    assert "confidences are arithmetic" in panel


@pytest.mark.req("REQ-012", "REQ-050")
def test_the_reasoning_button_is_hidden_from_people_who_may_not_press_it():
    """The screen agrees with the guard (DR-101).

    `POST .../brain/refresh` carries `declare_incident`, so a button offered to
    an SME could only ever answer 403 - and a control that exists and always
    refuses teaches people the product is broken. It is hidden, and the panel
    says who does it instead, so a paged expert knows what to ask for.

    The permission is read from the session the same way the rest of the war
    room reads it; the cockpit decides nothing about authority on its own.
    """
    panel = read("features", "brain", "components", "BrainPanel.tsx")
    shell = read("features", "warroom", "components", "WarRoomShell.tsx")

    assert "canRefresh" in panel, "the panel must be told whether to offer it"
    assert "canRefresh ? (" in panel, "hidden rather than disabled-and-refusing"
    assert "incident command runs the reasoning" in panel, (
        "and it must say who can, not just remove the button"
    )
    assert "canRefresh={canCommand}" in shell
    assert 'me?.permissions.includes("declare_incident")' in shell, (
        "the answer comes from the session's own permissions"
    )
