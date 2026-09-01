"""Duplicates and reconciliation across the lifecycle (REQ-043, REQ-044).

Written failing-first. Four things happen twice in every real incident and each
one has to be harmless:

- **two people press Declare.** Seconds apart, on the same application, because
  that is what a pager does to a rota. One incident, and the second caller is
  told which one to join.
- **a severity is re-submitted.** A screen re-posts what is already true; that
  must not become a second transition, a second event and a second ticket
  update, or the timeline of a long incident becomes unreadable.
- **a deployment is announced twice.** A re-triggered pipeline says
  `deployment.started` again. One change window.
- **a ticket create whose outcome is unknown is retried.** Reconcile before
  retry: ask the ITSM whether the ticket exists before making a second one.
"""

from __future__ import annotations

import pytest

from signalforge.tests.incidents.conftest import (
    APP_ID,
    Sender,
    announce,
    change_window,
    close_incident,
    commander,
    declare,
    issue_key,
    onboard,
    owner,
    read_incident,
    set_severity,
    set_state,
    topics,
)

pytestmark = pytest.mark.anyio


@pytest.mark.req("REQ-043")
async def test_a_second_declaration_joins_the_first_rather_than_opening_a_second(api):
    """Written failing-first. Two incident commanders, one application, one
    incident - and the refusal names the incident to join."""
    owner_token, _tenant = owner(api)
    onboard(api, owner_token)
    first = commander(api, owner_token, email="ic1@acme.io")
    second = commander(api, owner_token, email="ic2@acme.io")

    incident = declare(api, first["tokens"]["access_token"], severity="P1")
    refused = declare(
        api, second["tokens"]["access_token"], severity="P0", expect=409
    )
    assert incident["reference"] in refused["detail"]
    assert "already" in refused["detail"]

    listing = api.client.get(
        "/incidents", headers=api.auth_header(owner_token)
    ).json()
    assert listing["count"] == 1

    # Closing it frees the application: the next real event gets its own record.
    close_incident(api, first["tokens"]["access_token"], incident["id"], reason="done")
    reopened = declare(api, second["tokens"]["access_token"], severity="P0")
    assert reopened["id"] != incident["id"]
    assert reopened["reference"] != incident["reference"]


@pytest.mark.req("REQ-043")
async def test_setting_the_severity_that_is_already_set_changes_nothing(api):
    owner_token, _tenant = owner(api)
    onboard(api, owner_token)
    ic = commander(api, owner_token)
    token = ic["tokens"]["access_token"]

    incident = declare(api, token, severity="P1")
    set_severity(api, token, incident["id"], "P0", reason="worse than we thought")
    api.clock.advance(seconds=30)
    unchanged = set_severity(api, token, incident["id"], "P0")
    assert unchanged["severity"] == "P0"

    changes = topics(api, owner_token, "incident.severity.changed")
    assert len(changes) == 1, "a no-op is not a decision and does not get an event"
    detail = read_incident(api, token, incident["id"])
    # A declaration has no severity_before; a re-grade does. One re-grade.
    severity_moves = [
        row
        for row in detail["transitions"]
        if row["severity_before"] is not None
        and row["severity_before"] != row["severity_after"]
    ]
    assert len(severity_moves) == 1


@pytest.mark.req("REQ-043")
async def test_moving_to_a_state_it_is_already_in_is_refused_not_duplicated(api):
    owner_token, _tenant = owner(api)
    onboard(api, owner_token)
    ic = commander(api, owner_token)
    token = ic["tokens"]["access_token"]
    incident = declare(api, token, severity="P2")

    set_state(api, token, incident["id"], "recovering", reason="rolled back")
    again = set_state(api, token, incident["id"], "recovering", expect=409)
    assert "already recovering" in again["detail"]
    assert len(topics(api, owner_token, "incident.state.changed")) == 1


@pytest.mark.req("REQ-044", "REQ-032")
async def test_the_same_deployment_announced_twice_is_one_change_window(api):
    """A re-triggered pipeline is not a second deployment. The ingest ledger
    stops the duplicate `event_id`, and the projection folds the rest."""
    owner_token, _tenant = owner(api)
    onboard(api, owner_token)
    sender = Sender(
        api=api, key=issue_key(api, owner_token, stream="changes", app_id=APP_ID)
    )

    first = await announce(api, sender, change_id="release-v12", service=APP_ID)
    assert first["accepted"] == 1
    window = change_window(api, owner_token)
    assert window["open"] is True
    assert len(window["windows"]) == 1

    # A different event id for the same change: accepted by ingest, folded by
    # the projection into the window that already exists.
    second = await announce(api, sender, change_id="release-v12", service=APP_ID)
    assert second["accepted"] == 1
    window = change_window(api, owner_token)
    assert len(window["windows"]) == 1, "one change_id is one window"
    assert window["windows"][0]["change_id"] == "release-v12"

    # And a redelivery of an event already consumed changes nothing either.
    await api.container.dispatcher.run_once()
    assert len(change_window(api, owner_token)["windows"]) == 1


@pytest.mark.req("REQ-044")
async def test_a_completion_that_arrives_before_its_start_still_closes_the_window(api):
    """Out-of-order delivery. A window rebuilt from arrival order would reopen a
    finished deployment; this one is folded from the events' own fields."""
    owner_token, _tenant = owner(api)
    onboard(api, owner_token)
    sender = Sender(
        api=api, key=issue_key(api, owner_token, stream="changes", app_id=APP_ID)
    )

    started_at = api.clock.now().isoformat()
    await announce(
        api,
        sender,
        change_id="release-v13",
        status="completed",
        service=APP_ID,
        started_at=started_at,
        completed_at=api.clock.now().isoformat(),
    )
    closed = change_window(api, owner_token)
    assert closed["open"] is False
    assert len(closed["recent"]) == 1 and closed["windows"] == []

    await announce(
        api, sender, change_id="release-v13", status="started",
        service=APP_ID, started_at=started_at,
    )
    window = change_window(api, owner_token)
    assert window["open"] is False, "a completion, once seen, is never unseen"
    assert len(window["recent"]) == 1 and window["windows"] == []
    assert window["recent"][0]["announced_started"] is True, (
        "the late start is still recorded; it just does not reopen the window"
    )
