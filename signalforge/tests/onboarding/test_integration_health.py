"""S3-T05: what the S23 integration health screen actually reads (REQ-036, REQ-032).

S3-T04 and S3-T03 both closed with the same open item: a push binding's row on
`GET /connectors/health` was computed from the binding alone, so it reported the
last time SignalForge *checked itself* and never the last time anything actually
arrived. A push binding cannot be measured by pinging - SignalForge is the
server, so it is reachable whether or not the sender died a week ago - and the
number that answers "is this integration alive" lives on the ingest key.

This file is the join: one row per binding, carrying its own direction, its own
freshness for a pull, and the key-side freshness, counters and last refusal for
a push. Written before the join existed and run against the API without it.
"""

from __future__ import annotations

import pytest

from signalforge.tests.adapters.conftest import connect, observe  # noqa: F401
from signalforge.tests.ingest.conftest import (
    Sender,
    advance,
    alert,
    batch,
    issue_key,
)
from signalforge.tests.onboarding.conftest import APP_ID, owner, register_app

PUSH_ADAPTER = "grafana-alert-webhook"


def health(api, token, **params) -> list[dict]:
    response = api.client.get(
        "/connectors/health", params=params, headers=api.auth_header(token)
    )
    assert response.status_code == 200, response.text
    return response.json()


def bind_push(api, token) -> str:
    return connect(api, token, PUSH_ADAPTER, app_id=APP_ID)["id"]


@pytest.mark.req("REQ-036", "REQ-032")
def test_a_push_row_reports_when_something_last_arrived(api):
    """The screen's "last push" column. Until traffic arrives the row is honest
    about having measured nothing, rather than green because a server answered
    its own ping."""
    token, _tenant_id = owner(api)
    register_app(api, token)
    binding_id = bind_push(api, token)
    key = issue_key(
        api, token, stream="alerts", app_id=APP_ID, binding_id=binding_id
    )

    row = health(api, token)[0]
    assert row["direction"] == "inbound (the app posts)"
    assert row["ingest_keys"] == 1
    assert row["ingest_paths"] == ["/api/v1/ingest/alerts"]
    assert row["last_event_at"] is None
    assert row["event_freshness_s"] is None, "nothing has been posted yet"
    assert row["ingest_accepted"] == 0

    Sender(api=api, key=key).post(batch(alert()))
    advance(api, minutes=5)

    row = health(api, token)[0]
    assert row["ingest_accepted"] == 1
    assert row["last_event_at"] is not None
    assert row["event_freshness_s"] == 300.0
    assert row["ingest_last_error"] is None


@pytest.mark.req("REQ-036", "REQ-032")
def test_a_refused_post_is_readable_on_the_row_that_owns_it(api):
    """The sender only ever sees a bare 401. The operator reading the health
    screen is the one person who is allowed to know why."""
    token, _tenant_id = owner(api)
    register_app(api, token)
    binding_id = bind_push(api, token)
    sender = Sender(
        api=api,
        key=issue_key(
            api, token, stream="alerts", app_id=APP_ID, binding_id=binding_id
        ),
    )

    refused = sender.post(batch(alert()), secret="not-the-shared-secret")
    assert refused.status_code == 401

    row = health(api, token)[0]
    assert row["ingest_rejected"] == 1
    assert row["ingest_last_error"] == "signature mismatch"
    assert row["ingest_accepted"] == 0


@pytest.mark.req("REQ-036")
def test_a_pull_row_keeps_its_own_direction_and_check_freshness(api, observe):
    """The join must not change what a pull row means: its freshness is when
    SignalForge last read the integrated system successfully, and it has no
    ingest side at all."""
    token, _tenant_id = owner(api)
    register_app(api, token)
    connect(api, token, "http-observe-metrics", app_id=APP_ID)
    api.client.post(
        f"/apps/{APP_ID}/connection-tests", json={}, headers=api.auth_header(token)
    )

    advance(api, seconds=45)
    row = health(api, token)[0]
    assert row["direction"] == "inbound (SignalForge polls)"
    assert row["status"] == "up"
    assert row["freshness_s"] == 45.0
    assert row["ingest_keys"] == 0
    assert row["last_event_at"] is None
    assert row["ingest_paths"] == []
    assert row["simulated"] is False


@pytest.mark.req("REQ-036", "REQ-033")
def test_a_simulated_integration_says_so_on_its_own_row(api):
    """The SIMULATED column. A simulated integration that reads like a real one
    is the single most dangerous thing a demo platform can do."""
    token, _tenant_id = owner(api)
    register_app(api, token)
    connect(api, token, "itsm-lite", app_id=APP_ID)

    row = health(api, token)[0]
    assert row["simulated"] is True
    assert row["direction"] == "outbound (SignalForge calls)"


@pytest.mark.req("REQ-036", "REQ-032")
def test_revoking_a_key_takes_it_off_the_binding_row(api):
    """Human override mid-flow: the operator pulls the credential and the row
    stops claiming a live sender on the very next read, while what was already
    accepted stays on the key's own record."""
    token, _tenant_id = owner(api)
    register_app(api, token)
    binding_id = bind_push(api, token)
    key = issue_key(
        api, token, stream="alerts", app_id=APP_ID, binding_id=binding_id
    )
    Sender(api=api, key=key).post(batch(alert()))
    assert health(api, token)[0]["ingest_keys"] == 1

    api.client.delete(
        f"/ingest-keys/{key['id']}", headers=api.auth_header(token)
    )

    row = health(api, token)[0]
    assert row["ingest_keys"] == 0
    assert row["last_event_at"] is None, "no live key, so nothing is being measured"
    assert row["ingest_paths"] == []


@pytest.mark.req("REQ-036", "REQ-013")
def test_another_tenants_key_never_lands_on_this_tenants_row(api):
    """Two tenants, the same app_id, the same adapter. The join is a second
    place a tenant predicate has to hold, so it is asserted here rather than
    assumed from the repository."""
    first_token, _first = owner(api)
    register_app(api, first_token)
    first_binding = bind_push(api, first_token)
    issue_key(
        api, first_token, stream="alerts", app_id=APP_ID, binding_id=first_binding
    )

    second = api.signup(org="Rival", email="owner@rival.io")
    second_token = second["tokens"]["access_token"]
    register_app(api, second_token)
    bind_push(api, second_token)

    rows = health(api, second_token)
    assert len(rows) == 1
    assert rows[0]["ingest_keys"] == 0, "another tenant's key is not this row's"
    assert rows[0]["id"] != first_binding


@pytest.mark.req("REQ-036")
def test_the_screen_can_ask_for_one_application(api):
    token, _tenant_id = owner(api)
    register_app(api, token)
    register_app(api, token, app_id="ledger", name="Ledger")
    bind_push(api, token)
    connect(api, token, "itsm-lite", app_id="ledger")

    assert [row["app_id"] for row in health(api, token, app_id="ledger")] == ["ledger"]
    assert len(health(api, token)) == 2
