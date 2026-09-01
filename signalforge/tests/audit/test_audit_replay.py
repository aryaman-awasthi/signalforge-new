"""Audit replay: readable, verifiable, and impossible to read across tenants.

Four claims, and the last two are the ones that matter:

1. a replay reconstructs the incident from the chain, and every consequential
   line carries who did it and what justified it;
2. the scrubber's `until_seq` is the same code path with a different bound, so a
   partial view cannot disagree with the whole;
3. editing or deleting a stored record makes verification fail, names the first
   record that no longer holds, and says so in the reason;
4. another tenant's incident replays as nothing - not as a 403 that leaks
   existence, and never as content.
"""

from __future__ import annotations

import pytest

from signalforge.tests.audit.conftest import (  # noqa: F401  (fixtures)
    APP_ID,
    act,
    audit_seqs,
    delete_audit_row,
    governed,
    observe,
    request_execution,
    run_validation,
    tamper,
)

pytestmark = pytest.mark.anyio

ROLLBACK = "rollback-checkout"


async def _an_incident_with_a_history(governed) -> str:
    """Declare, refuse an auto-execution, approve properly, time out, reconcile."""
    api = governed.api
    incident = governed.declare(title="Checkout failing after the 14:02 release")
    incident_id = incident["id"]

    request_execution(
        api,
        governed.requester_token,
        incident_id,
        action_id=ROLLBACK,
        args={"release": "v11"},
        intent="auto",
    )
    requested = request_execution(
        api,
        governed.requester_token,
        incident_id,
        action_id=ROLLBACK,
        args={"release": "v11"},
        note="rolling back the 14:02 release",
    )
    execution_id = requested["execution"]["execution_id"]
    act(
        api,
        governed.approver_token,
        incident_id,
        execution_id,
        "approve",
        note="agreed - roll back to v11",
    )
    governed.observe.times_out(f"/observe/actions/{ROLLBACK}/execute")
    ran = act(api, governed.requester_token, incident_id, execution_id, "run")
    governed.observe.timing_out.clear()
    governed.observe.state.executions[ran["execution"]["idempotency_key"]] = {
        "execution_id": "exec-rollback-1",
        "action_id": ROLLBACK,
        "idempotency_key": ran["execution"]["idempotency_key"],
        "state": "succeeded",
        "requested_at": "2026-09-01T12:00:00+00:00",
        "finished_at": "2026-09-01T12:02:00+00:00",
        "output": {"release": "v11", "applied": True},
    }
    act(api, governed.requester_token, incident_id, execution_id, "reconcile")
    run_validation(api, governed.requester_token, incident_id)
    return incident_id


def _replay(api, token: str, incident_id: str, *, expect: int = 200, **params):
    query = "".join(f"&{k}={v}" for k, v in params.items())
    response = api.client.get(
        f"/incidents/{incident_id}/replay?x=1{query}", headers=api.auth_header(token)
    )
    assert response.status_code == expect, response.text
    return response.json()


@pytest.mark.req("REQ-072", "REQ-016")
async def test_a_replay_reconstructs_the_incident_with_actor_and_justification(governed):
    incident_id = await _an_incident_with_a_history(governed)
    document = _replay(governed.api, governed.owner_token, incident_id)

    assert document["chain"]["valid"] is True
    assert document["count"] > 0
    topics = [entry["topic"] for entry in document["entries"]]
    for expected in (
        "incident.declared",
        "execution.blocked",
        "execution.approval.granted",
        "execution.uncertain",
        "execution.reconciled",
        "validation.completed",
    ):
        assert expected in topics, f"{expected} is missing from the replay"

    # Order is the order it happened in.
    assert [entry["seq"] for entry in document["entries"]] == sorted(
        entry["seq"] for entry in document["entries"]
    )

    by_topic = {entry["topic"]: entry for entry in document["entries"]}

    # Who declared it, by name rather than by uuid.
    declared = by_topic["incident.declared"]
    assert declared["actor_type"] == "human"
    assert declared["actor_id"] == governed.requester_id
    assert declared["actor_label"] and declared["actor_label"] != declared["actor_id"]
    assert declared["consequence"] == "consequential"

    # What justified the refusal, and what refused it.
    blocked = by_topic["execution.blocked"]
    assert blocked["consequence"] == "consequential"
    assert any("FLOOR-" in clause for clause in blocked["justification"]), (
        blocked["justification"]
    )

    # Who approved, under which permission.
    granted = by_topic["execution.approval.granted"]
    assert granted["actor_id"] == governed.approver_id
    assert any("permission" in clause for clause in granted["justification"])

    # And what can be opened to check the reconciliation.
    reconciled = by_topic["execution.reconciled"]
    assert any(
        ref.startswith(("execution_id=", "idempotency_key="))
        for ref in reconciled["references"]
    ), reconciled["references"]


@pytest.mark.req("REQ-072")
async def test_the_scrubber_returns_a_prefix_of_the_same_history(governed):
    incident_id = await _an_incident_with_a_history(governed)
    whole = _replay(governed.api, governed.owner_token, incident_id)
    midpoint = whole["entries"][len(whole["entries"]) // 2]["seq"]

    partial = _replay(
        governed.api, governed.owner_token, incident_id, until_seq=midpoint
    )
    assert partial["count"] < whole["count"]
    assert all(entry["seq"] <= midpoint for entry in partial["entries"])
    prefix = [e["event_id"] for e in whole["entries"] if e["seq"] <= midpoint]
    assert [e["event_id"] for e in partial["entries"]] == prefix


@pytest.mark.req("REQ-072", "REQ-016")
async def test_an_edited_audit_record_makes_the_replay_report_invalid(governed):
    """The chain is what makes a replay evidence rather than a story."""
    incident_id = await _an_incident_with_a_history(governed)
    before = _replay(governed.api, governed.owner_token, incident_id)
    assert before["chain"]["valid"] is True

    target = audit_seqs(governed.api.db_path, governed.tenant_id, "execution.blocked")[0]
    tamper(governed.api.db_path, seq=target)

    after = _replay(governed.api, governed.owner_token, incident_id)
    assert after["chain"]["valid"] is False
    assert after["chain"]["first_broken_seq"] == target
    assert target in after["chain"]["broken_seqs"]
    assert "not evidence" in after["chain"]["reason"].lower()


@pytest.mark.req("REQ-072", "REQ-016")
async def test_a_deleted_audit_record_breaks_the_chain_at_its_successor(governed):
    incident_id = await _an_incident_with_a_history(governed)
    target = audit_seqs(governed.api.db_path, governed.tenant_id, "execution.blocked")[0]
    delete_audit_row(governed.api.db_path, seq=target)

    after = _replay(governed.api, governed.owner_token, incident_id)
    assert after["chain"]["valid"] is False
    assert after["chain"]["first_broken_seq"] is not None
    assert after["chain"]["first_broken_seq"] > target, (
        "the successor's prev link is what no longer matches"
    )


@pytest.mark.req("REQ-072", "REQ-013")
async def test_another_tenants_incident_replays_as_nothing(governed):
    """Not a 403 that confirms it exists. Nothing."""
    incident_id = await _an_incident_with_a_history(governed)

    intruder = governed.api.signup(
        org="Rival Ltd", email="owner@rival.test", display_name="Rival Owner"
    )
    document = _replay(
        governed.api, intruder["tokens"]["access_token"], incident_id
    )
    assert document["count"] == 0
    assert document["entries"] == []
    assert document["chain"]["valid"] is True, (
        "the intruder's own chain is intact; they simply have no such incident"
    )


@pytest.mark.req("REQ-072", "REQ-012")
async def test_an_auditor_may_read_a_replay_and_change_nothing(governed):
    incident_id = await _an_incident_with_a_history(governed)
    auditor = governed.api.invite_and_accept(
        governed.owner_token,
        email="auditor@acme.io",
        roles=["auditor"],
        display_name="Ada Auditor",
        password="auditor-strong-password",
    )
    token = auditor["tokens"]["access_token"]

    document = _replay(governed.api, token, incident_id)
    assert document["count"] > 0

    # The same session cannot act on the incident it can read.
    refused = governed.api.client.post(
        f"/incidents/{incident_id}/close",
        json={},
        headers=governed.api.auth_header(token),
    )
    assert refused.status_code == 403, refused.text
