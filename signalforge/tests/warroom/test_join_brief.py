"""The join brief (S4-T03, REQ-045).

Template now, model in S5. The tests are about the thing that has to be true
whichever writes it: a brief drawn from thin evidence says so, and a brief is
labelled with who wrote it.

The `caveats` section gets the most attention here on purpose. A brief assembled
from a declaration made while the detector was down knows far less than one
assembled from a corroborated proposal, and a reader who cannot tell the two
apart will spend twenty minutes chasing the wrong region. Confidence that the
evidence does not support is the failure mode of every incident summary, and it
is the one an LLM will be most tempted to produce - so the deterministic version
establishes the standard the model has to meet.
"""

from __future__ import annotations

import pytest

from signalforge.tests.warroom.conftest import (
    APP_ID,
    ack,
    brief,
    commander,
    create_task,
    declare,
    join,
    onboard,
    owner,
    page_for,
    pages,
    responder,
    war_room,
    wire_paging,
)

pytestmark = pytest.mark.anyio


@pytest.mark.req("REQ-045")
async def test_a_brief_says_who_wrote_it(api):
    """`source` exists so that a reader can calibrate how much to trust the prose.

    Updated in S5-T03, when the model-backed writer was wired in. This container
    has no model configured, so the writer is *attempted* and falls back - which
    is `degraded`, not `template`. The distinction is the point rather than a
    detail: `template` now means "this node has no writer at all", and
    `degraded` means "there is one and it could not be used", and an incident
    commander reading a brief at 03:00 is entitled to know which.

    The text is unchanged either way: it is the same deterministic brief, and
    its caveats still say a model did not write it.
    """
    owner_token, _tenant = owner(api)
    onboard(api, owner_token)
    wire_paging(api, owner_token)
    ic = commander(api, owner_token)
    incident = declare(api, ic["tokens"]["access_token"], severity="P0")

    written = brief(api, ic["tokens"]["access_token"], incident["id"])
    assert written["source"] == "degraded"
    assert "not by a model" in " ".join(written["caveats"])


@pytest.mark.req("REQ-045")
async def test_a_brief_leads_with_what_a_phone_screen_shows_first(api):
    """Severity, application, reference, state and age, in the headline.

    On a locked phone that is all that is visible, and it is enough to decide
    whether to get out of bed.
    """
    owner_token, _tenant = owner(api)
    onboard(api, owner_token)
    wire_paging(api, owner_token)
    ic = commander(api, owner_token)
    incident = declare(
        api,
        ic["tokens"]["access_token"],
        severity="P0",
        title="Payments failing in region-b",
    )

    api.clock.advance(seconds=600)
    from signalforge.tests.warroom.conftest import relogin

    written = brief(api, relogin(api), incident["id"])
    headline = written["headline"]
    assert incident["reference"] in headline
    assert "P0" in headline and APP_ID in headline
    assert "active" in headline
    assert "10 minutes old" in headline
    assert "Payments failing in region-b" in headline


@pytest.mark.req("REQ-045")
async def test_a_brief_on_uncorroborated_evidence_calls_the_cause_a_hypothesis(api):
    """The caveat that matters most, through the real API.

    Nothing is bound, so the detector answers `steady` with no corroboration and
    no topology to narrow anything. The brief refuses to read as though it knows
    things: one signal family is not two, and the difference is stated rather
    than smoothed over.
    """
    owner_token, _tenant = owner(api)
    onboard(api, owner_token)
    wire_paging(api, owner_token)
    ic = commander(api, owner_token)
    incident = declare(api, ic["tokens"]["access_token"], severity="P0")

    written = brief(api, ic["tokens"]["access_token"], incident["id"])
    caveats = " ".join(written["caveats"])
    assert "not corroborated" in caveats
    assert "hypothesis" in caveats
    assert "No topology is mapped" in caveats


@pytest.mark.req("REQ-045")
def test_a_brief_written_while_the_detector_was_down_says_it_knows_nothing():
    """The other half, asserted at the level it lives at.

    A person declaring while detection is broken is the case that matters most -
    it is exactly when somebody most needs to - and the brief that results has to
    admit how little is behind it rather than reading like the corroborated one.
    """
    from datetime import datetime, timezone

    from signalforge.domain.incidents.entities import DeclaredProposal, Incident
    from signalforge.domain.incidents.lifecycle import LifecycleState
    from signalforge.domain.onboarding.profile import Severity
    from signalforge.domain.warroom.blast import BlastRadius
    from signalforge.domain.warroom.brief import build_brief

    now = datetime(2026, 9, 1, 3, 0, tzinfo=timezone.utc)
    incident = Incident(
        id="i1",
        tenant_id="t1",
        app_id=APP_ID,
        reference="INC-000001",
        ordinal=1,
        title="Payments failing",
        severity=Severity.P0,
        state=LifecycleState.ACTIVE,
        declared_at=now,
        declared_by="u1",
        updated_at=now,
        proposal=DeclaredProposal(
            stance="unknown",
            unavailable="detection is not enabled on this node",
        ),
    )
    written = build_brief(
        incident,
        radius=BlastRadius(
            app_id=APP_ID, severity=Severity.P0, source="declaration"
        ),
        now=now,
    )
    caveats = " ".join(written.caveats)
    assert "drawn from the declaration alone" in caveats
    assert "detection is not enabled on this node" in caveats
    assert "Nothing here has been corroborated" in caveats
    assert "the severity a person typed" in caveats
    assert "No detection verdict" in " ".join(written.what_we_know)


@pytest.mark.req("REQ-045")
async def test_a_brief_is_addressed_to_the_person_reading_it(api):
    """A responder wants to know what is being asked of *them*. A brief that read
    identically to everybody would make each reader work out their own part."""
    owner_token, _tenant = owner(api)
    onboard(api, owner_token)
    wire_paging(api, owner_token)
    ic = commander(api, owner_token)
    ic_token = ic["tokens"]["access_token"]
    sme = responder(api, owner_token, email="sme@acme.io", display_name="Sam SME")
    incident = declare(api, ic_token, severity="P0")

    create_task(
        api,
        ic_token,
        incident["id"],
        title="check the connection pool",
        owner_user_id=sme["user"]["id"],
    )
    create_task(api, ic_token, incident["id"], title="find out who deployed")

    theirs = brief(api, sme["tokens"]["access_token"], incident["id"])
    needed = " ".join(theirs["what_we_need"])
    assert "You were paged because" in needed
    assert "Yours: check the connection pool" in needed
    assert "Unowned: find out who deployed" in needed

    # The commander's copy names their own part, not the SME's.
    ours = brief(api, ic_token, incident["id"])
    assert "Yours: check the connection pool" not in " ".join(ours["what_we_need"])


@pytest.mark.req("REQ-045", "REQ-044")
async def test_a_brief_names_a_change_that_was_in_flight_as_the_first_thing_to_rule_out(
    api,
):
    """A deployment announced and never completed, reaching the brief by the same
    route an auditor replays: a signed batch at the frozen ingest path."""
    from signalforge.tests.incidents.conftest import announce, issue_key, Sender

    owner_token, _tenant = owner(api)
    onboard(api, owner_token)
    wire_paging(api, owner_token)
    sender = Sender(api, issue_key(api, owner_token, app_id=APP_ID, stream="changes"))
    await announce(api, sender, change_id="release-v12", status="started")

    ic = commander(api, owner_token)
    incident = declare(api, ic["tokens"]["access_token"], severity="P0")

    written = brief(api, ic["tokens"]["access_token"], incident["id"])
    context = " ".join(written["change_context"])
    assert "first thing to rule out" in context
    assert "release-v12" in context or "deployment" in context


@pytest.mark.req("REQ-045")
async def test_a_brief_records_what_has_already_been_done(api):
    """Somebody arriving at minute forty must not repeat the last forty minutes."""
    from signalforge.tests.warroom.conftest import finish_task, set_state

    owner_token, _tenant = owner(api)
    onboard(api, owner_token)
    wire_paging(api, owner_token)
    ic = commander(api, owner_token)
    ic_token = ic["tokens"]["access_token"]
    incident = declare(api, ic_token, severity="P1")

    task = create_task(api, ic_token, incident["id"], title="roll back region-b")
    finish_task(api, ic_token, incident["id"], task["id"])
    set_state(api, ic_token, incident["id"], "recovering", reason="rollback applied")

    written = brief(api, ic_token, incident["id"])
    done = " ".join(written["what_has_been_done"])
    assert "Done: roll back region-b" in done
    assert "active -> recovering" in done
    assert "rollback applied" in done
    # And a recovering incident tells the reader not to start new work.
    assert "regression" in " ".join(written["what_we_need"])


@pytest.mark.req("REQ-045")
async def test_the_flat_text_is_the_same_brief(api):
    """One place, so an SMS, a voice call and a screen cannot drift apart."""
    owner_token, _tenant = owner(api)
    onboard(api, owner_token)
    wire_paging(api, owner_token)
    ic = commander(api, owner_token)
    incident = declare(api, ic["tokens"]["access_token"], severity="P0")

    written = brief(api, ic["tokens"]["access_token"], incident["id"])
    assert written["text"].startswith(written["headline"])
    for section in ("what_is_happening", "impact", "caveats"):
        for line in written[section]:
            assert line in written["text"]
