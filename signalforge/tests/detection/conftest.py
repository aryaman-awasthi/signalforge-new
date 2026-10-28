"""Fixtures for the detection suite (S4-T01).

Every test here drives the *real* pipeline: an application registered through
POST /apps, a metrics connector bound through POST /connectors, a mapping put
through the wizard's own route, and then the shipped poller scraping the frozen
`/observe/metrics` contract from the same ASGI test double the adapter suite
uses. Nothing constructs a rollup by hand except the pure-domain tests, which
say so.

The clock is frozen and advanced explicitly. That is not a convenience: rollups
are bucketed on a five-second grid, so "advance five seconds and poll again" is
how a test writes the next bucket, and "poll twice without advancing" is how it
proves that two polls inside one bucket are one observation.
"""

from __future__ import annotations

from typing import Any, Optional, Sequence

import pytest

from signalforge.tests.adapters.conftest import (  # noqa: F401  (fixtures re-exported)
    ObserveHarness,
    connect,
    default_config,
    observe,
)
from signalforge.tests.onboarding.conftest import register_app

APP_ID = "payments"
REGION_A = "region-a"
REGION_B = "region-b"

STEADY_SUCCESS = 0.99
STEADY_ERRORS = 0.01
STEADY_SATURATION = 0.40

MAPPING: dict[str, Any] = {
    "signals": [
        {
            "key": "payments-success",
            "signal": "business",
            "selector": "payment_success_ratio",
            "direction": "lower_is_worse",
            "unit": "ratio",
            "business_counter": True,
        },
        {
            "key": "errors",
            "signal": "errors",
            "selector": "error_ratio",
            "direction": "higher_is_worse",
            "unit": "ratio",
        },
        {
            "key": "saturation",
            "signal": "saturation",
            "selector": "saturation_ratio",
            "direction": "higher_is_worse",
            "unit": "ratio",
        },
    ],
    "region_label": "region",
    "regions": [REGION_A, REGION_B],
    "journeys": [{"journey_id": "payments", "name": "Payments", "tier": 1}],
    "probes": [
        {
            "probe_id": "payments-probe",
            "path": "/checkout",
            "journey_id": "payments",
            "expected_status": 200,
        }
    ],
}

SLOS = [
    {
        "slo_id": "payments-availability",
        "name": "Payments availability",
        "objective_pct": 99.0,
        "window_days": 30,
        "journey_id": "payments",
        "indicator": "payments-success",
    }
]


def exposition(
    *,
    success: Optional[dict[str, float]] = None,
    errors: Optional[dict[str, float]] = None,
    saturation: Optional[dict[str, float]] = None,
) -> str:
    """Prometheus text exposition for the three mapped signals, per region."""
    # `is None` rather than falsy: an empty dict is a test deliberately taking a
    # signal off the wire, which is not the same as not asking for one.
    if success is None:
        success = {REGION_A: STEADY_SUCCESS, REGION_B: STEADY_SUCCESS}
    if errors is None:
        errors = {REGION_A: STEADY_ERRORS, REGION_B: STEADY_ERRORS}
    if saturation is None:
        saturation = {REGION_A: STEADY_SATURATION, REGION_B: STEADY_SATURATION}
    lines = ["# TYPE payment_success_ratio gauge"]
    for region, value in success.items():
        lines.append(f'payment_success_ratio{{region="{region}"}} {value}')
    lines.append("# TYPE error_ratio gauge")
    for region, value in errors.items():
        lines.append(f'error_ratio{{region="{region}"}} {value}')
    lines.append("# TYPE saturation_ratio gauge")
    for region, value in saturation.items():
        lines.append(f'saturation_ratio{{region="{region}"}} {value}')
    return "\n".join(lines) + "\n"


def owner(api) -> tuple[str, str]:
    signed = api.signup()
    return signed["tokens"]["access_token"], signed["org"]["id"]


def onboard(
    api,
    token: str,
    *,
    app_id: str = APP_ID,
    mapping: Optional[dict] = None,
    slos: Optional[Sequence[dict]] = None,
    with_health: bool = False,
) -> dict[str, Optional[str]]:
    """Register, bind and map an application through the real wizard routes.

    Returns the binding ids, so a test can disable one mid-flow and watch what
    the detector does about it.
    """
    register_app(api, token, app_id=app_id, name=app_id.title())
    metrics = connect(api, token, "http-observe-metrics", app_id=app_id)
    health = (
        connect(api, token, "http-observe-health", app_id=app_id)
        if with_health
        else None
    )
    body = dict(MAPPING if mapping is None else mapping)
    response = api.client.put(
        f"/apps/{app_id}/mapping", json=body, headers=api.auth_header(token)
    )
    assert response.status_code == 200, response.text
    governance = api.client.put(
        f"/apps/{app_id}/governance",
        json={"slos": list(SLOS if slos is None else slos)},
        headers=api.auth_header(token),
    )
    assert governance.status_code == 200, governance.text
    return {
        "metrics": metrics["id"],
        "health": health["id"] if health else None,
    }


async def tick(api, tenant_id: str, app_id: str = APP_ID, *, evaluate: bool = False):
    """One five-second tick: advance the clock, poll, optionally evaluate."""
    api.clock.advance(seconds=5)
    report = await api.container.rollup_poller.poll(tenant_id, app_id)
    verdict = None
    if evaluate:
        verdict = await api.container.detection.evaluate(tenant_id, app_id)
    return report, verdict


async def steady(
    api,
    harness,
    tenant_id: str,
    ticks: int,
    *,
    app_id: str = APP_ID,
    evaluate: bool = False,
):
    """Drive `ticks` five-second buckets of unremarkable traffic.

    Ten of these is what it takes before a rolling baseline exists: six
    reference samples, plus the four-bucket lag that keeps the reference window
    from learning the breach it is about to be compared against.
    """
    harness.state.metrics_text = exposition()
    for _ in range(ticks):
        await tick(api, tenant_id, app_id, evaluate=evaluate)


def detection(api, token: str, app_id: str = APP_ID, *, expect: int = 200) -> dict:
    response = api.client.get(
        f"/apps/{app_id}/detection", headers=api.auth_header(token)
    )
    assert response.status_code == expect, response.text
    return response.json()


def rollups(
    api,
    token: str,
    app_id: str = APP_ID,
    *,
    expect: int = 200,
    window_s: float = 3_600,
    **params: Any,
) -> dict:
    query = "&".join(
        [f"window_s={window_s}"]
        + [f"{key}={value}" for key, value in params.items() if value is not None]
    )
    response = api.client.get(
        f"/apps/{app_id}/rollups?{query}", headers=api.auth_header(token)
    )
    assert response.status_code == expect, response.text
    return response.json()


async def principal(api, token: str):
    """The Principal the API would resolve, for the service-level calls a poller
    makes with no request behind it."""
    return await api.container.auth.authenticate(token)


def topics(api, token: str, *names: str) -> list[dict]:
    query = "&".join(f"topics={name}" for name in names)
    response = api.client.get(f"/events?{query}&limit=200", headers=api.auth_header(token))
    assert response.status_code == 200, response.text
    return response.json()["events"]


@pytest.fixture
def anyio_backend():
    return "asyncio"
