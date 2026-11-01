"""S2-T04: timeline primitives and the S21 settings shell (REQ-023, REQ-025).

Structural rules, in the same spirit as the S0 and S1 web tests: thin routes,
logic in features, everything through the typed cookie proxy, no token or
tenant handling in browser code, and the timeline's contract shape matching the
frozen envelope rather than a view model invented for the screen.
"""

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
WEB = ROOT / "web"


@pytest.mark.req("REQ-023")
def test_timeline_and_settings_files_exist():
    for path in [
        "app/settings/page.tsx",
        "features/timeline/api.ts",
        "features/timeline/store.ts",
        "features/timeline/hooks/useTimeline.ts",
        "features/timeline/hooks/useVirtualRows.ts",
        "features/timeline/components/EventTimeline.tsx",
        "features/timeline/components/EventRow.tsx",
        "features/timeline/components/TopicFilter.tsx",
        "features/timeline/components/SeqAnchorBar.tsx",
        "features/platform/api.ts",
        "features/platform/hooks/usePlatformSettings.ts",
        "features/platform/components/SettingsScreen.tsx",
        "features/platform/components/PlatformHealthPanel.tsx",
        "features/platform/components/DeadLettersPanel.tsx",
    ]:
        assert (WEB / path).is_file(), f"missing web/{path}"


@pytest.mark.req("REQ-023")
def test_settings_route_is_thin():
    source = (WEB / "app" / "settings" / "page.tsx").read_text(encoding="utf-8")
    assert "features/platform" in source
    assert "fetch(" not in source and "useState" not in source


@pytest.mark.req("REQ-023", "REQ-025")
def test_all_data_goes_through_the_typed_cookie_proxy():
    for feature in ("timeline", "platform"):
        api = (WEB / "features" / feature / "api.ts").read_text(encoding="utf-8")
        assert "proxyApi" in api, f"{feature} must use the cookie proxy"
        assert "fetch(" not in api, f"{feature} must not call fetch directly"
        assert "API_BASE_URL" not in api, (
            f"{feature} must not talk to the API origin from the browser"
        )
    for source_file in (WEB / "features" / "timeline").rglob("*.ts*"):
        text = source_file.read_text(encoding="utf-8")
        assert "localStorage" not in text and "access_token" not in text


@pytest.mark.req("REQ-023")
def test_timeline_types_track_the_frozen_envelope():
    """If the envelope contract gains a field the cockpit must decode it, not a
    reshaped copy of it."""
    from signalforge.contracts.envelope import EventEnvelope

    api = (WEB / "features" / "timeline" / "api.ts").read_text(encoding="utf-8")
    for field in EventEnvelope.model_fields:
        assert f"{field}:" in api, f"EventEnvelope.{field} missing from the web type"


@pytest.mark.req("REQ-023")
def test_the_event_list_is_virtualised_and_anchored():
    virtual = (WEB / "features" / "timeline" / "hooks" / "useVirtualRows.ts").read_text(
        encoding="utf-8"
    )
    assert "scrollTop" in virtual and "padTop" in virtual and "padBottom" in virtual
    assert "scrollToIndex" in virtual, "the seq anchor needs a direct jump"

    timeline = (
        WEB / "features" / "timeline" / "components" / "EventTimeline.tsx"
    ).read_text(encoding="utf-8")
    assert "slice(rows.first, rows.last)" in timeline, "only the window is mounted"
    assert "TopicFilter" in timeline and "SeqAnchorBar" in timeline


@pytest.mark.req("REQ-021", "REQ-023")
def test_the_timeline_store_dedupes_and_orders_by_seq():
    """At-least-once delivery reaches the browser too: replay pages and the
    socket can both deliver the same event."""
    store = (WEB / "features" / "timeline" / "store.ts").read_text(encoding="utf-8")
    assert "event_id" in store and "Map(" in store, "dedupe by event id"
    assert "(a.seq ?? 0) - (b.seq ?? 0)" in store, "ordered by recording order"
    assert "anchorSeq" in store


@pytest.mark.req("REQ-025")
def test_settings_shell_has_the_platform_health_and_dead_letter_panes():
    screen = (
        WEB / "features" / "platform" / "components" / "SettingsScreen.tsx"
    ).read_text(encoding="utf-8")
    assert "Platform health" in screen and "Dead letters" in screen
    assert "PlatformHealthPanel" in screen and "DeadLettersPanel" in screen

    dead_letters = (
        WEB / "features" / "platform" / "components" / "DeadLettersPanel.tsx"
    ).read_text(encoding="utf-8")
    assert "requeueDeadLetter" in dead_letters, "requeue is the operator action"

    health = (
        WEB / "features" / "platform" / "components" / "PlatformHealthPanel.tsx"
    ).read_text(encoding="utf-8")
    assert "drill clock" in health, "a virtual clock must be visible, never implied"
    assert "head_seq" in health and "dead_letters" in health


@pytest.mark.req("REQ-012")
def test_settings_is_guarded_by_the_org_role_group():
    middleware = (WEB / "middleware.ts").read_text(encoding="utf-8")
    assert '"/settings"' in middleware
    assert "org_owner" in middleware
    assert "UX layer only" in middleware, (
        "the guard must stay documented as UX over the API's real enforcement"
    )


@pytest.mark.req("REQ-021")
def test_the_virtual_window_attaches_its_scroll_listener_to_the_list_once_it_exists():
    """The list renders only after the timeline has loaded, which is after a
    mount-time effect has run. A `useRef` read in an effect with no
    dependencies saw null once and never looked again, so the window stayed on
    the first rows however far the person scrolled."""
    virtual = (WEB / "features" / "timeline" / "hooks" / "useVirtualRows.ts").read_text(
        encoding="utf-8"
    )
    assert "useState<HTMLDivElement | null>" in virtual, "the element is state"
    assert "useCallback((node: HTMLDivElement | null)" in virtual, "and the ref a callback"
    assert "}, [element]);" in virtual, "the listener effect re-runs when the element arrives"
    listener_effect = virtual.split("useEffect(() => {")[1].split("}, [")[1]
    assert listener_effect.startswith("element]"), "and depends on the element, not on nothing"
