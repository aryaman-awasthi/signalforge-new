"""Fixtures for the governance suite (S6).

Everything here drives the real API against the real container. An application
is registered with `POST /apps`, bound to the shipped observe adapters through
`POST /connectors`, and its actions are imported from the same ASGI test double
the adapter suite uses - one that implements only the frozen observe contract.
Nothing constructs a policy, an execution or an approval by hand except the
pure-domain tests, which say so.

Two people, always. Requesting a high-risk action and approving it are two acts
by two people, and `Approval.granted` refuses an approval by the person who
requested it whatever permissions they hold - so a suite with one commander
could not exercise the approval path at all.

The clock is frozen and advanced explicitly, because half of what this sprint
builds is about time: an approval that expired, a stability window that has not
been held long enough, an execution that has been uncertain for five minutes.
"""

from __future__ import annotations

from typing import Any, Optional

import pytest

from signalforge.tests.adapters.conftest import (  # noqa: F401  (re-exported)
    ObserveHarness,
    connect,
    default_config,
    observe,
)
from signalforge.tests.incidents.conftest import (  # noqa: F401  (re-exported)
    Sender,
    announce,
    batch,
    change,
    close_incident,
    commander,
    declare,
    issue_key,
    owner,
    read_incident,
    relogin,
    set_state,
    topics,
)
from signalforge.tests.onboarding.conftest import register_app

APP_ID = "checkout"

ROLLBACK_MANIFEST: dict[str, Any] = {
    "schema_version": 1,
    "actions": [
        {
            "action_id": "rollback-checkout",
            "name": "Roll back checkout",
            "description": "Roll the checkout deployment back to the previous release.",
            "action_class": "rollback",
            "risk": "high",
            "approver_roles": ["incident_commander"],
            "timeout_s": 120,
            "idempotent": False,
            "reversible": True,
            "reverse_action_id": "roll-forward-checkout",
            "preconditions": ["change_window_open"],
            "verification": ["checkout_health_up", "checkout_success_rate"],
            "params_schema": {
                "type": "object",
                "additionalProperties": False,
                "required": ["release"],
                "properties": {"release": {"type": "string", "minLength": 1}},
            },
        },
        {
            "action_id": "roll-forward-checkout",
            "name": "Roll forward checkout",
            "description": "Re-apply the release that was rolled back.",
            "action_class": "rollback",
            "risk": "high",
            "approver_roles": ["incident_commander"],
            "idempotent": False,
        },
        {
            "action_id": "collect-diagnostics",
            "name": "Collect diagnostics",
            "description": "Dump a heap profile and thread stacks.",
            "action_class": "diagnostic",
            "risk": "low",
            "idempotent": True,
        },
        {
            "action_id": "restart-checkout",
            "name": "Restart checkout",
            "description": "Rolling restart of the checkout pods.",
            "action_class": "restart",
            "risk": "high",
            "approver_roles": ["incident_commander", "tech_lead"],
            "idempotent": False,
        },
    ],
}
"""A manifest with a real rollback in it.

The shared observe double publishes a restart and a diagnostic; Sprint 6's
acceptance is about a rollback, so this suite replaces the manifest on the
double before importing. It is the application's own document either way - the
platform validates it against the frozen schema and imports what it says.
"""


@pytest.fixture
def governed(api, observe):  # noqa: F811  (the fixture is re-exported above)
    """An onboarded application with actions imported and two commanders.

    Returns a small record rather than a tuple, because a test that reads
    `gov.approver_token` is legible and one that reads `parts[3]` is not.
    """
    observe.state.actions = dict(ROLLBACK_MANIFEST)
    owner_token, tenant_id = owner(api)
    register_app(api, owner_token, app_id=APP_ID, name="Checkout")
    connect(api, owner_token, "http-observe-actions", app_id=APP_ID, scope="remediation")
    connect(api, owner_token, "http-observe-health", app_id=APP_ID, scope="read_only")
    imported = import_actions(api, owner_token)

    requester = commander(api, owner_token, email="ic@acme.io")
    approver = commander(api, owner_token, email="ic2@acme.io")
    return Governed(
        api=api,
        observe=observe,
        owner_token=owner_token,
        tenant_id=tenant_id,
        requester_token=requester["tokens"]["access_token"],
        requester_id=requester["user"]["id"],
        approver_token=approver["tokens"]["access_token"],
        approver_id=approver["user"]["id"],
        imported=imported,
    )


class Governed:
    def __init__(self, **fields: Any):
        self.__dict__.update(fields)

    def declare(self, **body: Any) -> dict:
        """A P0 declared by a person.

        The severity is named explicitly because this suite's application has no
        detection history - the detector proposes nothing, and the lifecycle
        rightly refuses a declaration that names no severity. Naming it is what
        an operator does when the detector has nothing to say, which is exactly
        the case Sprint 4 designed for.
        """
        body.setdefault("severity", "P0")
        body.setdefault("title", "Checkout failing for customers")
        return declare(self.api, self.requester_token, app_id=APP_ID, **body)


# --- request helpers ------------------------------------------------------------


def import_actions(api, token: str, app_id: str = APP_ID, *, expect: int = 200) -> dict:
    response = api.client.post(
        f"/apps/{app_id}/actions/import", json={}, headers=api.auth_header(token)
    )
    assert response.status_code == expect, response.text
    return response.json()


def get_policy(api, token: str, app_id: str = APP_ID, *, expect: int = 200) -> dict:
    response = api.client.get(
        f"/apps/{app_id}/policy", headers=api.auth_header(token)
    )
    assert response.status_code == expect, response.text
    return response.json()


def put_policy(
    api,
    token: str,
    source: str,
    app_id: str = APP_ID,
    *,
    expect: int = 201,
    **body: Any,
) -> dict:
    response = api.client.post(
        f"/apps/{app_id}/policy",
        json={"source": source, **body},
        headers=api.auth_header(token),
    )
    assert response.status_code == expect, response.text
    return response.json()


def dry_run_policy(
    api, token: str, source: str, app_id: str = APP_ID, *, expect: int = 200
) -> dict:
    response = api.client.post(
        f"/apps/{app_id}/policy/dry-run",
        json={"source": source},
        headers=api.auth_header(token),
    )
    assert response.status_code == expect, response.text
    return response.json()


def request_execution(
    api, token: str, incident_id: str, *, expect: int = 201, **body: Any
) -> dict:
    response = api.client.post(
        f"/incidents/{incident_id}/executions",
        json=body,
        headers=api.auth_header(token),
    )
    assert response.status_code == expect, response.text
    return response.json()


def act(
    api,
    token: str,
    incident_id: str,
    execution_id: str,
    what: str,
    *,
    expect: int = 200,
    **body: Any,
) -> dict:
    response = api.client.post(
        f"/incidents/{incident_id}/executions/{execution_id}/{what}",
        json=body or {},
        headers=api.auth_header(token),
    )
    assert response.status_code == expect, response.text
    return response.json()


def ledger(api, token: str, incident_id: str, *, expect: int = 200) -> dict:
    response = api.client.get(
        f"/incidents/{incident_id}/executions", headers=api.auth_header(token)
    )
    assert response.status_code == expect, response.text
    return response.json()


def next_best_actions(
    api, token: str, incident_id: str, *, explain: bool = False, expect: int = 200
) -> dict:
    response = api.client.get(
        f"/incidents/{incident_id}/actions?explain={'true' if explain else 'false'}",
        headers=api.auth_header(token),
    )
    assert response.status_code == expect, response.text
    return response.json()


def revise_plan(
    api, token: str, incident_id: str, *, expect: int = 201, **body: Any
) -> dict:
    response = api.client.post(
        f"/incidents/{incident_id}/plan", json=body, headers=api.auth_header(token)
    )
    assert response.status_code == expect, response.text
    return response.json()


def draft_communication(
    api, token: str, incident_id: str, *, expect: int = 201, **body: Any
) -> dict:
    body.setdefault("use_model", False)
    response = api.client.post(
        f"/incidents/{incident_id}/communications",
        json=body,
        headers=api.auth_header(token),
    )
    assert response.status_code == expect, response.text
    return response.json()


def communication_action(
    api,
    token: str,
    incident_id: str,
    communication_id: str,
    what: str,
    *,
    expect: int = 200,
    **body: Any,
) -> dict:
    response = api.client.post(
        f"/incidents/{incident_id}/communications/{communication_id}/{what}",
        json=body or {},
        headers=api.auth_header(token),
    )
    assert response.status_code == expect, response.text
    return response.json()


def communications(
    api, token: str, incident_id: str, *, expect: int = 200
) -> dict:
    response = api.client.get(
        f"/incidents/{incident_id}/communications", headers=api.auth_header(token)
    )
    assert response.status_code == expect, response.text
    return response.json()


def run_validation(
    api, token: str, incident_id: str, *, expect: int = 201, **body: Any
) -> dict:
    response = api.client.post(
        f"/incidents/{incident_id}/validation",
        json=body or {},
        headers=api.auth_header(token),
    )
    assert response.status_code == expect, response.text
    return response.json()


def validation_view(api, token: str, incident_id: str, *, expect: int = 200) -> dict:
    response = api.client.get(
        f"/incidents/{incident_id}/validation", headers=api.auth_header(token)
    )
    assert response.status_code == expect, response.text
    return response.json()


def graph_nodes(api, token: str, incident_id: str) -> list[dict]:
    response = api.client.get(
        f"/incidents/{incident_id}/brain", headers=api.auth_header(token)
    )
    assert response.status_code == 200, response.text
    return response.json()["nodes"]


STRICT_POLICY = """
approval_ttl_s: 600
stability_window_s: 120
stability_samples: 2
max_action_retries: 2
revalidation_cycles_before_escalation: 2
approver_roles: [incident_commander]
actions:
  collect-diagnostics:
    note: "read-only, runs on request"
"""

AUTO_ROLLBACK_POLICY = """
approval_ttl_s: 900
actions:
  rollback-checkout:
    auto_execute: true
"""
"""The document the platform must refuse. Sprint 6's critical test."""


@pytest.fixture
def anyio_backend():
    return "asyncio"
