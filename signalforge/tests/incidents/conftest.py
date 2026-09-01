"""Fixtures for the incident lifecycle suite (S4-T02).

Every test here drives the real API. An incident is declared by a real session
holding `declare_incident` from the frozen roles matrix, against an application
registered through `POST /apps`; a deployment reaches the change window by being
posted to the frozen ingest path with a real HMAC and then dispatched over the
real bus. Nothing constructs an incident, a transition or a change window by
hand except the pure-domain tests, which say so.

The clock is frozen and advanced explicitly, because half of what this sprint
builds is about *time*: a change window that has been open for four hours is
stale, and the only honest way to test that is to move the clock.
"""

from __future__ import annotations

from typing import Any, Optional

import pytest

from signalforge.tests.ingest.conftest import (  # noqa: F401  (helpers re-exported)
    Sender,
    batch,
    change,
    issue_key,
)
from signalforge.tests.onboarding.conftest import register_app

APP_ID = "payments"


def owner(api) -> tuple[str, str]:
    """Sign up and return (access token, tenant id). The org owner holds
    manage_apps and nothing else - declaring is an incident-command job."""
    signed = api.signup()
    return signed["tokens"]["access_token"], signed["org"]["id"]


def relogin(
    api,
    *,
    org_slug: str = "acme-corp",
    email: str = "owner@acme.io",
    password: str = "a-strong-password",
) -> str:
    """A fresh access token.

    Needed by any test that advances the clock past the 15-minute access TTL -
    which is every test about a change window going stale, because staleness is
    measured in hours. The session security is doing exactly what it should; the
    test just has to log in again like a person would.
    """
    response = api.client.post(
        "/auth/login",
        json={"org_slug": org_slug, "email": email, "password": password},
    )
    assert response.status_code == 200, response.text
    return response.json()["tokens"]["access_token"]


def commander(api, owner_token: str, *, email: str = "ic@acme.io", app_id: Optional[str] = None):
    """An incident commander: declare_incident, change_severity, close_incident."""
    return api.invite_and_accept(
        owner_token,
        email=email,
        roles=["incident_commander"],
        app_id=app_id,
        display_name="Ida Commander",
        password="ic-strong-password",
    )


def onboard(api, token: str, *, app_id: str = APP_ID) -> dict:
    return register_app(api, token, app_id=app_id, name=app_id.title())


def declare(
    api,
    token: str,
    *,
    app_id: str = APP_ID,
    expect: int = 201,
    **body: Any,
) -> dict:
    payload: dict[str, Any] = {"title": "Payments failing in both regions"}
    payload.update(body)
    response = api.client.post(
        f"/incidents?app_id={app_id}", json=payload, headers=api.auth_header(token)
    )
    assert response.status_code == expect, response.text
    return response.json()


def read_incident(api, token: str, incident_id: str, *, expect: int = 200) -> dict:
    response = api.client.get(
        f"/incidents/{incident_id}", headers=api.auth_header(token)
    )
    assert response.status_code == expect, response.text
    return response.json()


def list_incidents(api, token: str, *, expect: int = 200, **params: Any) -> dict:
    query = "&".join(f"{k}={v}" for k, v in params.items() if v is not None)
    response = api.client.get(
        f"/incidents?{query}" if query else "/incidents",
        headers=api.auth_header(token),
    )
    assert response.status_code == expect, response.text
    return response.json()


def set_severity(
    api, token: str, incident_id: str, severity: str, *, expect: int = 200, **body: Any
) -> dict:
    response = api.client.patch(
        f"/incidents/{incident_id}/severity",
        json={"severity": severity, **body},
        headers=api.auth_header(token),
    )
    assert response.status_code == expect, response.text
    return response.json()


def set_state(
    api, token: str, incident_id: str, state: str, *, expect: int = 200, **body: Any
) -> dict:
    response = api.client.post(
        f"/incidents/{incident_id}/state",
        json={"state": state, **body},
        headers=api.auth_header(token),
    )
    assert response.status_code == expect, response.text
    return response.json()


def close_incident(
    api, token: str, incident_id: str, *, expect: int = 200, **body: Any
) -> dict:
    response = api.client.post(
        f"/incidents/{incident_id}/close",
        json=dict(body) or {},
        headers=api.auth_header(token),
    )
    assert response.status_code == expect, response.text
    return response.json()


def resync(api, token: str, incident_id: str, *, expect: int = 200) -> dict:
    response = api.client.post(
        f"/incidents/{incident_id}/itsm-sync", json={}, headers=api.auth_header(token)
    )
    assert response.status_code == expect, response.text
    return response.json()


def lifecycle(api, token: str, app_id: str = APP_ID, *, expect: int = 200) -> dict:
    response = api.client.get(
        f"/apps/{app_id}/lifecycle", headers=api.auth_header(token)
    )
    assert response.status_code == expect, response.text
    return response.json()


def change_window(api, token: str, app_id: str = APP_ID, *, expect: int = 200) -> dict:
    response = api.client.get(
        f"/apps/{app_id}/change-window", headers=api.auth_header(token)
    )
    assert response.status_code == expect, response.text
    return response.json()


def topics(api, token: str, *names: str) -> list[dict]:
    query = "&".join(f"topics={name}" for name in names)
    response = api.client.get(
        f"/events?{query}&limit=200", headers=api.auth_header(token)
    )
    assert response.status_code == 200, response.text
    return response.json()["events"]


async def announce(
    api,
    sender: "Sender",
    *,
    change_id: str = "release-v12",
    status: str = "started",
    dispatch: bool = True,
    **overrides: Any,
) -> dict:
    """One deployment announcement, exactly as a pipeline makes it: a signed
    batch at the frozen ingest path, then the bus sweep that projects it.

    Timestamps come from the frozen clock rather than wall time, because a change
    window's whole meaning is its age: a document stamped `now()` in real time
    would be hours old against a frozen clock and would read as stale, which is
    the correct behaviour and the wrong test.
    """
    overrides.setdefault("started_at", api.clock.now().isoformat())
    document = change(change_id, status=status, **overrides)
    document["_occurred_at"] = api.clock.now().isoformat()
    response = sender.post(batch(document))
    assert response.status_code == 202, response.text
    if dispatch:
        await api.container.dispatcher.run_once()
    return response.json()


@pytest.fixture
def anyio_backend():
    return "asyncio"
