"""The reference harness onboards through the public contracts (REQ-038).

This is the sprint's acceptance, run for real: a Java application built from
reference-app/ is started as a subprocess, and the platform onboards it over the
network through the same routes the wizard calls - discover, connect, test, map,
import actions, upload topology, set SLOs, learn baselines, run every test, score
readiness. Nothing is mounted in-process and nothing is seeded.

It skips, loudly, when the harness has not been built, because a Java build is
not something a Python suite should perform. Everything about the harness that
can be checked without a JVM is checked unconditionally in
test_reference_contract.py, so a skip here narrows the evidence rather than
removing it.
"""

from __future__ import annotations

import httpx
import pytest

from signalforge.contracts.observe import OBSERVE_METRICS_CONTENT_TYPE, OBSERVE_PATHS
from signalforge.domain.integrations.promtext import parse_exposition, select
from signalforge.tests.reference.conftest import (
    DRILL,
    READ_ONLY,
    REMEDIATION,
    start_harness,
)

APP_ID = "payments"
BINDINGS = [
    ("http-observe-metrics", "read_only", READ_ONLY),
    ("http-observe-logs", "read_only", READ_ONLY),
    ("http-observe-traces", "read_only", READ_ONLY),
    ("http-observe-health", "read_only", READ_ONLY),
    ("http-observe-actions", "remediation", REMEDIATION),
    ("http-observe-chaos", "drill", DRILL),
    ("topo-import", "read_only", READ_ONLY),
]

SIGNALS = [
    {
        "key": "availability",
        "signal": "availability",
        "selector": "payment_success_ratio",
        "direction": "lower_is_worse",
        "unit": "ratio",
    },
    {
        "key": "errors",
        "signal": "errors",
        "selector": 'payment_requests_total{status="failed"}',
        "direction": "higher_is_worse",
        "unit": "count",
    },
    {
        "key": "latency",
        "signal": "latency",
        "selector": "payment_latency_p95_ms",
        "direction": "higher_is_worse",
        "unit": "ms",
    },
    {
        "key": "saturation",
        "signal": "saturation",
        "selector": "db_pool_in_use",
        "direction": "higher_is_worse",
        "unit": "count",
    },
    {
        "key": "settlements",
        "signal": "business",
        "selector": "settlements_completed_total",
        "direction": "lower_is_worse",
        "unit": "count",
        "business_counter": True,
    },
]


@pytest.fixture(scope="module")
def harness(tmp_path_factory):
    """The built harness with its seeded traffic generator running, so a
    baseline has volume to learn from. Boot and teardown are shared with the
    detection acceptance in conftest.start_harness."""
    yield from start_harness(tmp_path_factory.mktemp("harness-run"))


def _bearer(key: str) -> dict:
    return {"Authorization": f"Bearer {key}"}


@pytest.mark.req("REQ-038")
def test_the_harness_serves_the_observe_contract_over_real_http(harness):
    """Before any onboarding: every frozen path answers, guarded, and the metrics
    exposition parses with the platform's own strict parser - which refuses a
    malformed line rather than dropping it."""
    for key, path in OBSERVE_PATHS.items():
        if "{" in path:
            continue
        unauthenticated = httpx.get(f"{harness}{path}", timeout=10.0)
        assert unauthenticated.status_code in (401, 403), f"{path} is not guarded"

    metrics = httpx.get(f"{harness}{OBSERVE_PATHS['metrics']}", headers=_bearer(READ_ONLY),
                        timeout=10.0)
    assert metrics.status_code == 200
    assert metrics.headers["content-type"] == OBSERVE_METRICS_CONTENT_TYPE

    from datetime import datetime, timezone

    series = parse_exposition(metrics.text, datetime.now(timezone.utc))
    assert series, "the harness exports no metrics"
    for signal in SIGNALS:
        assert select(series, signal["selector"]), (
            f"nothing matches {signal['selector']!r}, so this golden signal could not be mapped"
        )
    # The business counter must be reachable per region, because the severity
    # matrix reasons about regions and a single global number cannot answer it.
    regions = {one.labels.get("region") for one in select(series, "settlements_completed_total")}
    assert regions == {"region-a", "region-b"}

    # A read-only credential cannot reach an action, and only a drill credential
    # reaches chaos: the credential-scope floor, enforced by the application.
    assert httpx.get(f"{harness}/observe/actions", headers=_bearer(READ_ONLY),
                     timeout=10.0).status_code == 403
    assert httpx.get(f"{harness}/observe/chaos", headers=_bearer(REMEDIATION),
                     timeout=10.0).status_code == 403
    assert httpx.get(f"{harness}/observe/chaos", headers=_bearer(DRILL),
                     timeout=10.0).status_code == 200


@pytest.mark.req("REQ-038", "REQ-036")
def test_the_harness_onboards_to_full_readiness_with_no_seeds(api, harness):
    """The sprint's acceptance. Every call below is a route the wizard's screens
    call, in the order the wizard calls them, against an application that was
    written without any knowledge of this platform."""
    signup = api.signup()
    token = signup["tokens"]["access_token"]
    headers = api.auth_header(token)

    registered = api.client.post(
        "/apps",
        json={"app_id": APP_ID, "name": "Payments", "tier": "tier_1", "base_url": harness},
        headers=headers,
    )
    assert registered.status_code == 201, registered.text

    discovered = api.client.post(f"/apps/{APP_ID}/discover", json={}, headers=headers)
    assert discovered.status_code == 200, discovered.text
    observe_findings = {
        finding["key"]: finding
        for finding in discovered.json()["findings"]
        if finding["source"] == "observe"
    }
    assert len(observe_findings) == 7
    for finding in observe_findings.values():
        assert finding["present"] and finding["guarded"], finding
        # Discovery writes nothing and sends no credential; it only reports.
        assert finding["candidates"], f"{finding['key']} matched no adapter"

    for adapter_id, scope, credential in BINDINGS:
        bound = api.client.post(
            "/connectors",
            json={
                "app_id": APP_ID,
                "adapter_id": adapter_id,
                "name": f"payments {adapter_id}",
                "credential_scope": scope,
                "config": {"base_url": harness},
                "credentials": {"token": credential},
            },
            headers=headers,
        )
        assert bound.status_code == 201, bound.text

    tested = api.client.post(f"/apps/{APP_ID}/connection-tests", json={}, headers=headers)
    assert tested.status_code == 200, tested.text
    outcomes = {result["adapter_id"]: result["outcome"] for result in tested.json()}
    assert set(outcomes) == {adapter_id for adapter_id, _, _ in BINDINGS}
    assert set(outcomes.values()) == {"passed"}, outcomes

    mapped = api.client.put(
        f"/apps/{APP_ID}/mapping",
        json={
            "signals": SIGNALS,
            "region_label": "region",
            "regions": ["region-a", "region-b"],
            "journeys": [
                {"journey_id": "upi", "name": "UPI payment", "tier": 1},
                {"journey_id": "card", "name": "Card payment", "tier": 1},
            ],
            "probes": [
                {
                    "probe_id": "upi-a",
                    "path": "/api/payments/probe/upi?region=region-a",
                    "journey_id": "upi",
                    "expected_status": 200,
                    "per_region": False,
                },
                {
                    "probe_id": "upi-b",
                    "path": "/api/payments/probe/upi?region=region-b",
                    "journey_id": "upi",
                    "expected_status": 200,
                    "per_region": False,
                },
            ],
        },
        headers=headers,
    )
    assert mapped.status_code == 200, mapped.text
    assert {signal["key"] for signal in mapped.json()["signals"]} == {s["key"] for s in SIGNALS}

    imported = api.client.post(f"/apps/{APP_ID}/actions/import", json={}, headers=headers)
    assert imported.status_code == 200, imported.text
    actions = {action["action_id"]: action for action in imported.json()["actions"]}
    assert imported.json()["imported"] == len(actions) >= 5
    # The floor lands on the application's own manifest without anybody typing
    # it: nothing in a high-risk class may be set to run on its own.
    for action in actions.values():
        if action["action_class"] in (
            "rollback", "restart", "failover", "traffic_shift", "config_change",
            "customer_impacting",
        ):
            assert action["execution_mode"] != "auto", action
            assert action["effective_risk"] == "high", action
            assert action["approver_roles"], action

    exported = httpx.get(
        f"{harness}{OBSERVE_PATHS['topology']}", headers=_bearer(READ_ONLY), timeout=15.0
    ).json()
    uploaded = api.client.put(
        f"/apps/{APP_ID}/topology", json={"document": exported}, headers=headers
    )
    assert uploaded.status_code == 200, uploaded.text
    assert uploaded.json()["unchanged"] is False
    version = uploaded.json()["version"]

    # A second export a moment later carries a new capture time and the same
    # graph. One version, no second event: otherwise a poller would fill the
    # timeline with topology changes nobody made.
    re_exported = httpx.get(
        f"{harness}{OBSERVE_PATHS['topology']}", headers=_bearer(READ_ONLY), timeout=15.0
    ).json()
    assert re_exported["captured_at"] != exported["captured_at"]
    again = api.client.put(
        f"/apps/{APP_ID}/topology", json={"document": re_exported}, headers=headers
    )
    assert again.status_code == 200, again.text
    assert again.json()["unchanged"] is True
    assert again.json()["version"] == version

    governed = api.client.put(
        f"/apps/{APP_ID}/governance",
        json={
            "slos": [
                {
                    "slo_id": "upi-availability",
                    "name": "UPI availability",
                    "objective_pct": 99.5,
                    "window_days": 30,
                    "journey_id": "upi",
                    "indicator": "payment_success_ratio",
                }
            ]
        },
        headers=headers,
    )
    assert governed.status_code == 200, governed.text

    learned = api.client.post(f"/apps/{APP_ID}/baseline", json={}, headers=headers)
    assert learned.status_code == 200, learned.text
    assert learned.json()["missed"] == []
    assert {one["signal_key"] for one in learned.json()["learned"]} == {s["key"] for s in SIGNALS}

    ran = api.client.post(f"/apps/{APP_ID}/test", json={}, headers=headers)
    assert ran.status_code == 200, ran.text
    summary = ran.json()["summary"]
    assert summary["failed"] == 0 and summary["passed"] >= len(BINDINGS), summary

    readiness = api.client.get(f"/apps/{APP_ID}/readiness", headers=headers)
    assert readiness.status_code == 200, readiness.text
    scored = readiness.json()
    assert scored["ready"] is True, [c for c in scored["checks"] if not c["passed"]]
    assert scored["score"] == 100, [c for c in scored["checks"] if not c["passed"]]
