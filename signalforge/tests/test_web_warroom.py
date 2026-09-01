"""S4-T04: the detection, lifecycle and war-room screens (REQ-040..REQ-045).

Structural rules in the same spirit as the S0-S3 web tests: thin routes, logic in
features, everything through the typed cookie proxy, and - the part that does the
real work - the cockpit's types pinned against the API's own response models, so
a contract that gains a field fails a test rather than quietly going unrendered.

Two properties here are not structural and matter more than the rest:

- **The ten S9 panel titles are pinned verbatim.** They are a fixed vocabulary:
  an incident commander who learns where "Next Best Action" is should find it
  under the same name in every room, every drill and every screenshot in the
  runbook. Renaming one has to be a decision somebody makes in this file.
- **The countdown must come from the server.** A browser that computed
  `deadline - Date.now()` would show a responder a different number from the one
  the escalation sweep is acting on, and the two disagreeing during a P0 is how
  people conclude the pager is broken.
"""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
WEB = ROOT / "web"


def read(*parts: str) -> str:
    return (WEB.joinpath(*parts)).read_text(encoding="utf-8")


def code(*parts: str) -> str:
    """The source with its comments removed.

    Needed by any assertion of the form "this construct does not appear", because
    the files that most deserve such an assertion are exactly the ones whose
    comments explain why the construct is absent - and a test that cannot tell
    an explanation from an implementation would forbid documenting the rule it
    is enforcing.
    """
    source = read(*parts)
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.DOTALL)
    return re.sub(r"^\s*//.*$", "", source, flags=re.MULTILINE)


# The S9 shell's fixed vocabulary, from the sprint plan, verbatim.
PANEL_TITLES = (
    "Incident Overview",
    "Live Bridge Transcript",
    "Team Chat",
    "AI Incident Brain",
    "Next Best Action",
    "AI Copilot",
    "Incident Timeline",
    "Warning and Guidance",
    "Health Score and Metrics",
    "Affected Services",
)


@pytest.mark.req("REQ-045")
def test_the_detection_lifecycle_and_war_room_screens_exist():
    for path in [
        "app/apps/[appId]/page.tsx",
        "app/incidents/page.tsx",
        "app/incidents/[incidentId]/page.tsx",
        "app/my/pages/page.tsx",
        "features/incidents/api.ts",
        "features/incidents/hooks/useLifecycle.ts",
        "features/incidents/components/AppDetail.tsx",
        "features/incidents/components/GoldenTiles.tsx",
        "features/incidents/components/HealthPanel.tsx",
        "features/incidents/components/ChangeWindow.tsx",
        "features/incidents/components/WatchBanner.tsx",
        "features/incidents/components/ProposalSheet.tsx",
        "features/incidents/components/IncidentList.tsx",
        "features/warroom/api.ts",
        "features/warroom/hooks/useWarRoom.ts",
        "features/warroom/hooks/useCountdown.ts",
        "features/warroom/components/WarRoomShell.tsx",
        "features/warroom/components/Panel.tsx",
        "features/warroom/components/AckBoard.tsx",
        "features/warroom/components/BridgePanel.tsx",
        "features/warroom/components/TaskBoard.tsx",
        "features/warroom/components/MobilePaging.tsx",
    ]:
        assert (WEB / path).exists(), f"missing {path}"


@pytest.mark.req("REQ-045")
def test_the_war_room_renders_all_ten_panel_titles_and_who_is_in_the_room():
    """Pinned verbatim. A renamed panel fails here rather than in a screenshot."""
    shell = read("features", "warroom", "components", "WarRoomShell.tsx")
    health = read("features", "incidents", "components", "HealthPanel.tsx")
    for title in PANEL_TITLES:
        rendered = f'title="{title}"' in shell or f">\n        {title}\n" in shell
        # Health Score and Metrics appears in both the S5 panel and the S9 shell.
        if title == "Health Score and Metrics":
            rendered = rendered or title in health
        assert rendered, f"the S9 shell does not render the panel {title!r}"
    assert 'title="Who is in the room"' in shell


@pytest.mark.req("REQ-045")
def test_the_countdown_ticks_from_the_servers_number_and_not_from_the_browsers_clock():
    """The one piece of arithmetic this cockpit is not allowed to do itself.

    A responder's laptop clock can be minutes off. If the browser computed the
    remaining time from a parsed deadline, that screen would disagree with the
    escalation sweep - and the sweep is the thing that actually escalates.
    """
    hook = code("features", "warroom", "hooks", "useCountdown.ts")
    for forbidden in ("Date.now()", "Date.parse", "new Date("):
        assert forbidden not in hook, (
            f"the countdown must not use {forbidden}: it would derive the "
            "remaining time from the browser's clock rather than the server's"
        )
    for component in ("AckBoard.tsx", "MobilePaging.tsx"):
        source = code("features", "warroom", "components", component)
        assert "useCountdown(page.countdown_s)" in source, (
            f"{component} must anchor its timer on the server's countdown_s"
        )
        assert "Date.now()" not in source


@pytest.mark.req("REQ-045", "REQ-046")
def test_a_proposal_can_never_be_typed_as_a_declaration():
    """`requires_human` and `declared` are literal types, not booleans.

    Detection reaches a proposal and stops. Typing these as literals means a
    screen that tries to treat a proposal as an incident does not compile - which
    is a stronger guarantee than a code review.
    """
    api = read("features", "incidents", "api.ts")
    assert "requires_human: true;" in api
    assert "declared: false;" in api
    assert "declared_by_human: true;" in api


@pytest.mark.req("REQ-043", "REQ-046")
def test_the_proposal_sheet_declares_through_the_api_and_says_the_act_is_human():
    sheet = read("features", "incidents", "components", "ProposalSheet.tsx")
    assert "declareIncident" in sheet
    assert "Only a person declares an incident" in sheet
    # The two declare paths are separate acts and the record says which.
    assert "as proposed" in sheet
    assert "recorded as an override" in sheet


@pytest.mark.req("REQ-042")
def test_the_corroboration_checklist_shows_the_rows_that_did_not_pass():
    """A proposal that is not corroborated is the one somebody needs to look at
    carefully. Hiding the failed rows would make it look like the strong one."""
    sheet = read("features", "incidents", "components", "ProposalSheet.tsx")
    assert "Two or more independent signal families" in sheet
    assert "At least one is customer-facing" in sheet
    assert "Not an alert storm" in sheet
    assert "passed: false" not in sheet, "rows are computed, not hardcoded"
    assert "row.passed ?" in sheet, "failed rows render too, differently"


@pytest.mark.req("REQ-040")
def test_the_watch_banner_is_quiet_and_names_what_is_being_checked():
    """`watch` means nothing has been decided. A banner that shouted here would
    train people to ignore the one that shouts when it matters."""
    banner = read("features", "incidents", "components", "WatchBanner.tsx")
    assert "AI is checking" in banner
    assert "Nothing has been decided" in banner
    # No alarm colour on the watch banner - it uses the muted surface tokens.
    assert "bg-danger" not in banner and "text-danger" not in banner
    assert "signal.signal_key" in banner, "the list names the signals by name"


@pytest.mark.req("REQ-042")
def test_the_health_panel_toggles_to_grafana_and_always_states_the_fallback():
    """A browser gives an iframe no usable error, so the panel says in advance
    what a blank frame would mean rather than pretending it loaded."""
    panel = read("features", "incidents", "components", "HealthPanel.tsx")
    assert "SignalForge" in panel and "Grafana" in panel
    assert "<iframe" in panel
    assert "refusing to be embedded" in panel
    assert 'useState<HealthView>("signalforge")' in panel, (
        "the SignalForge view is the default: it cannot fail independently"
    )


@pytest.mark.req("REQ-044")
def test_the_change_window_chip_is_lit_by_the_api_and_a_stale_window_is_still_listed():
    source = read("features", "incidents", "components", "ChangeWindow.tsx")
    assert "view.open" in source, "the chip is the API's decision, not a count"
    assert "never announced completion" in source
    assert "change.stale" in source
    # The three groups the API actually publishes. An earlier version of this
    # screen read a `chip` field that does not exist, and rendered nothing.
    for group in ("windows", "stale", "recent"):
        assert f"view?.{group}" in source or f"view.{group}" in source, (
            f"the screen must render the API's `{group}` group"
        )


@pytest.mark.req("REQ-045")
def test_the_ack_board_shows_the_reason_and_offers_ack_only_on_your_own_row():
    """Rendering a button guaranteed to 403 teaches people the product is
    unreliable; the API refuses somebody else's page whatever is drawn."""
    board = read("features", "warroom", "components", "AckBoard.tsx")
    assert "{page.reason}" in board, "every row says why that person was asked"
    assert "page.recipient === currentUserId" in board
    assert "mine && page.state" in board
    assert "Escalate now" in board


@pytest.mark.req("REQ-045")
def test_the_mobile_pager_reads_a_route_that_cannot_name_anybody_else():
    pager = read("features", "warroom", "components", "MobilePaging.tsx")
    hook = read("features", "warroom", "hooks", "useWarRoom.ts")
    api = read("features", "warroom", "api.ts")
    assert "useMyPages" in pager
    assert "/my/pages?open_only=" in api
    assert "recipient" not in api.split("export const myPages")[1].split("\n")[0]
    assert "no recipient parameter" in hook


@pytest.mark.req("REQ-045")
def test_an_unassigned_task_says_it_is_unassigned_rather_than_naming_a_guess():
    board = read("features", "warroom", "components", "TaskBoard.tsx")
    assert "owner_label" in board
    assert "could not resolve to exactly one person" in board
    assert "task.owner_user_id === currentUserId" in board


@pytest.mark.req("REQ-045")
def test_the_unbuilt_agent_panels_are_rendered_with_their_reason():
    """A panel that quietly does not exist is indistinguishable from one that is
    broken, and the layout an incident commander learns must not move under them
    when Sprint 5 lands."""
    shell = read("features", "warroom", "components", "WarRoomShell.tsx")
    for title in ("Next Best Action", "AI Copilot", "Warning and Guidance", "Team Chat"):
        block = shell.split(f'title="{title}"')[1][:400]
        assert "pending=" in block, f"{title} must say what will fill it"
        assert "Sprint 5" in block


@pytest.mark.req("REQ-012")
def test_the_war_room_and_the_pager_are_not_behind_a_role_group():
    """The frozen matrix gives the SME no permissions, and the SME is who gets
    paged. A group guard on these paths would redirect exactly the people an
    incident exists to reach."""
    middleware = read("middleware.ts")
    groups = middleware.split("ORG_GROUP_PATHS = ")[1].split("]")[0]
    for path in ('"/incidents"', '"/my"', '"/my/pages"'):
        assert path not in groups, f"{path} must not join a role group"
    assert "must never join a role group" in middleware


@pytest.mark.req("REQ-007")
def test_the_new_routes_are_thin_and_all_data_goes_through_the_typed_proxy():
    for route in [
        "app/apps/[appId]/page.tsx",
        "app/incidents/page.tsx",
        "app/incidents/[incidentId]/page.tsx",
        "app/my/pages/page.tsx",
    ]:
        source = read(*route.split("/"))
        assert "fetch(" not in source and "useState" not in source, (
            f"{route} must be a thin route: logic belongs in features/"
        )
        assert len(source.splitlines()) < 25

    for feature in ("incidents", "warroom"):
        api = read("features", feature, "api.ts")
        assert "proxyApi" in api
        assert "API_BASE_URL" not in api and "fetch(" not in api, (
            "every call goes through the cookie proxy; the browser never sees a token"
        )


@pytest.mark.req("REQ-045")
def test_the_cockpits_types_match_the_apis_response_models():
    """Pinned field by field against the API's own schemas, so a contract that
    gains a field fails here rather than quietly going unrendered."""
    from signalforge.api.schemas.warroom import (
        BlastRadiusOut,
        BriefOut,
        MemberOut,
        PageOut,
        TaskOut,
        WarRoomOut,
    )
    from signalforge.api.schemas.incidents import (
        ChangeWindowItemOut,
        ChangeWindowOut,
        IncidentOut,
        LifecycleOut,
    )

    api = read("features", "warroom", "api.ts")
    incidents = read("features", "incidents", "api.ts")

    for model, source in (
        (PageOut, api),
        (TaskOut, api),
        (WarRoomOut, api),
        (MemberOut, api),
        (BlastRadiusOut, api),
        (BriefOut, api),
        (LifecycleOut, incidents),
        (IncidentOut, incidents),
        # Pinned after a real miss: the cockpit typed a `chip` field the API
        # never served and rendered nothing, because nothing checked.
        (ChangeWindowOut, incidents),
        (ChangeWindowItemOut, incidents),
    ):
        for field in model.model_fields:
            assert f"{field}:" in source, (
                f"{model.__name__}.{field} is served by the API and has no type "
                "in the cockpit - run through features/*/api.ts"
            )
