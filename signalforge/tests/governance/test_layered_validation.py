"""Layered validation and the closure gate (REQ-065).

The claim under test is that recovery is checked against the *application*. The
health readings in these tests come from the same ASGI double the adapter suite
uses, over the shipped health adapter, through the invoker - and the Incident
Brain is never consulted, which the domain enforces by refusing a check whose
source names it.

The expensive case has its own test: technical healthy, service healthy, customer
failing, and closure still refused.
"""

from __future__ import annotations

import pytest

from signalforge.domain.governance.errors import GovernanceValidationError
from signalforge.domain.governance.validation import (
    LayerCheck,
    LayerVerdict,
    StabilityWindow,
    ValidationLayer,
    verdict_of,
)
from signalforge.tests.governance.conftest import (  # noqa: F401  (fixtures)
    close_incident,
    governed,
    run_validation,
    set_state,
    validation_view,
)

pytestmark = pytest.mark.anyio


# --- the domain rules ----------------------------------------------------------


@pytest.mark.req("REQ-065")
def test_a_validation_check_may_not_read_the_incident_brain():
    """Enforced at construction rather than by review: a rule that lives only in
    a docstring lasts until somebody is in a hurry."""
    with pytest.raises(GovernanceValidationError) as raised:
        LayerCheck(
            check_id="pool",
            label="the brain thinks the pool is fine",
            verdict=LayerVerdict.PASS,
            source="incident-brain",
        )
    assert "never against the platform's own reasoning" in str(raised.value)


@pytest.mark.req("REQ-065")
def test_a_layer_is_as_good_as_its_worst_check():
    """No averaging and no quorum: three of four customer journeys working is a
    failing customer layer."""
    checks = [
        LayerCheck("a", "a", LayerVerdict.PASS, "probe-runner"),
        LayerCheck("b", "b", LayerVerdict.PASS, "probe-runner"),
        LayerCheck("c", "c", LayerVerdict.PASS, "probe-runner"),
        LayerCheck("d", "d", LayerVerdict.FAIL, "probe-runner"),
    ]
    assert verdict_of(checks) is LayerVerdict.FAIL
    assert verdict_of(checks[:3]) is LayerVerdict.PASS
    # Silence is not consent.
    assert (
        verdict_of([LayerCheck("e", "e", LayerVerdict.UNKNOWN, "probe-runner")])
        is LayerVerdict.UNKNOWN
    )
    assert verdict_of([]) is LayerVerdict.NOT_APPLICABLE


@pytest.mark.req("REQ-065")
def test_any_regression_resets_the_stability_window_to_zero():
    from datetime import datetime, timedelta, timezone

    at = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)
    window = StabilityWindow(required_s=300, required_samples=3)
    for step in range(3):
        window = window.observed(at=at + timedelta(seconds=60 * step), healthy=True)
    assert window.consecutive_passes == 3

    window = window.observed(
        at=at + timedelta(seconds=200), healthy=False, reason="error rate spiked"
    )
    assert window.consecutive_passes == 0, "a flap is not two of three"
    assert window.regressions == 1
    assert window.last_regression_reason == "error rate spiked"
    assert window.satisfied(at + timedelta(seconds=400)) is False

    window = window.observed(at=at + timedelta(seconds=260), healthy=True)
    assert window.consecutive_passes == 1


@pytest.mark.req("REQ-065")
def test_the_customer_layer_gates_closure_and_business_does_not():
    assert ValidationLayer.CUSTOMER.gates_closure is True
    assert ValidationLayer.TECHNICAL.gates_closure is True
    assert ValidationLayer.SERVICE.gates_closure is True
    assert ValidationLayer.BUSINESS.gates_closure is False


# --- against the real application ---------------------------------------------


@pytest.mark.req("REQ-065")
async def test_every_layer_names_the_system_it_read(governed):
    incident = governed.declare()
    cycle = run_validation(governed.api, governed.requester_token, incident["id"])
    assert cycle["cycle"] == 1
    layers = {layer["layer"]: layer for layer in cycle["layers"]}
    assert set(layers) == {"technical", "service", "customer", "business"}
    for layer in layers.values():
        for check in layer["checks"]:
            assert check["source"], "every check names where it was read"
            assert check["source"] not in {"incident-brain", "brain", "graph"}


@pytest.mark.req("REQ-065")
async def test_a_failing_customer_layer_blocks_closure(governed):
    """Technical healthy, service healthy, customer failing - and the incident
    still cannot be closed. This is the case four layers exist for."""
    incident = governed.declare()
    await _fail_a_customer_journey(governed)
    cycle = run_validation(governed.api, governed.requester_token, incident["id"])

    assert cycle["verdict"] == "not_recovered"
    assert cycle["closure_blocked"] is True
    assert "customer" in cycle["closure_lock_reason"]

    refused = close_incident(
        governed.api, governed.requester_token, incident["id"], expect=409
    )
    assert "customer" in refused["detail"]
    assert "incident.closure.blocked" in governed.api.audit_topics(governed.tenant_id)


@pytest.mark.req("REQ-043", "REQ-065")
async def test_a_failed_revalidation_returns_the_incident_to_active(governed):
    incident = governed.declare()
    set_state(governed.api, governed.requester_token, incident["id"], "recovering")
    await _fail_a_customer_journey(governed)

    cycle = run_validation(governed.api, governed.requester_token, incident["id"])
    assert cycle["verdict"] == "not_recovered"
    assert cycle["returned_to_active"] is True
    assert cycle["evidence_node_ids"], "the failure is fed back into the reasoning"

    from signalforge.tests.governance.conftest import read_incident

    assert (
        read_incident(governed.api, governed.requester_token, incident["id"])["state"]
        == "active"
    )


@pytest.mark.req("REQ-065")
async def test_a_failed_validation_becomes_evidence_on_the_graph(governed):
    from signalforge.tests.governance.conftest import graph_nodes

    incident = governed.declare()
    await _fail_a_customer_journey(governed)
    cycle = run_validation(governed.api, governed.requester_token, incident["id"])

    nodes = graph_nodes(governed.api, governed.requester_token, incident["id"])
    evidence = [n for n in nodes if n["id"] in cycle["evidence_node_ids"]]
    assert evidence, "the cycle wrote what it found into the incident's record"
    node = evidence[0]
    assert node["node_type"] == "evidence"
    # Measured, not inferred: this is a reading of the application.
    assert node["provenance"]["authority"] == "observed"
    assert node["provenance"]["result_ref"].startswith("validation:")


@pytest.mark.req("REQ-065")
async def test_repeated_failures_escalate_after_the_configured_cycles(governed):
    from signalforge.tests.governance.conftest import put_policy

    put_policy(
        governed.api,
        governed.owner_token,
        "revalidation_cycles_before_escalation: 2\n",
    )
    incident = governed.declare()
    await _fail_a_customer_journey(governed)

    first = run_validation(governed.api, governed.requester_token, incident["id"])
    assert first["escalated"] is False
    second = run_validation(governed.api, governed.requester_token, incident["id"])
    assert second["cycle"] == 2
    assert second["escalated"] is True
    assert "validation.escalated" in governed.api.audit_topics(governed.tenant_id)

    view = validation_view(governed.api, governed.requester_token, incident["id"])
    assert view["failed_cycles"] >= 2
    assert view["closure_blocked"] is True


@pytest.mark.req("REQ-065")
async def test_an_incident_nobody_validated_can_still_be_closed(governed):
    """Closing is a human act, and a commander who never asked for validation is
    making a judgement the platform has no standing to refuse. What it refuses is
    closing in the face of its own failing check."""
    incident = governed.declare()
    closed = close_incident(governed.api, governed.requester_token, incident["id"])
    assert closed["state"] == "closed"


# --- helpers -------------------------------------------------------------------


async def _fail_a_customer_journey(governed) -> None:
    """Break the customer layer and nothing else.

    A journey probe is mapped at a path the application does not serve, and the
    real probe runner runs it over the real adapter - so the failing check is a
    genuine HTTP result, not a stubbed verdict. The health endpoint keeps saying
    UP, which is the whole point: technical and service pass, customers do not.
    """
    from signalforge.tests.governance.conftest import APP_ID

    # The application itself is healthy. That is the point of the scenario: the
    # instances are up, the dependencies are up, and customers still cannot pay.
    governed.observe.state.health = {
        "status": "up",
        "checked_at": "2026-09-01T12:00:00+00:00",
        "components": [
            {"component": "database", "status": "up"},
            {"component": "payment-gateway", "status": "up"},
        ],
    }
    response = governed.api.client.put(
        f"/apps/{APP_ID}/mapping",
        json={
            "journeys": [
                {"journey_id": "card-payment", "name": "Card payment", "tier": 1}
            ],
            "probes": [
                {
                    "probe_id": "card-payment",
                    "path": "/checkout/pay",
                    "journey_id": "card-payment",
                    "per_region": False,
                }
            ],
        },
        headers=governed.api.auth_header(governed.owner_token),
    )
    assert response.status_code == 200, response.text
    records = await governed.api.container.probe_runner.run(
        governed.tenant_id, APP_ID
    )
    assert records and any(
        record.verdict.value == "failed" for record in records
    ), "the probe must actually have failed against the application"
