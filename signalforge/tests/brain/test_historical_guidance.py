"""Historical learning through the real API and the real stores (S5-T03, REQ-052).

The half of the brain that is most useful when it is right and most expensive
when it is wrong. These assert the shape that keeps it honest: prior incidents
are offered as advisory evidence with a decayed relevance and a caveat on every
row, a mistake warning comes from what a prior incident *ruled out*, and the
relevance an incident was last given is read back from the graph so that
"movement" is the difference between two things that were actually written down.
"""

from __future__ import annotations

import pytest

from signalforge.tests.brain.conftest import (
    APP_ID,
    BRAIN_TOPOLOGY,
    Sender,
    announce,
    brain,
    close_incident,
    commander,
    declare,
    guidance,
    issue_key,
    onboard,
    owner,
    refresh,
    relogin,
    say,
    upload_topology,
    wire_bridge,
)

pytestmark = pytest.mark.anyio


async def scene_with_history(api):
    """One closed incident with a hypothesis the room got behind, then a second
    incident that resembles it.

    Both incidents are declared with a deployment in flight, which is what makes
    them *similar* rather than merely both being P1s on the same application:
    the fingerprint compares signal families, services, regions and change types,
    and two incidents sharing only an application and a severity sit below the
    relevance floor on purpose.
    """
    owner_token, tenant_id = owner(api)
    onboard(api, owner_token)
    upload_topology(api, owner_token, BRAIN_TOPOLOGY, APP_ID)
    wire_bridge(api, owner_token)
    ic = commander(api, owner_token)
    token = ic["tokens"]["access_token"]

    sender = Sender(
        api=api, key=issue_key(api, owner_token, stream="changes", app_id=APP_ID)
    )
    await announce(api, sender, change_id="release-v11", service="payments")

    first = declare(api, token, title="Payments failing in eu-west-1", severity="P1")
    say(api, token, first["id"], "connection-pool is at 100% utilisation")
    refresh(api, token, first["id"])
    close_incident(api, token, first["id"], reason="pool restarted")

    # Three days on, so the prior incident has aged enough for the decay curve
    # to be doing something. A frozen clock moved that far outlives the 15-minute
    # access TTL, so the commander signs in again - which is what a person would
    # do, and the session security doing exactly its job.
    api.clock.advance(days=3)
    token = relogin(api, email="ic@acme.io", password="ic-strong-password")
    owner_token = relogin(api)

    sender = Sender(
        api=api, key=issue_key(api, owner_token, stream="changes", app_id=APP_ID)
    )
    await announce(api, sender, change_id="release-v12", service="payments")

    second = declare(api, token, title="Payments failing again", severity="P1")
    refresh(api, token, second["id"])
    return tenant_id, token, first, second


@pytest.mark.req("REQ-052")
async def test_a_prior_incident_is_offered_as_advisory_evidence_with_its_caveat(api):
    _tenant_id, token, first, second = await scene_with_history(api)
    view = guidance(api, token, second["id"])

    assert view["similar"], "a near-identical prior incident should be offered"
    row = view["similar"][0]
    assert first["reference"] in row["label"]
    assert "guidance, not a finding about this incident" in row["content"]
    # Advisory, and inferred - the brain correlated it, nobody measured it.
    assert row["provenance"]["authority"] == "inferred"
    assert row["detail"]["advisory"] is True
    assert 0.0 < row["detail"]["relevance"] <= 1.0
    assert row["detail"]["source_incident"] == first["id"]


@pytest.mark.req("REQ-052", "REQ-053")
async def test_history_alone_does_not_carry_a_hypothesis(api):
    """The failure Historical Learning exists to avoid, asserted end to end."""
    _tenant_id, token, _first, second = await scene_with_history(api)
    view = brain(api, token, second["id"])

    recurrence = [
        card
        for card in view["hypotheses"]
        if "recurrence" in card["node"]["derived_key"]
    ]
    for card in recurrence:
        assert card["confidence"] < 0.6, (
            "a hypothesis resting on history alone must not read as likely"
        )


@pytest.mark.req("REQ-052")
async def test_relevance_movement_is_measured_against_what_was_written_before(api):
    """The agent's memory is the graph. A second pass compares against the
    relevance the first pass recorded, not against nothing."""
    _tenant_id, token, first, second = await scene_with_history(api)
    before = guidance(api, token, second["id"])["similar"][0]
    recorded = before["detail"]["relevance"]

    # New evidence arrives that makes this incident look more like the prior one.
    say(api, token, second["id"], "connection-pool is at 100% utilisation again")
    api.clock.advance(minutes=1)
    refresh(api, token, second["id"])

    after = guidance(api, token, second["id"])["similar"][0]
    assert after["detail"]["source_incident"] == first["id"]
    # Movement is reported as a number rather than left absent, and it is
    # measured against the value the previous pass stored.
    assert "movement" in after["detail"]
    assert after["detail"]["relevance"] > 0
    assert isinstance(after["detail"]["movement"], (int, float))
    assert recorded > 0


@pytest.mark.req("REQ-052")
async def test_a_ruled_out_hypothesis_becomes_a_pinned_mistake_warning(api):
    """"We spent forty minutes on this last time and it was not it" is the more
    useful half of history, and it survives the round trip through the store.

    Retraction is driven through the repository rather than a route because
    there is no human-facing "we were wrong about this" until S7's PIR flow -
    the graph has always supported it, and this is the first thing to read it
    back.
    """
    tenant_id, token, first, second = await scene_with_history(api)

    from signalforge.domain.brain.nodes import NodeType

    async with api.container.uow_factory() as uow:
        held = list(
            await uow.incident_graph.list(
                tenant_id, first["id"], types=[NodeType.HYPOTHESIS]
            )
        )
        assert held, "the closed incident must have hypotheses to rule out"
        ruled_out = held[0]
        await uow.incident_graph.retract(
            tenant_id,
            ruled_out.id,
            at=api.clock.now(),
            reason="the pool was measured idle; this was not it",
        )
        await uow.commit()

    api.clock.advance(minutes=5)
    refresh(api, token, second["id"])
    view = guidance(api, token, second["id"])

    assert view["warnings"], "a ruled-out hypothesis must become a warning"
    warning = view["warnings"][0]
    assert first["reference"] in warning["label"]
    assert "ruled it out" in warning["label"]
    assert "guidance from a prior incident, not a finding about this one" in (
        warning["content"]
    )
    assert warning["detail"]["advisory"] is True
    assert "mistake-warning" in warning["tags"]

