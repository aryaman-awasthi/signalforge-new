"""Change windows from real deploy-hook calls (REQ-044).

The acceptance clause is "the change-window chip appears from a real deploy hook
call", so every test that can be end to end is: a signed batch at the frozen
ingest path, the real bus sweep, the real projection, and the API a screen reads.
The pure-domain tests at the bottom cover the two folds a live pipeline is
unlikely to produce on demand - a change that never completes, and one whose
events arrive in the wrong order.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from signalforge.contracts.canonical import ChangeEvent, ChangeStatus, ChangeType
from signalforge.domain.incidents.changes import (
    CHANGE_WINDOW_MAX_S,
    merge_change,
    window_view,
)
from signalforge.tests.incidents.conftest import (
    APP_ID,
    Sender,
    announce,
    change_window,
    commander,
    declare,
    issue_key,
    lifecycle,
    onboard,
    owner,
    read_incident,
    relogin,
)

pytestmark = pytest.mark.anyio

AT = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)


def _sender(api, token: str, app_id: str = APP_ID) -> Sender:
    return Sender(api=api, key=issue_key(api, token, stream="changes", app_id=app_id))


@pytest.mark.req("REQ-044", "REQ-032")
async def test_a_deploy_hook_that_starts_and_never_completes_opens_the_chip(api):
    owner_token, _tenant = owner(api)
    onboard(api, owner_token)
    sender = _sender(api, owner_token)

    assert change_window(api, owner_token)["open"] is False

    await announce(
        api,
        sender,
        change_id="release-v12",
        service=APP_ID,
        version="v12",
        actor="pipeline@ci",
    )

    window = change_window(api, owner_token)
    assert window["open"] is True, "started with no completion is the chip"
    assert len(window["windows"]) == 1
    entry = window["windows"][0]
    assert entry["change_id"] == "release-v12"
    assert entry["version"] == "v12"
    assert entry["actor"] == "pipeline@ci"
    assert entry["announced_started"] is True
    assert entry["announced_completed"] is False
    assert entry["stale"] is False
    assert "still running" in entry["detail"]
    assert "1 change(s) in flight" in window["detail"]

    # The same fact on the operator's own screen.
    view = lifecycle(api, owner_token)
    assert view["change_window"]["open"] is True


@pytest.mark.req("REQ-044")
async def test_the_completion_closes_the_window(api):
    owner_token, _tenant = owner(api)
    onboard(api, owner_token)
    sender = _sender(api, owner_token)

    started_at = api.clock.now().isoformat()
    await announce(api, sender, change_id="release-v12", service=APP_ID,
                   started_at=started_at)
    api.clock.advance(seconds=90)
    await announce(
        api,
        sender,
        change_id="release-v12",
        status="completed",
        service=APP_ID,
        started_at=started_at,
        completed_at=api.clock.now().isoformat(),
    )

    window = change_window(api, owner_token)
    assert window["open"] is False
    assert window["windows"] == []
    assert len(window["recent"]) == 1
    assert window["recent"][0]["duration_s"] == 90.0
    assert "completed after 90s" in window["recent"][0]["detail"]


@pytest.mark.req("REQ-044")
async def test_a_rollback_ends_the_window_and_says_which_ending_it_was(api):
    """Failed and rolled_back end the window too - and are *more* interesting as
    evidence, which is why the ending is recorded rather than collapsed."""
    owner_token, _tenant = owner(api)
    onboard(api, owner_token)
    sender = _sender(api, owner_token)

    started_at = api.clock.now().isoformat()
    await announce(api, sender, change_id="release-v12", service=APP_ID,
                   started_at=started_at)
    await announce(
        api,
        sender,
        change_id="release-v12",
        status="rolled_back",
        service=APP_ID,
        started_at=started_at,
    )
    window = change_window(api, owner_token)
    assert window["open"] is False
    assert window["recent"][0]["status"] == "rolled_back"


@pytest.mark.req("REQ-044")
async def test_a_pipeline_that_stopped_talking_goes_stale_rather_than_staying_open(api):
    """Silence is the third thing a pipeline does. Nobody knows whether the
    deploy landed, so the window is not closed on a guess - but a chip that has
    been lit for three days is a chip people stop seeing."""
    owner_token, _tenant = owner(api)
    onboard(api, owner_token)
    sender = _sender(api, owner_token)
    await announce(api, sender, change_id="release-v12", service=APP_ID)

    assert change_window(api, owner_token)["open"] is True
    api.clock.advance(seconds=CHANGE_WINDOW_MAX_S + 60)
    # Four hours later the operator's access token has expired, exactly as it
    # should have. They log in again; the window is still where they left it.
    owner_token = relogin(api)

    window = change_window(api, owner_token)
    assert window["open"] is False, "the chip goes out"
    assert window["windows"] == []
    assert len(window["stale"]) == 1
    assert window["stale"][0]["open"] is True, "still unfinished, just not believed"
    assert "never announced completion" in window["stale"][0]["detail"]
    assert "never completed" in window["detail"]


@pytest.mark.req("REQ-044", "REQ-043")
async def test_what_was_changing_is_copied_onto_the_incident(api):
    """"What changed" is the first question during an incident, and the answer
    has to survive the deployment finishing."""
    owner_token, _tenant = owner(api)
    onboard(api, owner_token)
    sender = _sender(api, owner_token)
    started_at = api.clock.now().isoformat()
    await announce(api, sender, change_id="release-v12", service=APP_ID,
                   version="v12", started_at=started_at)

    ic = commander(api, owner_token)
    token = ic["tokens"]["access_token"]
    incident = declare(api, token, severity="P0")
    assert [row["change_id"] for row in incident["change_windows"]] == ["release-v12"]
    assert incident["change_windows"][0]["version"] == "v12"

    # The deployment finishes; the incident still records what was in flight.
    await announce(
        api,
        sender,
        change_id="release-v12",
        status="completed",
        service=APP_ID,
        started_at=started_at,
        completed_at=api.clock.now().isoformat(),
    )
    assert change_window(api, owner_token)["open"] is False
    assert read_incident(api, token, incident["id"])["change_windows"][0][
        "change_id"
    ] == "release-v12"


@pytest.mark.req("REQ-044", "REQ-013")
async def test_a_change_window_belongs_to_one_application_of_one_tenant(api):
    owner_token, _tenant = owner(api)
    onboard(api, owner_token, app_id=APP_ID)
    onboard(api, owner_token, app_id="settlement")
    sender = _sender(api, owner_token, app_id=APP_ID)
    await announce(api, sender, change_id="release-v12", service=APP_ID)

    assert change_window(api, owner_token, APP_ID)["open"] is True
    assert change_window(api, owner_token, "settlement")["open"] is False
    # An application this tenant does not have answers 404, not an empty window.
    change_window(api, owner_token, "nothing-here", expect=404)


# --- the pure fold ------------------------------------------------------------


def _event(status: str = "started", **overrides) -> ChangeEvent:
    document = {
        "change_id": "release-v12",
        "change_type": ChangeType.DEPLOYMENT,
        "service": "orders",
        "status": ChangeStatus(status),
        "started_at": AT,
        "version": "v12",
    }
    document.update(overrides)
    return ChangeEvent(**document)


def _merge(existing, event, at=AT):
    return merge_change(
        existing, event, tenant_id="t", app_id="a", at=at, source_system="ci"
    )


@pytest.mark.req("REQ-044")
def test_folding_the_same_event_twice_produces_an_equal_window():
    once = _merge(None, _event())
    twice = _merge(once, _event())
    assert once == twice, "at-least-once delivery has to be harmless"


@pytest.mark.req("REQ-044")
def test_a_late_start_cannot_reopen_a_completed_change():
    completed = _merge(None, _event("completed", completed_at=AT + timedelta(seconds=30)))
    reordered = _merge(completed, _event("started"))
    assert reordered.announced_completed is True
    assert reordered.open is False
    assert reordered.announced_started is True
    assert reordered.completed_at == AT + timedelta(seconds=30)


@pytest.mark.req("REQ-044")
def test_the_earliest_start_wins_so_a_duplicate_cannot_move_the_age():
    first = _merge(None, _event())
    later = _merge(first, _event(started_at=AT + timedelta(minutes=10)))
    assert later.started_at == AT, "staleness is measured from the real start"


@pytest.mark.req("REQ-044")
def test_the_view_splits_in_flight_from_stale_from_finished():
    at = AT + timedelta(hours=5)
    stale = _merge(None, _event())
    live = _merge(None, _event(change_id="release-v13", started_at=at - timedelta(minutes=2)))
    done = _merge(
        None,
        _event(
            "completed",
            change_id="release-v11",
            started_at=at - timedelta(minutes=30),
            completed_at=at - timedelta(minutes=29),
        ),
    )
    view = window_view([stale, live, done], app_id="a", at=at)
    assert [w.change_id for w in view.open] == ["release-v13"]
    assert [w.change_id for w in view.stale] == ["release-v12"]
    assert [w.change_id for w in view.recent] == ["release-v11"]
    assert view.change_window_open is True
