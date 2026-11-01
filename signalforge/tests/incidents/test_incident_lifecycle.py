"""Steady to a declared P0 and back to closed (REQ-043, REQ-046).

The sprint's acceptance path, end to end and with nothing constructed by hand:
real traffic scraped from an application serving only the frozen observe
contract, detection reaching a corroborated proposal on its own, a person
declaring from it, and the whole state machine walked to closure.

The seam this file exists to pin is the one between the two halves of the
lifecycle. Detection can reach `proposed` and stops; the next state exists
because somebody pressed a button, and every fact about that - who, when, from
which stance, accepting or overriding the proposed severity - is on the record.
"""

from __future__ import annotations

import pytest

from signalforge.tests.adapters.conftest import observe  # noqa: F401  (fixture)
from signalforge.tests.detection.conftest import (
    REGION_A,
    REGION_B,
    exposition,
    steady,
    tick,
)
from signalforge.tests.detection.conftest import onboard as detection_onboard
from signalforge.tests.incidents.conftest import (
    APP_ID,
    close_incident,
    commander,
    declare,
    lifecycle,
    list_incidents,
    owner,
    read_incident,
    set_severity,
    set_state,
    topics,
)

pytestmark = pytest.mark.anyio


async def _escalate(api, observe, tenant_id: str) -> None:
    """Ten quiet buckets to learn a baseline, then a fault in both regions."""
    await steady(api, observe, tenant_id, 12, evaluate=True)
    observe.state.metrics_text = exposition(
        success={REGION_A: 0.45, REGION_B: 0.45},
        errors={REGION_A: 0.55, REGION_B: 0.55},
    )
    for _ in range(3):
        await tick(api, tenant_id, evaluate=True)


@pytest.mark.req("REQ-043", "REQ-046")
async def test_a_corroborated_proposal_becomes_an_incident_only_when_a_person_declares(
    api, observe
):
    owner_token, tenant_id = owner(api)
    detection_onboard(api, owner_token)
    ic = commander(api, owner_token)
    token = ic["tokens"]["access_token"]

    await _escalate(api, observe, tenant_id)

    # Detection has gone as far as it is allowed to go.
    view = lifecycle(api, owner_token)
    assert view["state"] == "proposed"
    assert view["source"] == "detection"
    assert view["declared"] is False
    assert view["declare_requires_human"] is True
    assert view["corroborated"] is True
    assert view["proposed_severity"] in ("P0", "P1")
    assert view["incident"] is None
    assert list_incidents(api, owner_token)["count"] == 0, "nothing was declared"

    # A person presses Declare and accepts what the matrix proposed.
    incident = declare(api, token)
    assert incident["state"] == "active"
    assert incident["severity"] == view["proposed_severity"]
    assert incident["severity_source"] == "proposed"
    assert incident["overrode_detection"] is False
    assert incident["stance_at_declare"] == "proposed"
    assert incident["declared_by"] == ic["user"]["id"]
    assert incident["declared_by_human"] is True
    assert incident["reference"] == "INC-000001"

    # The proposal it was declared from is copied onto it, evidence included.
    proposal = incident["proposal"]
    assert proposal["corroborated"] is True
    assert len(proposal["families"]) >= 2
    assert proposal["evidence"], "a proposal an operator cannot drill into is an assertion"
    assert any(ref["ref"].startswith("rollup:") for ref in proposal["evidence"])
    assert proposal["health_score"] is not None

    # And the application's view now reads the decision rather than the stance.
    after = lifecycle(api, owner_token)
    assert after["state"] == "active"
    assert after["source"] == "incident"
    assert after["incident"]["id"] == incident["id"]
    assert after["stance"] == "proposed", "the detector keeps saying what it sees"


@pytest.mark.req("REQ-043")
async def test_the_whole_state_machine_is_walked_by_people_and_recorded(api, observe):
    owner_token, tenant_id = owner(api)
    detection_onboard(api, owner_token)
    ic = commander(api, owner_token)
    token = ic["tokens"]["access_token"]
    await _escalate(api, observe, tenant_id)

    # Minutes rather than the hours a real incident takes: the access token is
    # good for fifteen, and an expiring session mid-walk would be testing the
    # session rather than the lifecycle.
    # Declared cautiously, then re-graded once the blast radius was clear.
    incident = declare(api, token, severity="P2")
    api.clock.advance(minutes=1)
    set_severity(api, token, incident["id"], "P0", reason="both regions, tier-1")
    api.clock.advance(minutes=2)
    set_state(api, token, incident["id"], "recovering", reason="traffic shifted away")
    api.clock.advance(minutes=1)
    # A regression during validation, called by the person watching it.
    back = set_state(api, token, incident["id"], "active", reason="it came back")
    assert back["recovering_at"] is None, (
        "the recovery is no longer true, so the timestamp that claimed it is gone"
    )
    api.clock.advance(minutes=2)
    set_state(api, token, incident["id"], "recovering", reason="fixed properly")
    api.clock.advance(minutes=3)
    closed = close_incident(api, token, incident["id"], reason="validated in both regions")

    assert closed["state"] == "closed"
    assert closed["closed_by"] == ic["user"]["id"]
    assert closed["closure_reason"] == "validated in both regions"
    assert closed["open"] is False

    detail = read_incident(api, token, incident["id"])
    moves = [(row["from_state"], row["to_state"]) for row in detail["transitions"]]
    assert moves == [
        ("proposed", "active"),
        ("active", "active"),  # the severity change: a move of grade, not of state
        ("active", "recovering"),
        ("recovering", "active"),
        ("active", "recovering"),
        ("recovering", "closed"),
    ]
    assert all(row["actor_type"] == "human" for row in detail["transitions"])
    assert all(row["actor_id"] == ic["user"]["id"] for row in detail["transitions"])
    assert detail["transitions"][1]["severity_before"] == "P2"
    assert detail["transitions"][1]["severity_after"] == "P0"
    # The timeline tells a re-grade from a move by this field, which the API
    # did not send until the panel was found reading it.
    assert detail["transitions"][1]["kind"] == "severity"
    assert {row["kind"] for i, row in enumerate(detail["transitions"]) if i != 1} == {"state"}
    assert detail["transitions"][0]["severity_before"] is None, (
        "a declaration grades an incident for the first time; it re-grades nothing"
    )

    # Every one of those acts is in the tenant's audit chain.
    audited = [
        row["topic"] for row in api.audit_rows(tenant_id) if row["topic"].startswith("incident.")
    ]
    assert audited.count("incident.declared") == 1
    assert audited.count("incident.severity.changed") == 1
    assert audited.count("incident.state.changed") == 3
    assert audited.count("incident.closed") == 1

    # The application is back to a stance, and a new incident can be declared.
    view = lifecycle(api, owner_token)
    assert view["source"] == "detection"
    assert view["incident"] is None
    assert list_incidents(api, owner_token, state="open")["count"] == 0
    assert list_incidents(api, owner_token)["count"] == 1


@pytest.mark.req("REQ-043", "REQ-046")
async def test_the_escalation_reads_as_one_story_across_both_planes(api, observe):
    """Detection's events are signal-plane observations with a system actor and
    no incident id; the lifecycle's are incident-plane decisions with a person
    and an incident id. Together they are the escalation, and the difference
    between them is what makes it auditable."""
    owner_token, tenant_id = owner(api)
    detection_onboard(api, owner_token)
    ic = commander(api, owner_token)
    await _escalate(api, observe, tenant_id)
    incident = declare(api, ic["tokens"]["access_token"], severity="P0")

    machine = topics(api, owner_token, "detection.stance.changed", "detection.severity.proposed")
    human = topics(api, owner_token, "incident.declared")
    assert machine and human

    for event in machine:
        assert event["plane"] == "signal"
        assert event["actor"]["type"] == "system"
        assert event["incident_id"] is None
    for event in human:
        assert event["plane"] == "incident"
        assert event["actor"]["type"] == "human"
        assert event["incident_id"] == incident["id"]

    # The declaration comes after the proposal it was made from.
    assert max(e["seq"] for e in machine) < human[0]["seq"]


@pytest.mark.req("REQ-043", "REQ-046")
async def test_a_person_may_overrule_the_severity_the_matrix_proposed(api, observe):
    owner_token, tenant_id = owner(api)
    detection_onboard(api, owner_token)
    ic = commander(api, owner_token)
    token = ic["tokens"]["access_token"]
    await _escalate(api, observe, tenant_id)

    proposed = lifecycle(api, owner_token)["proposed_severity"]
    incident = declare(api, token, severity="P2", reason="known bad canary, not customers")
    assert incident["severity"] == "P2"
    assert incident["severity_source"] == "chosen"
    assert incident["overrode_detection"] is True
    assert incident["proposal"]["severity"] == proposed, (
        "what the matrix said is kept beside what the person decided"
    )

    declared = topics(api, owner_token, "incident.declared")[0]["payload"]
    assert declared["proposed_severity"] == proposed
    assert declared["severity"] == "P2"
    assert declared["overrode_detection"] is True


@pytest.mark.req("REQ-043")
async def test_a_person_can_declare_while_the_detector_cannot_be_read(api):
    """Model failure, in its lifecycle form. The detector is the thing that broke;
    the incident still has to be declarable, because that is exactly the moment
    somebody needs to."""
    owner_token, _tenant = owner(api)
    from signalforge.tests.onboarding.conftest import register_app

    register_app(api, owner_token, app_id=APP_ID, name="Payments")
    ic = commander(api, owner_token)
    token = ic["tokens"]["access_token"]

    broken = api.container.detection

    class Unavailable:
        async def snapshot(self, principal, app_id):
            raise RuntimeError("rollups.db is unreadable")

    api.container.incidents._detection = Unavailable()
    try:
        # With no proposal there is no severity to accept, and the refusal says so.
        refused = declare(api, token, expect=422)
        assert "has to name one" in refused["detail"]
        assert "rollups.db is unreadable" in refused["detail"]

        incident = declare(api, token, severity="P1")
        assert incident["state"] == "active"
        assert incident["proposal"]["unavailable"].endswith("rollups.db is unreadable")
        assert incident["proposal"]["severity"] is None

        view = lifecycle(api, owner_token)
        assert view["detection_error"].endswith("rollups.db is unreadable")
        assert view["state"] == "active", "the incident is the source of truth now"
    finally:
        api.container.incidents._detection = broken

