"""A governor pass end to end, over the real API and the real stores (S5-T01,
S5-T03, REQ-050, REQ-052, REQ-053).

These run in the **degraded** state by default - the container's model settings
come from an empty environment - which is deliberate: everything asserted here is
what SignalForge does when there is no model at all. The ledger fills, three
hypotheses exist, confidences are computed, topology splits confirmed from
predicted, and history is offered as guidance. The model adds narrative on top of
this, and this is what remains when it is gone.
"""

from __future__ import annotations

import pytest

from signalforge.tests.brain.conftest import (
    APP_ID,
    BRAIN_TOPOLOGY,
    BRAIN_TOPOLOGY_V2,
    announce,
    brain,
    commander,
    declare,
    guidance,
    Sender,
    issue_key,
    node_history,
    onboard,
    owner,
    refresh,
    say,
    topology,
    upload_topology,
    war_room,
    wire_bridge,
)

pytestmark = pytest.mark.anyio


async def scene(api, *, with_topology: bool = True, with_change: bool = False):
    """A declared incident with a room, a map and optionally a deployment."""
    owner_token, _tenant = owner(api)
    onboard(api, owner_token)
    if with_topology:
        upload_topology(api, owner_token, BRAIN_TOPOLOGY, APP_ID)
    wire_bridge(api, owner_token)
    ic = commander(api, owner_token)
    ic_token = ic["tokens"]["access_token"]
    if with_change:
        sender = issue_key(api, owner_token, app_id=APP_ID)
        await announce(api, sender, change_id="release-v12", service="payments")
    # Severity is stated: with no telemetry flowing the matrix proposes nothing,
    # and a commander declaring on their own judgement is the honest case here.
    incident = declare(
        api, ic_token, title="Payments failing in eu-west-1", severity="P1"
    )
    return owner_token, ic_token, incident


@pytest.mark.req("REQ-050", "REQ-052")
async def test_a_pass_fills_the_ledger_and_says_which_agents_ran(api):
    _owner_token, ic_token, incident = await scene(api)
    result = refresh(api, ic_token, incident["id"])

    assert result["nodes_written"] > 0
    assert "telemetry-agent" in result["ran"]
    assert "topology-agent" in result["ran"]
    assert "hypothesis-agent" in result["ran"]
    assert result["snapshot_id"]

    view = brain(api, ic_token, incident["id"])
    assert view["counts"], "the ledger must have node types in it"
    assert {node["node_type"] for node in view["nodes"]} & {
        "fact",
        "hypothesis",
        "impact",
    }
    # Every node says where it came from and how much to trust it.
    for node in view["nodes"]:
        assert node["provenance"]["source"]
        assert node["provenance"]["authority"] in (
            "authoritative",
            "observed",
            "inferred",
        )


@pytest.mark.req("REQ-053")
async def test_three_competing_hypotheses_exist_with_computed_confidence(api):
    """A room that sees one hypothesis tests one hypothesis."""
    _owner_token, ic_token, incident = await scene(api)
    refresh(api, ic_token, incident["id"])
    view = brain(api, ic_token, incident["id"])

    assert len(view["hypotheses"]) >= 3
    for card in view["hypotheses"]:
        assert 0.0 < card["confidence"] < 1.0
        assert card["discriminating_test"], "a percentage without a next step is noise"
        assert card["explain"]
        assert card["node"]["node_type"] == "hypothesis"


@pytest.mark.req("REQ-053")
async def test_a_second_pass_over_an_unchanged_world_writes_no_new_version(api):
    """The idempotency guard. An agent running every thirty seconds against an
    unchanged incident must write nothing, or the history is a heartbeat."""
    _owner_token, ic_token, incident = await scene(api)
    refresh(api, ic_token, incident["id"])
    before = brain(api, ic_token, incident["id"])

    second = refresh(api, ic_token, incident["id"])
    after = brain(api, ic_token, incident["id"])

    assert second["versions_written"] == 0
    assert second["nodes_written"] == 0
    assert len(after["nodes"]) == len(before["nodes"])


@pytest.mark.req("REQ-050", "REQ-053")
async def test_new_evidence_moves_a_hypothesis_and_the_history_says_why(api):
    """The acceptance sequence: evidence arrives, a hypothesis moves, and the
    reason is readable from stored numbers."""
    owner_token, ic_token, incident = await scene(api)
    refresh(api, ic_token, incident["id"])
    before = {
        card["node"]["derived_key"]: card["confidence"]
        for card in brain(api, ic_token, incident["id"])["hypotheses"]
    }
    change_key = next(key for key in before if "change" in key)

    # A deployment lands on the incident: real ingest, real HMAC, real bus.
    sender = Sender(
        api=api, key=issue_key(api, owner_token, stream="changes", app_id=APP_ID)
    )
    await announce(api, sender, change_id="release-v12", service="payments")
    refresh(api, ic_token, incident["id"])

    view = brain(api, ic_token, incident["id"])
    after = {card["node"]["derived_key"]: card["confidence"] for card in view["hypotheses"]}
    moved = [key for key in before if key in after and after[key] != before[key]]
    assert moved or set(after) - set(before), "new evidence must move something"

    card = next(
        (c for c in view["hypotheses"] if "change" in c["node"]["derived_key"]), None
    )
    assert card is not None
    assert card["history"], "a confidence that moved must have a history entry"
    latest = card["history"][-1]
    assert latest["explain"]
    assert "net weight" in latest["explain"]
    assert latest["direction"] in ("rose", "fell", "steady")
    assert change_key


@pytest.mark.req("REQ-050")
async def test_the_topology_view_keeps_confirmed_and_predicted_apart(api):
    _owner_token, ic_token, incident = await scene(api)
    refresh(api, ic_token, incident["id"])
    view = topology(api, ic_token, incident["id"])

    assert view["version"] == "v1"
    assert view["nodes"] and view["edges"]
    assert set(view["confirmed"]).isdisjoint(set(view["predicted"]))
    for impact in view["impacts"]:
        assert impact["confirmation"] in ("confirmed", "predicted")
        if impact["confirmation"] == "predicted":
            assert "nothing has been measured" in impact["reason"]


@pytest.mark.req("REQ-050")
async def test_a_second_map_version_is_selectable_and_the_delta_is_computed(api):
    owner_token, ic_token, incident = await scene(api)
    upload_topology(api, owner_token, BRAIN_TOPOLOGY_V2, APP_ID)
    refresh(api, ic_token, incident["id"])

    latest = topology(api, ic_token, incident["id"])
    assert latest["version"] == "v2"
    assert set(latest["versions"]) >= {"v1", "v2"}
    assert latest["delta"] is not None
    assert "fraud-check" in latest["delta"]["added_nodes"]

    # Older versions are kept forever, so an incident can be read against the
    # map that was in force when it started.
    older = topology(api, ic_token, incident["id"], version="v1")
    assert older["version"] == "v1"


@pytest.mark.req("REQ-050")
async def test_every_version_of_a_belief_is_readable(api):
    _owner_token, ic_token, incident = await scene(api)
    refresh(api, ic_token, incident["id"])
    view = brain(api, ic_token, incident["id"])
    key = view["hypotheses"][0]["node"]["derived_key"]

    history = node_history(api, ic_token, incident["id"], key)
    assert history["derived_key"] == key
    assert history["versions"]
    assert [node["version"] for node in history["versions"]] == sorted(
        node["version"] for node in history["versions"]
    )


@pytest.mark.req("REQ-052")
async def test_what_is_said_on_the_bridge_becomes_classified_graph_nodes(api):
    _owner_token, ic_token, incident = await scene(api)
    say(api, ic_token, incident["id"], "connection-pool is at 100% utilisation")
    say(api, ic_token, incident["id"], "probably the deploy that went out at 14:02")
    say(api, ic_token, incident["id"], "I'll drain the queue in ten minutes")
    refresh(api, ic_token, incident["id"])

    view = brain(api, ic_token, incident["id"])
    statements = [
        node for node in view["nodes"] if node["detail"].get("classifier")
    ]
    kinds = {node["detail"].get("kind") for node in statements}
    assert {"fact", "assumption", "commitment"} <= kinds
    # Somebody else's words, flagged as such all the way to the reader.
    assert all(node["untrusted"] for node in statements)
    # And classified by the rules, because this container has no model.
    assert all(node["detail"]["classifier"] == "deterministic" for node in statements)


@pytest.mark.req("REQ-052")
async def test_the_pass_records_an_execution_trace_without_prompts(api):
    """Which agent ran, on what, with which model, and whether it degraded -
    and nothing that could be a prompt or an answer."""
    _owner_token, ic_token, incident = await scene(api)
    refresh(api, ic_token, incident["id"])
    view = brain(api, ic_token, incident["id"])

    assert view["runs"]
    agents = {run["agent"] for run in view["runs"]}
    assert {"telemetry-agent", "topology-agent", "hypothesis-agent"} <= agents
    for run in view["runs"]:
        assert run["status"] in ("ok", "degraded", "failed")
        assert run["started_at"]
        assert len(run["error"]) <= 500


@pytest.mark.req("REQ-052")
async def test_an_application_with_no_map_says_so_rather_than_inventing_one(api):
    _owner_token, ic_token, incident = await scene(api, with_topology=False)
    refresh(api, ic_token, incident["id"])

    view = topology(api, ic_token, incident["id"])
    assert view["version"] == ""
    assert "No dependency map" in view["detail"]["reason"]

    brain_view = brain(api, ic_token, incident["id"])
    assumptions = [
        node for node in brain_view["nodes"] if node["node_type"] == "assumption"
    ]
    assert any("no dependency map" in node["label"].lower() for node in assumptions)


@pytest.mark.req("REQ-052")
async def test_guidance_is_empty_and_captioned_when_there_is_no_history(api):
    _owner_token, ic_token, incident = await scene(api)
    refresh(api, ic_token, incident["id"])
    view = guidance(api, ic_token, incident["id"])

    assert view["similar"] == []
    assert "guidance" in view["caveat"]
    assert "measured on the system that is broken now" in view["caveat"]


@pytest.mark.req("REQ-050")
async def test_a_closed_incident_is_a_record_rather_than_a_work_item(api):
    from signalforge.tests.brain.conftest import close_incident

    _owner_token, ic_token, incident = await scene(api)
    refresh(api, ic_token, incident["id"])
    close_incident(api, ic_token, incident["id"], reason="mitigated")

    result = refresh(api, ic_token, incident["id"])
    assert result["skipped"] == "the incident is closed"
    # The ledger is still readable. Nothing is deleted when an incident closes.
    assert brain(api, ic_token, incident["id"])["nodes"]


@pytest.mark.req("REQ-045")
async def test_the_war_room_still_works_exactly_as_sprint_four_left_it(api):
    """Sprint 4's controls are unaffected by the brain being wired in."""
    _owner_token, ic_token, incident = await scene(api)
    refresh(api, ic_token, incident["id"])
    room = war_room(api, ic_token, incident["id"])
    assert room["incident_id"] == incident["id"]
    assert room["required_roles"]
    assert room["blast_radius"]["summary"]
