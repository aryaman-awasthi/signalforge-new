"""The drill engine itself (REQ-070).

Three claims, and each one is the reason a game day is worth holding:

1. a checkpoint compares what the *product* returned with what the *file* said,
   and says which clause failed when they differ;
2. scenario time is the only thing that makes time pass, so an approval TTL is
   exercised in milliseconds without the TTL being shortened;
3. a scenario cannot reach past the ports - a step that names a world verb the
   vocabulary does not contain is refused before the run starts.
"""

from __future__ import annotations

import pytest

from signalforge.domain.drills.errors import CheckpointFailed, ScenarioError, StepError
from signalforge.domain.drills.scenario import load_scenario
from signalforge.tests.governance.conftest import (  # noqa: F401  (fixtures)
    APP_ID,
    STRICT_POLICY,
    act,
    governed,
    put_policy,
    request_execution,
)

pytestmark = pytest.mark.anyio

ROLLBACK = "rollback-checkout"


def _scenario(steps: list[dict], **overrides) -> dict:
    document = {
        "drill_id": "engine-probe",
        "name": "Engine probe",
        "scope": "drill:engine-probe",
        "clock": {"starts_at": "2026-09-01T12:00:00+00:00"},
        "steps": steps,
    }
    document.update(overrides)
    return load_scenario(document)


@pytest.mark.req("REQ-070")
async def test_a_checkpoint_reads_what_the_product_returned(drill_engine, governed):
    incident = governed.declare(title="Checkout failing")
    run = await drill_engine.run(
        _scenario(
            [
                {
                    "id": "read",
                    "kind": "machine",
                    "actor": "ic",
                    "label": "read the incident",
                    "call": {
                        "method": "GET",
                        "path": f"/incidents/{incident['id']}",
                    },
                    "capture": "incident",
                },
                {
                    "id": "cp",
                    "kind": "checkpoint",
                    "label": "it is an active P0",
                    "expect": {
                        "incident.state": "active",
                        "incident.severity": "P0",
                    },
                },
            ]
        ),
        identities={"ic": governed.requester_token},
    )
    assert run.passed
    assert run.captures["incident"]["id"] == incident["id"]
    assert [record.step_id for record in run.records] == ["read", "cp"]


@pytest.mark.req("REQ-070")
async def test_a_failing_checkpoint_names_the_clause(drill_engine, governed):
    incident = governed.declare(title="Checkout failing")
    with pytest.raises(CheckpointFailed) as raised:
        await drill_engine.run(
            _scenario(
                [
                    {
                        "id": "read",
                        "kind": "machine",
                        "actor": "ic",
                        "label": "read the incident",
                        "call": {"method": "GET", "path": f"/incidents/{incident['id']}"},
                        "capture": "incident",
                    },
                    {
                        "id": "cp",
                        "kind": "checkpoint",
                        "label": "the drill expected the wrong thing",
                        "expect": {"incident.severity": "P3"},
                    },
                ]
            ),
            identities={"ic": governed.requester_token},
        )
    detail = str(raised.value)
    assert "incident.severity" in detail
    assert "'P0'" in detail, "the observed value is in the message, not just the expected one"


@pytest.mark.req("REQ-070", "REQ-061", "REQ-024")
async def test_virtual_time_expires_a_real_approval(drill_engine, governed):
    """The approval TTL is the platform's; only the clock is the drill's.

    A high-risk rollback is requested and approved for real. The drill then
    advances scenario time past the policy's approval TTL and presses run. The
    gateway refuses, because the approval it is presented with expired - and
    nothing slept, and no TTL was shortened to make the test quick.
    """
    # The application's own policy, tightening the floor's TTL to ten minutes.
    put_policy(governed.api, governed.owner_token, STRICT_POLICY)
    incident = governed.declare(title="Checkout failing after the release")
    incident_id = incident["id"]
    requested = request_execution(
        governed.api,
        governed.requester_token,
        incident_id,
        action_id=ROLLBACK,
        args={"release": "v11"},
    )
    execution_id = requested["execution"]["execution_id"]
    approved = act(
        governed.api,
        governed.approver_token,
        incident_id,
        execution_id,
        "approve",
        note="agreed",
    )
    ttl_s = approved["approval"]["seconds_left"]
    assert ttl_s > 0

    run = await drill_engine.run(
        _scenario(
            [
                {
                    "id": "wait",
                    "kind": "clock",
                    "label": "the war room moves on to something else",
                    "clock": {"advance_s": ttl_s + 60},
                },
                {
                    "id": "run-it",
                    "kind": "human",
                    "actor": "ic",
                    "label": "somebody presses run, late",
                    "call": {
                        "method": "POST",
                        "path": (
                            f"/incidents/{incident_id}/executions/{execution_id}/run"
                        ),
                        "body": {},
                        "expect_status": [422],
                    },
                    "capture": "refusal",
                },
                {
                    "id": "cp-expired",
                    "kind": "checkpoint",
                    "label": "the gateway refused an expired approval",
                    "expect": [{"path": "refusal.detail", "matches": "(?i)expir"}],
                },
            ]
        ),
        identities={"ic": governed.requester_token},
    )
    assert run.passed
    assert governed.observe.state.executions == {}, "nothing reached the application"


@pytest.mark.req("REQ-070")
async def test_a_step_whose_call_is_refused_stops_the_drill(drill_engine, governed):
    with pytest.raises(StepError) as raised:
        await drill_engine.run(
            _scenario(
                [
                    {
                        "id": "read",
                        "kind": "machine",
                        "actor": "ic",
                        "label": "an incident that does not exist",
                        "call": {"method": "GET", "path": "/incidents/nope"},
                    }
                ]
            ),
            identities={"ic": governed.requester_token},
        )
    assert "expected [200, 201]" in str(raised.value)


@pytest.mark.req("REQ-070")
def test_the_world_vocabulary_is_closed():
    with pytest.raises(ScenarioError) as raised:
        _scenario(
            [
                {
                    "id": "sneak",
                    "kind": "world",
                    "label": "reach into the platform",
                    "world": {"set_incident_state": "closed"},
                }
            ]
        )
    assert "unknown world verb" in str(raised.value)


@pytest.mark.req("REQ-070")
def test_a_scenario_never_rewinds():
    with pytest.raises(ScenarioError) as raised:
        _scenario(
            [
                {
                    "id": "later",
                    "kind": "clock",
                    "label": "five minutes in",
                    "at_s": 300,
                    "clock": {"advance_s": 1},
                },
                {
                    "id": "earlier",
                    "kind": "clock",
                    "label": "one minute in",
                    "at_s": 60,
                    "clock": {"advance_s": 1},
                },
            ]
        )
    assert "backwards" in str(raised.value)
