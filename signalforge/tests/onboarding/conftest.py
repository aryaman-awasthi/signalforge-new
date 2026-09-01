"""Fixtures for the onboarding wizard suite (S3-T04).

Everything here goes through the real API. An application is registered with
POST /apps, integrations are bound with POST /connectors, and the application
the wizard then discovers, tests, maps and baselines is the same ASGI test double
the adapter suite uses - one that implements only the frozen observe contract.
That is the claim under test: serving the contract is sufficient to be onboarded,
and no code in the core knows which application it is.
"""

from __future__ import annotations

from typing import Any, Optional

from signalforge.tests.adapters.conftest import (  # noqa: F401  (fixtures re-exported)
    ObserveHarness,
    connect,
    default_config,
    observe,
)

APP_ID = "checkout"


def owner(api) -> tuple[str, str]:
    """Sign up and return (access token, tenant id)."""
    signed = api.signup()
    return signed["tokens"]["access_token"], signed["org"]["id"]


def register_app(
    api,
    token: str,
    *,
    app_id: str = APP_ID,
    name: str = "Checkout",
    base_url: Optional[str] = ObserveHarness.BASE_URL,
    expect: int = 201,
    **extra: Any,
) -> dict:
    body: dict[str, Any] = {"app_id": app_id, "name": name, **extra}
    if base_url is not None:
        body["base_url"] = base_url
    response = api.client.post("/apps", json=body, headers=api.auth_header(token))
    assert response.status_code == expect, response.text
    return response.json()


def put_mapping(api, token: str, app_id: str = APP_ID, *, expect: int = 200, **body) -> dict:
    response = api.client.put(
        f"/apps/{app_id}/mapping", json=body, headers=api.auth_header(token)
    )
    assert response.status_code == expect, response.text
    return response.json()


def import_actions(api, token: str, app_id: str = APP_ID, *, expect: int = 200) -> dict:
    response = api.client.post(
        f"/apps/{app_id}/actions/import", json={}, headers=api.auth_header(token)
    )
    assert response.status_code == expect, response.text
    return response.json()


def patch_action(
    api, token: str, action_id: str, app_id: str = APP_ID, *, expect: int = 200, **body
) -> dict:
    response = api.client.patch(
        f"/apps/{app_id}/actions/{action_id}", json=body, headers=api.auth_header(token)
    )
    assert response.status_code == expect, response.text
    return response.json()


def put_governance(api, token: str, app_id: str = APP_ID, *, expect: int = 200, **body) -> dict:
    response = api.client.put(
        f"/apps/{app_id}/governance", json=body, headers=api.auth_header(token)
    )
    assert response.status_code == expect, response.text
    return response.json()


def upload_topology(
    api, token: str, document: dict, app_id: str = APP_ID, *, expect: int = 200
) -> dict:
    response = api.client.put(
        f"/apps/{app_id}/topology",
        json={"document": document},
        headers=api.auth_header(token),
    )
    assert response.status_code == expect, response.text
    return response.json()


def topics(api, token: str, *names: str) -> list[dict]:
    query = "&".join(f"topics={name}" for name in names)
    response = api.client.get(f"/events?{query}&limit=200", headers=api.auth_header(token))
    assert response.status_code == 200, response.text
    return response.json()["events"]


SIMPLE_TOPOLOGY = {
    "captured_at": "2026-09-01T12:00:00+00:00",
    "nodes": [
        {"node_id": "web", "name": "Web", "node_type": "service", "region": "eu-west-1"},
        {"node_id": "db", "name": "Ledger", "node_type": "database"},
    ],
    "edges": [{"source": "web", "target": "db", "edge_type": "depends_on"}],
}

SIGNAL_BODY = {
    "signals": [
        {
            "key": "errors",
            "signal": "errors",
            "selector": 'http_requests_total{route="/checkout"}',
            "direction": "higher_is_worse",
            "unit": "count",
        },
        {
            "key": "payments-success",
            "signal": "business",
            "selector": "checkout_success_ratio",
            "direction": "lower_is_worse",
            "unit": "ratio",
            "business_counter": True,
        },
    ],
    "region_label": "region",
    "regions": ["eu-west-1", "us-east-1"],
    "journeys": [{"journey_id": "checkout", "name": "Checkout", "tier": 1}],
    "probes": [
        {
            "probe_id": "checkout-probe",
            "path": "/checkout",
            "journey_id": "checkout",
            "expected_status": 200,
        }
    ],
}
