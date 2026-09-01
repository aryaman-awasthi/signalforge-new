"""Fixtures for the war-room suite (S4-T03).

Every test here drives the real API. A war room is opened by declaring a real
incident through `POST /incidents` with a real session holding
`declare_incident`; pages are raised by the shipped paging code against real
notification connectors bound through `POST /connectors`; the bridge is the
shipped teams-sim collaboration adapter, reached through `ConnectorInvoker`.
Nothing constructs a room, a page or a task by hand except the pure-domain
tests, which say so.

The clock is frozen and advanced explicitly, because the whole of this task is
about *time*: a countdown that has run out, a page that went unanswered for five
minutes, a commitment due in ten. Advancing a frozen clock is the only honest way
to test any of it, and it is also what a drill does at 100x.
"""

from __future__ import annotations

from typing import Any, Optional

import pytest

from signalforge.tests.adapters.conftest import connect
from signalforge.tests.incidents.conftest import (  # noqa: F401  (re-exported)
    APP_ID,
    Sender,
    announce,
    close_incident,
    commander,
    declare,
    issue_key,
    onboard,
    owner,
    read_incident,
    relogin,
    set_state,
    topics,
)

PAGE_CHANNELS = ("notify-in-app", "page-sim")


def wire_paging(api, owner_token: str, *, app_id: str = APP_ID) -> None:
    """Bind the notification connectors a page actually goes out through.

    Two of them, deliberately: in-app is a real channel with a real socket
    behind it, and page-sim is simulated. A test that only ever bound the
    simulated one would not prove that a page fans out across channels.
    """
    for adapter_id in PAGE_CHANNELS:
        connect(api, owner_token, adapter_id, app_id=app_id)


def wire_bridge(api, owner_token: str, *, app_id: str = APP_ID, script=None) -> dict:
    """Bind the collaboration connector the war room's bridge runs on."""
    config: dict[str, Any] = {}
    if script is not None:
        config["script"] = script
    return connect(api, owner_token, "teams-sim", app_id=app_id, config=config)


def responder(
    api,
    owner_token: str,
    *,
    email: str,
    roles: tuple[str, ...] = ("sme",),
    display_name: str = "Sam Responder",
    app_id: Optional[str] = None,
):
    """Somebody who will be paged. An SME by default - the role the frozen matrix
    gives no permissions at all, which is the whole point of the ack path."""
    return api.invite_and_accept(
        owner_token,
        email=email,
        roles=list(roles),
        app_id=app_id,
        display_name=display_name,
        password="responder-strong-password",
    )


def rotate(
    api,
    owner_token: str,
    *user_ids: str,
    name: str = "payments primary",
    app_id: Optional[str] = APP_ID,
    expect: int = 201,
) -> dict:
    """A real on-call rotation through the real API. `manage_org` creates it, as
    the frozen matrix says, and the escalation ladder then reads it."""
    response = api.client.post(
        "/oncall/rotations",
        json={"name": name, "app_id": app_id, "user_ids": list(user_ids)},
        headers=api.auth_header(owner_token),
    )
    assert response.status_code == expect, response.text
    return response.json()


def war_room(api, token: str, incident_id: str, *, expect: int = 200) -> dict:
    response = api.client.get(
        f"/incidents/{incident_id}/war-room", headers=api.auth_header(token)
    )
    assert response.status_code == expect, response.text
    return response.json()


def join(api, token: str, incident_id: str, *, expect: int = 200, **body: Any) -> dict:
    response = api.client.post(
        f"/incidents/{incident_id}/war-room/join",
        json=dict(body),
        headers=api.auth_header(token),
    )
    assert response.status_code == expect, response.text
    return response.json()


def brief(api, token: str, incident_id: str, *, expect: int = 200) -> dict:
    response = api.client.get(
        f"/incidents/{incident_id}/brief", headers=api.auth_header(token)
    )
    assert response.status_code == expect, response.text
    return response.json()


def pages(api, token: str, incident_id: str, *, expect: int = 200) -> dict:
    response = api.client.get(
        f"/incidents/{incident_id}/pages", headers=api.auth_header(token)
    )
    assert response.status_code == expect, response.text
    return response.json()


def raise_page(
    api, token: str, incident_id: str, *, expect: int = 201, **body: Any
) -> dict:
    response = api.client.post(
        f"/incidents/{incident_id}/pages",
        json=dict(body),
        headers=api.auth_header(token),
    )
    assert response.status_code == expect, response.text
    return response.json()


def ack(
    api, token: str, incident_id: str, page_id: str, *, expect: int = 200, **body: Any
) -> dict:
    response = api.client.post(
        f"/incidents/{incident_id}/pages/{page_id}/ack",
        json=dict(body),
        headers=api.auth_header(token),
    )
    assert response.status_code == expect, response.text
    return response.json()


def escalate(
    api, token: str, incident_id: str, page_id: str, *, expect: int = 200
) -> dict:
    response = api.client.post(
        f"/incidents/{incident_id}/pages/{page_id}/escalate",
        json={},
        headers=api.auth_header(token),
    )
    assert response.status_code == expect, response.text
    return response.json()


def my_pages(api, token: str, *, expect: int = 200, **params: Any) -> dict:
    query = "&".join(f"{k}={v}" for k, v in params.items())
    response = api.client.get(
        f"/my/pages?{query}" if query else "/my/pages", headers=api.auth_header(token)
    )
    assert response.status_code == expect, response.text
    return response.json()


def read_bridge(api, token: str, incident_id: str, *, expect: int = 200) -> dict:
    response = api.client.get(
        f"/incidents/{incident_id}/bridge", headers=api.auth_header(token)
    )
    assert response.status_code == expect, response.text
    return response.json()


def say(api, token: str, incident_id: str, text: str, *, expect: int = 201) -> dict:
    response = api.client.post(
        f"/incidents/{incident_id}/bridge",
        json={"text": text},
        headers=api.auth_header(token),
    )
    assert response.status_code == expect, response.text
    return response.json()


def tasks(api, token: str, incident_id: str, *, expect: int = 200) -> dict:
    response = api.client.get(
        f"/incidents/{incident_id}/tasks", headers=api.auth_header(token)
    )
    assert response.status_code == expect, response.text
    return response.json()


def create_task(
    api, token: str, incident_id: str, *, expect: int = 201, **body: Any
) -> dict:
    response = api.client.post(
        f"/incidents/{incident_id}/tasks",
        json=dict(body),
        headers=api.auth_header(token),
    )
    assert response.status_code == expect, response.text
    return response.json()


def patch_task(
    api, token: str, incident_id: str, task_id: str, *, expect: int = 200, **body: Any
) -> dict:
    response = api.client.patch(
        f"/incidents/{incident_id}/tasks/{task_id}",
        json=dict(body),
        headers=api.auth_header(token),
    )
    assert response.status_code == expect, response.text
    return response.json()


def finish_task(
    api, token: str, incident_id: str, task_id: str, *, expect: int = 200, **body: Any
) -> dict:
    response = api.client.post(
        f"/incidents/{incident_id}/tasks/{task_id}/done",
        json=dict(body),
        headers=api.auth_header(token),
    )
    assert response.status_code == expect, response.text
    return response.json()


def page_for(room: dict, user_id: str) -> Optional[dict]:
    """The page addressed to one person, whatever state it reached."""
    for page in room["pages"]:
        if page["recipient"] == user_id:
            return page
    return None


@pytest.fixture
def anyio_backend():
    return "asyncio"
