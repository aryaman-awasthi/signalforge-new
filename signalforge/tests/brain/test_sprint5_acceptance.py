"""Sprint 5 acceptance, as one scripted incident (S5).

    "A scripted evidence sequence moves three hypotheses with explainable
    deltas; revoking the LLM key mid-run flips the DEGRADED badge and the system
    continues deterministically; ai-eval suite green."
    - playbook/sprints/sprint-5.md

One test walks the whole thing, in order, through the real API: declare, reason,
watch three hypotheses appear at the prior, feed a scripted sequence of evidence,
watch one rise and one fall with the reason readable from stored numbers, read the
topology's confirmed/predicted split, ask the copilot a question it can answer and
one it cannot, then **revoke the key at the gateway mid-run** and assert that the
badge flips, the brief falls back and every incident control still works.

It runs against a credentialled provider whose socket is an `httpx.MockTransport`,
so the model path is the shipped one and nothing touches the network. Revoking the
key is simulated the way it actually happens - the gateway starts answering 401 -
rather than by reaching into the container, so the degradation path exercised is
the production one.
"""

from __future__ import annotations

import httpx
import pytest

from signalforge.tests.brain.conftest import (
    APP_ID,
    BRAIN_TOPOLOGY,
    Sender,
    announce,
    ask,
    brain,
    commander,
    declare,
    guidance,
    issue_key,
    onboard,
    owner,
    read_brief,
    refresh,
    say,
    topology,
    upload_topology,
    war_room,
    wire_bridge,
)

pytestmark = pytest.mark.anyio


@pytest.mark.req("REQ-050", "REQ-051", "REQ-052", "REQ-053", "REQ-054", "REQ-055")
async def test_sprint_five_acceptance(api_with_model, gateway):
    api = api_with_model

    # -- 1. an incident exists, by the Sprint 4 flow, unchanged ---------------
    owner_token, _tenant = owner(api)
    onboard(api, owner_token)
    upload_topology(api, owner_token, BRAIN_TOPOLOGY, APP_ID)
    wire_bridge(api, owner_token)
    ic = commander(api, owner_token)
    token = ic["tokens"]["access_token"]
    incident = declare(
        api, token, title="Payments failing in eu-west-1", severity="P1"
    )
    incident_id = incident["id"]
    assert incident["state"] == "active"
    assert war_room(api, token, incident_id)["required_roles"]

    # The model is available and answers when asked. The narrative is prose
    # beside a number the model did not produce.
    gateway.queue(
        "hypothesis-narrative",
        {"narratives": [{"key": "hypothesis:saturation", "narrative": "A person "
                        "reported the pool at 100%, which is the first thing "
                        "measured about it."}]},
    )

    # -- 2. the brain fills, and 3. three competing hypotheses exist ----------
    refresh(api, token, incident_id)
    first = brain(api, token, incident_id)
    assert first["nodes"], "the ledger must contain what the platform believes"
    assert len(first["hypotheses"]) >= 3, "a room that sees one tests one"

    # -- 4. initial confidences are visible and are the platform's arithmetic -
    baseline = {
        card["node"]["derived_key"]: card["confidence"] for card in first["hypotheses"]
    }
    assert all(0.0 < value < 1.0 for value in baseline.values())
    for card in first["hypotheses"]:
        assert card["explain"], "every number carries its explanation"
        assert card["discriminating_test"], "and the next step that would move it"

    saturation = next(key for key in baseline if "saturation" in key)

    # -- 5. new evidence arrives: somebody measures the pool ------------------
    api.clock.advance(minutes=2)
    say(api, token, incident_id, "connection-pool is at 100% utilisation, errors climbing")
    refresh(api, token, incident_id)
    risen = _confidences(brain(api, token, incident_id))

    # -- 6. at least one hypothesis rises ------------------------------------
    assert risen[saturation] > baseline[saturation], (
        "a measurement of the suspected component must move its hypothesis"
    )

    # A change lands too, and becomes its own hypothesis with its own evidence.
    sender = Sender(
        api=api, key=issue_key(api, owner_token, stream="changes", app_id=APP_ID)
    )
    await announce(api, sender, change_id="release-v12", service="payments")
    refresh(api, token, incident_id)
    with_change = brain(api, token, incident_id)
    assert any(
        "change" in card["node"]["derived_key"] for card in with_change["hypotheses"]
    )

    # -- 7. and at least one falls -------------------------------------------
    # The pool is checked and cleared. A room that had just cleared a suspect and
    # watched its confidence keep rising would stop reading the number.
    api.clock.advance(minutes=6)
    say(api, token, incident_id, "connection-pool is fine, no issues after the restart")
    refresh(api, token, incident_id)
    after = brain(api, token, incident_id)
    fallen = _confidences(after)
    assert fallen[saturation] < risen[saturation], (
        "evidence against must be able to bring a hypothesis back down"
    )

    # -- 8. the UI can show WHY, from stored numbers -------------------------
    card = next(
        c for c in after["hypotheses"] if c["node"]["derived_key"] == saturation
    )
    latest = card["history"][-1]
    assert latest["direction"] == "fell"
    assert latest["previous"] is not None
    assert "net weight" in latest["explain"]
    assert latest["refuting"], "the entry names the evidence that pushed it down"
    assert card["evidence_against"], "and the card lists it"

    # -- 9. the confidence history remains visible ---------------------------
    assert len(card["history"]) >= 2
    assert card["sparkline"] == [
        round(entry["current"], 4) for entry in card["history"]
    ]
    # Nothing was overwritten to produce it.
    versions = [
        node
        for node in after["nodes"]
        if node["derived_key"] == saturation
    ]
    assert any(node["status"] == "superseded" for node in versions), (
        "a hypothesis that moved has an older version, kept"
    )

    # -- 10. topology shows CONFIRMED vs PREDICTED ---------------------------
    map_view = topology(api, token, incident_id)
    assert map_view["version"] == "v1"
    assert set(map_view["confirmed"]).isdisjoint(map_view["predicted"])
    assert any(
        impact["confirmation"] == "predicted" for impact in map_view["impacts"]
    ), "the walk must reach something it can only predict"
    for impact in map_view["impacts"]:
        if impact["confirmation"] == "predicted":
            assert "nothing has been measured" in impact["reason"]

    # -- 11. guidance carries its provenance and its caveat ------------------
    advice = guidance(api, token, incident_id)
    assert "measured on the system that is broken now" in advice["caveat"]
    for node in advice["contradictions"] + advice["warnings"] + advice["similar"]:
        assert node["provenance"]["source"]
        assert node["provenance"]["authority"] == "inferred" or node["untrusted"]

    # -- 12. the copilot answers a grounded question with references ---------
    node_ids = [node["id"] for node in after["nodes"]]
    pool_node = next(
        node["id"] for node in after["nodes"] if "connection-pool" in node["label"]
    )
    gateway.queue(
        "copilot-answer",
        {
            "answer": "Somebody reported the connection pool at 100% utilisation, "
            "and later reported it clear after a restart.",
            "citations": [pool_node],
            "sufficient": True,
        },
    )
    grounded = ask(api, token, incident_id, "what happened to the connection pool?")
    assert grounded["verdict"] == "grounded"
    assert grounded["citations"] == [pool_node]
    assert set(grounded["citations"]) <= set(node_ids)

    # -- 13. and refuses one it cannot support -------------------------------
    gateway.queue(
        "copilot-answer",
        {"answer": "not in the record", "citations": [], "sufficient": False},
    )
    refused = ask(api, token, incident_id, "how many customers were charged twice?")
    assert refused["verdict"] == "insufficient_evidence"
    assert "does not contain enough" in refused["answer"]

    # -- 14. the join brief is model-backed while the model is available ------
    gateway.queue(
        "join-brief",
        {
            "headline": "INC: P1 on payments, active.",
            "what_is_happening": ["Payments is failing in eu-west-1."],
            "impact": ["No customer-facing journey is implicated by the evidence held."],
            "what_we_know": ["The connection pool was reported at 100% and later clear."],
            "what_has_been_done": ["Declared by the incident commander."],
            "change_context": ["A deployment of payments started around the onset."],
            "what_we_need": ["Somebody to own the change correlation."],
            "caveats": ["Some of this rests on what people said rather than on telemetry."],
        },
    )
    written = read_brief(api, token, incident_id)
    assert written["source"] == "model"
    # And the honest caveats survived the rewrite.
    assert any("not corroborated" in caveat for caveat in written["caveats"])
    assert any("Written by a model" in caveat for caveat in written["caveats"])

    # -- 15. the key is revoked at the gateway, mid-incident ------------------
    gateway.answers.clear()
    gateway.default = httpx.Response(401, json={"error": "invalid api key"})

    # -- 16. SignalForge visibly enters DEGRADED AI mode ----------------------
    refresh(api, token, incident_id)
    degraded_view = brain(api, token, incident_id)
    assert degraded_view["degraded"] is True
    assert degraded_view["degraded_reason"]
    assert any(run["status"] == "degraded" for run in degraded_view["runs"])

    gateway.default = httpx.Response(401, json={"error": "invalid api key"})
    degraded_answer = ask(api, token, incident_id, "what do we know?")
    assert degraded_answer["verdict"] == "degraded"
    assert degraded_answer["degraded"] is True
    assert degraded_answer["citations"], "the record is still listed, with citations"

    degraded_brief = read_brief(api, token, incident_id)
    assert degraded_brief["source"] == "degraded"

    # -- 17. incident operations continue deterministically -------------------
    api.clock.advance(minutes=1)
    say(api, token, incident_id, "I'll roll back region-b in ten minutes")
    # And a line the rules cannot place, so the conversation agent genuinely
    # reaches for the model during continued operation and is refused. A pass
    # that made no model call would report `degraded: false` quite correctly,
    # which is why the badge is read from the run trace rather than from the
    # last pass's return value.
    say(api, token, incident_id, "hmm, that region again")
    result = refresh(api, token, incident_id)
    assert result["degraded"] is True, "a model call made in this pass was refused"
    still = brain(api, token, incident_id)
    assert still["degraded"] is True, "and the badge stays on"
    # The ledger still filled, the hypotheses still have computed confidences,
    # and the history still explains every movement - with no model involved.
    assert len(still["hypotheses"]) >= 3
    assert all(0.0 < c["confidence"] < 1.0 for c in still["hypotheses"])
    assert all(c["explain"] for c in still["hypotheses"])
    commitments = [
        node
        for node in still["nodes"]
        if node["detail"].get("kind") == "commitment"
    ]
    assert commitments, "the bridge is still understood without a model"
    assert all(
        node["detail"]["classifier"] == "deterministic" for node in commitments
    )

    # -- 18. Sprint 4's incident controls are untouched -----------------------
    from signalforge.tests.brain.conftest import close_incident, read_incident

    room = war_room(api, token, incident_id)
    assert room["pages"] is not None and room["tasks"]
    assert read_incident(api, token, incident_id)["state"] == "active"
    closed = close_incident(api, token, incident_id, reason="pool restarted, validated")
    assert closed["state"] == "closed"
    # And the reasoning is kept, not deleted, when the incident ends.
    assert brain(api, token, incident_id)["nodes"]


def _confidences(view: dict) -> dict[str, float]:
    return {
        card["node"]["derived_key"]: card["confidence"] for card in view["hypotheses"]
    }
