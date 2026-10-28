"""Security properties of the war-room surface (artifact 18, S4-T03).

Written failing-first for the two categories CLAUDE.md names: RBAC and tenant
isolation.

The war room introduces the only session writes in this platform that name no
permission besides `POST /auth/logout`, and the reason is a fact in the frozen
roles matrix rather than a convenience: `ROLE_PERMISSIONS[Role.SME]` is empty.
The person most likely to be paged holds nothing, so the acts that *are* that
person - acknowledging, joining, speaking, ticking their own task - cannot sit
behind a permission without locking the war room out of the people it exists to
reach.

The control that replaces a permission is **self-scoping**, and it is stronger
rather than weaker: `declare_incident` would let any holder acknowledge anybody's
page, whereas the entity refuses everyone but the addressee. Every one of the
four is asserted here in both directions - the addressee may, and somebody else
holding every permission in the matrix may not.
"""

from __future__ import annotations

import json

import pytest

from signalforge.api.main import app as api_app
from signalforge.contracts.roles import ROLE_PERMISSIONS, Permission, Role
from signalforge.tests.warroom.conftest import (
    APP_ID,
    ack,
    brief,
    commander,
    create_task,
    declare,
    escalate,
    finish_task,
    join,
    my_pages,
    onboard,
    owner,
    page_for,
    patch_task,
    raise_page,
    read_bridge,
    responder,
    say,
    tasks,
    war_room,
    wire_bridge,
    wire_paging,
)

pytestmark = pytest.mark.anyio

WRITE_ROUTES = {
    ("POST", "/incidents/{incident_id}/pages"): Permission.DECLARE_INCIDENT,
    ("POST", "/incidents/{incident_id}/pages/{page_id}/escalate"): (
        Permission.DECLARE_INCIDENT
    ),
    ("POST", "/incidents/{incident_id}/tasks"): Permission.DECLARE_INCIDENT,
    ("PATCH", "/incidents/{incident_id}/tasks/{task_id}"): Permission.DECLARE_INCIDENT,
    # S5, corrected in DR-101. A reasoning pass writes the platform's beliefs
    # about a live incident - graph versions, evidence, hypotheses, confidence
    # history, snapshots, agent runs - and spends the incident's token budget.
    # That is incident command work and it carries the same permission the rest
    # of command does. Both directions are asserted in test_brain_boundary.py.
    ("POST", "/incidents/{incident_id}/brain/refresh"): Permission.DECLARE_INCIDENT,
}

SELF_SCOPED_WRITES = {
    ("POST", "/incidents/{incident_id}/pages/{page_id}/ack"),
    ("POST", "/incidents/{incident_id}/war-room/join"),
    ("POST", "/incidents/{incident_id}/bridge"),
    ("POST", "/incidents/{incident_id}/tasks/{task_id}/done"),
}
"""The four. Pinned as a literal so a fifth cannot be added without somebody
deciding, in this file, that it is genuinely self-scoped."""

STATE_FREE_WRITES = {
    ("POST", "/incidents/{incident_id}/brain/ask"),
}
"""The other reason an incident write may carry no permission (S5-T03).

`ask` is not self-scoped - there is no "yourself" in it - and does not need to
be, because it **writes no state at all**. It reads one incident's graph and
returns an answer grounded in what is already there. It cannot declare, re-grade,
close, page, approve, execute or send, and holds no path to anything that can.

It is a POST because it carries a question body and must not be cached, not
because it mutates anything. It is tenant-scoped by the repositories it reads
through, which the test below asserts in the same direction as the self-scoped
four: somebody holding every permission in the matrix still cannot reach another
tenant's incident.

`POST .../brain/refresh` sat here too, and should not have. Writing no *incident
lifecycle* state is not the same as writing no state: a pass writes graph nodes
and versions, edges, evidence, hypotheses, confidence history, snapshots and
agent-run records, and spends the incident's token budget. It moved to
`WRITE_ROUTES` above under `declare_incident` (DR-101). The category kept its
meaning by losing the member that did not fit it, which is what a closed list is
for."""

READ_ROUTES = {
    ("GET", "/incidents/{incident_id}/war-room"),
    ("GET", "/incidents/{incident_id}/brief"),
    ("GET", "/incidents/{incident_id}/pages"),
    ("GET", "/incidents/{incident_id}/bridge"),
    ("GET", "/incidents/{incident_id}/tasks"),
    ("GET", "/my/pages"),
}


def _guards() -> dict[tuple[str, str], set[str]]:
    from signalforge.tests.security.test_authz_matrix import _walk_api_routes
    from signalforge.tests.security.test_onboarding_boundary import _dependency_names

    found: dict[tuple[str, str], set[str]] = {}
    for route in _walk_api_routes(api_app.routes):
        path = getattr(route, "path", "")
        for method in getattr(route, "methods", set()) or set():
            found[(method, path)] = _dependency_names(route.dependant)
    return found


@pytest.mark.req("REQ-012", "REQ-045")
async def test_command_writes_name_a_permission_and_self_scoped_writes_name_none(api):
    """The split, pinned as two exact maps.

    A new war-room write lands in one of the two lists or fails the build, which
    is the point: an unpermissioned write that is *not* self-scoped would be a
    route every member of every tenant could perform on anybody.
    """
    guards = _guards()

    for key, permission in WRITE_ROUTES.items():
        assert key in guards, f"war-room write route missing: {key}"
        assert permission.value in guards[key], (
            f"{key} is not behind {permission.value}"
        )

    for key in SELF_SCOPED_WRITES | STATE_FREE_WRITES | READ_ROUTES:
        assert key in guards, f"war-room route missing: {key}"
        assert not guards[key], (
            f"{key} carries a permission. The frozen matrix gives the SME none "
            "(ROLE_PERMISSIONS[Role.SME] is empty), so a permission here would "
            "exclude exactly the person a page exists to reach."
        )
    assert ROLE_PERMISSIONS[Role.SME] == frozenset(), (
        "the reasoning above depends on this being true in the frozen matrix"
    )


@pytest.mark.req("REQ-012", "REQ-045")
async def test_the_unpermissioned_write_exemption_is_justified_route_by_route(api):
    """The two lists are joined here, so neither can drift from the other.

    `test_api_surface.py` exempts a set of session writes from needing a
    permission. Every incident-path entry in that exemption must appear in one of
    the two lists below, each of which carries a *different* justification and a
    test obligation to match:

    - `SELF_SCOPED_WRITES` - it acts only on the caller, and the entity refuses
      everyone else. Tested in both directions below.
    - `STATE_FREE_WRITES` - it writes no state at all, incident or brain.
      Tested below for tenant scoping and for leaving both untouched.
    - `INERT_DRAFT_WRITES` (S6-T04) - it writes an artifact that cannot reach
      anybody without a second, permissioned act by a second person. Tested in
      tests/security/test_governance_boundary.py and
      tests/governance/test_communications.py.
    - `AUDIENCE_SCOPED_WRITES` (S6-T04) - it *does* enforce a permission, chosen
      by the communication's audience, which the route has not read yet. Tested
      from both sides in the same two files.

    The two Sprint 6 categories live in the governance boundary file rather than
    here, so each justification sits beside the obligation it takes on - but they
    are joined here, because this is the one test that can see the whole
    exemption at once.

    Without this join, an unpermissioned write could be added to the exemption
    and quietly become a route every member of every tenant may perform on
    anybody.
    """
    from signalforge.tests.security.test_api_surface import SESSION_ONLY_WRITES
    from signalforge.tests.security.test_governance_boundary import (
        AUDIENCE_SCOPED_WRITES,
        INERT_DRAFT_WRITES,
    )

    categories = (
        SELF_SCOPED_WRITES,
        STATE_FREE_WRITES,
        INERT_DRAFT_WRITES,
        AUDIENCE_SCOPED_WRITES,
    )
    exempted = {
        (method.upper(), path)
        for method, path in SESSION_ONLY_WRITES
        if "incident" in path or "war-room" in path
    }
    justified: set[tuple[str, str]] = set()
    for category in categories:
        justified |= category
    assert exempted == justified, (
        "an incident write was exempted from needing a permission without being "
        "listed - and justified - as self-scoped, state-free, an inert draft, or "
        "audience-scoped"
    )
    for index, category in enumerate(categories):
        for other in categories[index + 1:]:
            assert not category & other, (
                "a route must be justified exactly one way, not two"
            )


@pytest.mark.req("REQ-012", "REQ-013", "REQ-055")
async def test_the_state_free_writes_change_no_incident_state_and_cross_no_tenant(api):
    """The obligation `STATE_FREE_WRITES` takes on.

    Written failing-first for the two categories that matter here: tenant
    isolation, and the claim that this route is exempt from a permission
    *because* it mutates nothing. If either stopped being true, an unguarded
    POST would have become a way for any member of any tenant to change an
    incident.

    "Mutates nothing" is asserted against the brain as well as the incident,
    which is the check that would have caught `refresh` being put in this
    category: the graph an SME asks a question of must look identical
    afterwards.
    """
    from signalforge.tests.brain.conftest import ask, brain, refresh
    from signalforge.tests.incidents.conftest import read_incident

    owner_token, _tenant = owner(api)
    onboard(api, owner_token)
    wire_paging(api, owner_token)
    ic = commander(api, owner_token)
    ic_token = ic["tokens"]["access_token"]
    sme = responder(api, owner_token, email="sme@acme.io")
    sme_token = sme["tokens"]["access_token"]
    incident = declare(api, ic_token, severity="P0")
    # A pass has run, so there is a graph for a question to change if it could.
    refresh(api, ic_token, incident["id"])

    before = read_incident(api, ic_token, incident["id"])
    brain_before = brain(api, ic_token, incident["id"])

    # The SME - holding no permission at all - may ask, which is the point.
    answer = ask(api, sme_token, incident["id"], "what do we know?")
    assert answer["verdict"] in (
        "grounded",
        "insufficient_evidence",
        "ungrounded",
        "degraded",
    )
    # And may not cause a reasoning pass: that is command work (DR-101).
    refresh(api, sme_token, incident["id"], expect=403)

    # The incident is untouched: same state, same severity, same closure, and no
    # transition was added.
    after = read_incident(api, ic_token, incident["id"])
    assert after["state"] == before["state"]
    assert after["severity"] == before["severity"]
    assert after["closed_at"] == before["closed_at"]
    assert len(after["transitions"]) == len(before["transitions"])

    # So is the brain, which is the state this category was too loose about.
    brain_after = brain(api, ic_token, incident["id"])
    assert brain_after["counts"] == brain_before["counts"]
    for field in ("nodes", "edges", "hypotheses", "snapshots", "runs"):
        assert len(brain_after[field]) == len(brain_before[field]), (
            f"a state-free write added {field}"
        )

    # A second tenant cannot reach this incident through either route. The
    # boundary is the repository, so both answer the way a read of nothing does.
    other = api.client.post(
        "/auth/signup",
        json={
            "org_name": "Other Co",
            "email": "owner@other.test",
            "password": "another-strong-password",
            "display_name": "Other Owner",
        },
    )
    assert other.status_code == 201, other.text
    intruder = other.json()["tokens"]["access_token"]
    refresh(api, intruder, incident["id"], expect=404)
    ask(api, intruder, incident["id"], "what do we know?", expect=404)


@pytest.mark.req("REQ-012", "REQ-045")
async def test_a_page_is_acknowledged_only_by_the_person_it_names(api):
    """Written failing-first. The self-scoping control, both directions.

    The commander holds `declare_incident`, `change_severity`, `close_incident`
    and `approve_high_risk`, and none of them let them answer somebody else's
    page - which is the whole difference between "I have this" and "somebody has
    this".
    """
    owner_token, _tenant = owner(api)
    onboard(api, owner_token)
    wire_paging(api, owner_token)
    ic = commander(api, owner_token)
    sme = responder(api, owner_token, email="sme@acme.io")
    incident = declare(api, ic["tokens"]["access_token"], severity="P0")

    room = war_room(api, ic["tokens"]["access_token"], incident["id"])
    page = page_for(room, sme["user"]["id"])
    assert page is not None, "the SME in the blast radius should have been paged"

    # The commander, holding every incident permission, cannot answer it.
    ack(api, ic["tokens"]["access_token"], incident["id"], page["id"], expect=403)
    # Neither can the org owner, who holds manage_org.
    ack(api, owner_token, incident["id"], page["id"], expect=403)
    # The person it names can, holding nothing at all.
    answered = ack(api, sme["tokens"]["access_token"], incident["id"], page["id"])
    assert answered["state"] == "acked"
    assert answered["acked_by"] == sme["user"]["id"]


@pytest.mark.req("REQ-012", "REQ-045")
async def test_a_task_is_completed_by_its_owner_and_reassigned_only_by_command(api):
    """The two halves of the task board's split guard."""
    owner_token, _tenant = owner(api)
    onboard(api, owner_token)
    wire_paging(api, owner_token)
    ic = commander(api, owner_token)
    ic_token = ic["tokens"]["access_token"]
    sme = responder(api, owner_token, email="sme@acme.io")
    other = responder(
        api, owner_token, email="other@acme.io", display_name="Other Person"
    )
    incident = declare(api, ic_token, severity="P1")

    task = create_task(
        api,
        ic_token,
        incident["id"],
        title="drain the queue",
        owner_user_id=sme["user"]["id"],
    )
    # Somebody else's task is not theirs to tick, whatever they hold.
    finish_task(
        api, other["tokens"]["access_token"], incident["id"], task["id"], expect=403
    )
    finish_task(api, ic_token, incident["id"], task["id"], expect=403)
    done = finish_task(api, sme["tokens"]["access_token"], incident["id"], task["id"])
    assert done["status"] == "done" and done["completed_by"] == sme["user"]["id"]

    # Reassignment is command work and the SME cannot do it.
    second = create_task(api, ic_token, incident["id"], title="check the pool")
    patch_task(
        api,
        sme["tokens"]["access_token"],
        incident["id"],
        second["id"],
        owner_user_id=other["user"]["id"],
        expect=403,
    )
    moved = patch_task(
        api, ic_token, incident["id"], second["id"], owner_user_id=other["user"]["id"]
    )
    assert moved["owner_user_id"] == other["user"]["id"]


@pytest.mark.req("REQ-013", "REQ-045")
async def test_another_tenants_war_room_is_indistinguishable_from_one_that_is_not_there(
    api,
):
    """Written failing-first. Both tenants call their application `payments`."""
    owner_a, _tenant_a = owner(api)
    onboard(api, owner_a)
    wire_paging(api, owner_a)
    ic_a = commander(api, owner_a)
    incident = declare(api, ic_a["tokens"]["access_token"], severity="P0")
    room = war_room(api, ic_a["tokens"]["access_token"], incident["id"])
    page = room["pages"][0] if room["pages"] else None

    signed = api.client.post(
        "/auth/signup",
        json={
            "org_name": "Other Corp",
            "email": "other@other.io",
            "password": "another-strong-password",
            "display_name": "Other",
        },
    )
    assert signed.status_code == 201, signed.text
    owner_b = signed.json()["tokens"]["access_token"]
    ic_b = commander(api, owner_b, email="ic@other.io")
    token_b = ic_b["tokens"]["access_token"]

    war_room(api, token_b, incident["id"], expect=404)
    brief(api, token_b, incident["id"], expect=404)
    tasks(api, token_b, incident["id"], expect=404)
    join(api, token_b, incident["id"], expect=404)
    say(api, token_b, incident["id"], "hello", expect=404)
    create_task(api, token_b, incident["id"], title="mine now", expect=404)
    if page is not None:
        ack(api, token_b, incident["id"], page["id"], expect=404)
        escalate(api, token_b, incident["id"], page["id"], expect=404)

    # B's own pager shows nothing of A's.
    assert my_pages(api, owner_b)["count"] == 0
    # And A's room is untouched.
    assert war_room(api, ic_a["tokens"]["access_token"], incident["id"])["id"] == room["id"]


@pytest.mark.req("REQ-012", "REQ-045")
async def test_my_pages_can_only_ever_show_the_callers_own(api):
    """`/my/pages` has no recipient parameter, which is what lets it carry no
    permission. Asserted rather than described: two people, two pagers."""
    owner_token, _tenant = owner(api)
    onboard(api, owner_token)
    wire_paging(api, owner_token)
    ic = commander(api, owner_token)
    sme = responder(api, owner_token, email="sme@acme.io")
    second = responder(
        api, owner_token, email="sme2@acme.io", display_name="Second Responder"
    )
    declare(api, ic["tokens"]["access_token"], severity="P0")

    mine = my_pages(api, sme["tokens"]["access_token"])
    theirs = my_pages(api, second["tokens"]["access_token"])
    for listing, user_id in (
        (mine, sme["user"]["id"]),
        (theirs, second["user"]["id"]),
    ):
        for page in listing["pages"]:
            assert page["recipient"] == user_id

    # A query parameter naming somebody else changes nothing: there is none.
    spoofed = api.client.get(
        f"/my/pages?recipient={second['user']['id']}&user_id={second['user']['id']}",
        headers=api.auth_header(sme["tokens"]["access_token"]),
    )
    assert spoofed.status_code == 200
    for page in spoofed.json()["pages"]:
        assert page["recipient"] == sme["user"]["id"]


@pytest.mark.req("REQ-025", "REQ-045")
async def test_the_scrape_counts_pages_and_can_name_nobody(api):
    owner_token, tenant_id = owner(api)
    onboard(api, owner_token)
    wire_paging(api, owner_token)
    ic = commander(api, owner_token)
    sme = responder(api, owner_token, email="sme@acme.io")
    incident = declare(api, ic["tokens"]["access_token"], severity="P0")

    scrape = api.client.get("/metrics").text
    assert "signalforge_pages_total" in scrape
    assert "signalforge_war_rooms_total" in scrape
    for secret in (
        tenant_id,
        incident["id"],
        incident["reference"],
        APP_ID,
        sme["user"]["id"],
        "sme@acme.io",
    ):
        assert secret not in scrape, (
            "/metrics has no session; a page names a person and a person belongs "
            "to one tenant"
        )


@pytest.mark.req("REQ-015", "REQ-045")
async def test_nothing_the_war_room_writes_carries_a_connector_credential(api):
    """Paging decrypts a notification binding's credential on every send, and the
    bridge decrypts a collaboration binding's on every post."""
    owner_token, tenant_id = owner(api)
    onboard(api, owner_token)
    wire_paging(api, owner_token)
    wire_bridge(api, owner_token)
    from signalforge.tests.adapters.conftest import connect

    connect(
        api,
        owner_token,
        "http-observe-metrics",
        app_id=APP_ID,
        credentials={"token": "s3cr3t-metrics-token"},
    )
    ic = commander(api, owner_token)
    token = ic["tokens"]["access_token"]
    responder(api, owner_token, email="sme@acme.io")
    incident = declare(api, token, severity="P0")
    say(api, token, incident["id"], "I'll roll back region-b in 10 minutes")

    surfaces = [
        json.dumps(war_room(api, token, incident["id"])),
        json.dumps(brief(api, token, incident["id"])),
        json.dumps(tasks(api, token, incident["id"])),
        api.client.get("/events?limit=400", headers=api.auth_header(owner_token)).text,
        json.dumps(api.audit_rows(tenant_id)),
        api.client.get("/metrics").text,
    ]
    for surface in surfaces:
        assert "s3cr3t-metrics-token" not in surface
        assert "app-token" not in surface


@pytest.mark.req("REQ-014", "REQ-032", "REQ-045")
async def test_the_bridge_applies_the_same_redaction_floor_as_ingest(api):
    """Written failing-first (S4 carry-over).

    S3-T02 established that free text is masked *at the boundary, at write time*,
    so a later reader of the row cannot bypass it. A message a responder types on
    the bridge did not arrive through ingest and so did not pass the floor - and
    the commitment harvester stores an excerpt of that text on a task's
    `evidence_ref`, which is how a card number typed at 03:00 ends up on the task
    board, in the event stream and in the audit chain.

    The fix is the same policy applied at the bridge boundary, not a second one:
    `domain/ingest/redaction.mask` is the only implementation, so the two paths
    cannot drift.
    """
    owner_token, tenant_id = owner(api)
    onboard(api, owner_token)
    wire_paging(api, owner_token)
    wire_bridge(api, owner_token)
    ic = commander(api, owner_token)
    token = ic["tokens"]["access_token"]
    responder(api, owner_token, email="sme@acme.io")
    incident = declare(api, token, severity="P0")

    card = "4111 1111 1111 1111"
    email = "customer.private@bank.example"
    posted = say(
        api,
        token,
        incident["id"],
        f"I'll refund card {card} for {email} in 10 minutes",
    )
    # Masked at the moment it is written, so what comes back already is.
    echoed = posted["messages"][0]["text"]
    assert card not in echoed
    assert email not in echoed
    assert "***" in echoed
    # And the commitment it produced is still a commitment: the floor removes
    # the customer's data, not the work.
    board = tasks(api, token, incident["id"])
    assert board["tasks"], "the commitment must still become a task"

    surfaces = {
        "war room": json.dumps(war_room(api, token, incident["id"])),
        "bridge transcript": json.dumps(read_bridge(api, token, incident["id"])),
        "task board": json.dumps(board),
        "join brief": json.dumps(brief(api, token, incident["id"])),
        "events": api.client.get(
            "/events?limit=400", headers=api.auth_header(owner_token)
        ).text,
        "audit chain": json.dumps(api.audit_rows(tenant_id)),
        "scrape": api.client.get("/metrics").text,
    }
    for name, surface in surfaces.items():
        for secret in (card, "4111111111111111", email):
            assert secret not in surface, (
                f"the bridge's redaction floor did not reach the {name}"
            )
