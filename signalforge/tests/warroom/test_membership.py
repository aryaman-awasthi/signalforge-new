"""Who is in the room, and why (S4-T03, REQ-045).

Two layers, tested at the level each actually lives at. `required_roles` is a
pure table over the blast radius and is asserted as a table - a rule somebody can
argue with, in one place, without a database. Resolving those roles to people
needs bindings and is driven through the real API: an incident declared by a real
session, against an application registered through `POST /apps`.

The membership *gap* gets as much attention as the membership. A P0 whose comms
lead seat is empty is the case this design exists for: the room says so on its
face, on the timeline and in the audit chain, rather than quietly rendering a
shorter list that looks complete.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from signalforge.contracts.canonical import (
    TopologyEdge,
    TopologyEdgeType,
    TopologyNode,
    TopologyNodeType,
    TopologySnapshot,
)
from signalforge.contracts.roles import Role
from signalforge.domain.onboarding.profile import Severity
from signalforge.domain.warroom.blast import (
    BlastRadius,
    downstream_services,
    required_roles,
)
from signalforge.tests.warroom.conftest import (
    APP_ID,
    commander,
    declare,
    join,
    onboard,
    owner,
    responder,
    topics,
    war_room,
    wire_paging,
)

pytestmark = pytest.mark.anyio

NOW = datetime(2026, 9, 1, 3, 0, tzinfo=timezone.utc)


def radius(**overrides) -> BlastRadius:
    fields = {
        "app_id": APP_ID,
        "severity": Severity.P2,
        "regions": ("region-a",),
        "regions_total": 2,
    }
    fields.update(overrides)
    return BlastRadius(**fields)


def roles_for(**overrides) -> set[Role]:
    return {requirement.role for requirement in required_roles(radius(**overrides))}


# --- the table ----------------------------------------------------------------


@pytest.mark.req("REQ-045")
def test_every_room_has_a_commander_and_a_technical_owner():
    """The two unconditional rows. A room without a commander is a crowd."""
    for severity in Severity:
        assert {Role.INCIDENT_COMMANDER, Role.TECH_LEAD} <= roles_for(
            severity=severity
        ), f"{severity.value} must still call for command and technical ownership"


@pytest.mark.req("REQ-045")
def test_an_executive_is_present_for_a_p0_and_never_paged():
    """Being in the room and being woken are different things, and conflating
    them is how executives stop reading their notifications."""
    requirements = {
        requirement.role: requirement
        for requirement in required_roles(radius(severity=Severity.P0))
    }
    assert Role.EXECUTIVE in requirements
    assert requirements[Role.EXECUTIVE].paged is False
    assert requirements[Role.INCIDENT_COMMANDER].paged is True

    # A P1 in one of two regions does not reach leadership.
    assert Role.EXECUTIVE not in roles_for(severity=Severity.P1)
    # A P1 across the whole estate does.
    assert Role.EXECUTIVE in roles_for(
        severity=Severity.P1, regions=("region-a", "region-b"), regions_total=2
    )


@pytest.mark.req("REQ-045")
def test_a_comms_lead_is_called_exactly_when_a_customer_is_feeling_it():
    assert Role.COMMS_LEAD not in roles_for(customer_facing=False)
    assert Role.COMMS_LEAD in roles_for(customer_facing=True)
    # Severity does not substitute for customer impact in either direction.
    assert Role.COMMS_LEAD not in roles_for(severity=Severity.P0, customer_facing=False)


@pytest.mark.req("REQ-045")
def test_the_auditor_and_the_org_owner_are_never_required():
    """Deliberate, and pinned so that adding either becomes a decision.

    The auditor is read-only by the frozen matrix and can change nothing during
    an incident; the org owner is an administrative role. Anybody who should be
    in the room operationally holds an operational role, and the binding says so.
    """
    for severity in Severity:
        for customer_facing in (True, False):
            called = roles_for(severity=severity, customer_facing=customer_facing)
            assert Role.AUDITOR not in called
            assert Role.ORG_OWNER not in called


@pytest.mark.req("REQ-045")
def test_an_sme_is_called_by_what_is_downstream_and_by_severity():
    assert Role.SME in roles_for(services=("checkout",))
    assert Role.SME in roles_for(journeys=("card-payment",))
    assert Role.SME in roles_for(severity=Severity.P0)
    # A P3 with nothing downstream and no journey named does not wake an SME.
    assert Role.SME not in roles_for(severity=Severity.P3)


# --- the graph walk -----------------------------------------------------------


def graph() -> TopologySnapshot:
    """A small estate: the application, two things that call it, and one thing it
    calls. `depends_on` edges read source-depends-on-target."""
    return TopologySnapshot(
        version="v1",
        captured_at=NOW,
        nodes=[
            TopologyNode(
                node_id="payments-api",
                name="payments-api",
                node_type=TopologyNodeType.SERVICE,
                region="region-a",
                labels={"app": APP_ID},
            ),
            TopologyNode(
                node_id="checkout",
                name="checkout",
                node_type=TopologyNodeType.SERVICE,
                region="region-a",
            ),
            TopologyNode(
                node_id="storefront",
                name="storefront",
                node_type=TopologyNodeType.SERVICE,
                region="region-a",
            ),
            TopologyNode(
                node_id="ledger-db",
                name="ledger-db",
                node_type=TopologyNodeType.DATABASE,
                region="region-a",
            ),
        ],
        edges=[
            TopologyEdge(
                source="checkout",
                target="payments-api",
                edge_type=TopologyEdgeType.DEPENDS_ON,
            ),
            TopologyEdge(
                source="storefront",
                target="checkout",
                edge_type=TopologyEdgeType.DEPENDS_ON,
            ),
            TopologyEdge(
                source="payments-api",
                target="ledger-db",
                edge_type=TopologyEdgeType.DEPENDS_ON,
            ),
        ],
    )


@pytest.mark.req("REQ-045")
def test_the_blast_radius_walks_to_dependents_and_not_to_dependencies():
    """The direction is the whole point.

    Walking forwards would answer "what does payments need", which is a different
    and equally interesting question. Blast radius means "what breaks when
    payments does", and answering the wrong one by accident is exactly the sort
    of quiet wrongness a war room cannot afford.
    """
    services, truncated = downstream_services(graph(), app_id=APP_ID)
    assert services == ("checkout", "storefront")
    assert "ledger-db" not in services, (
        "ledger-db is what payments depends on, not what depends on payments"
    )
    assert truncated is False


@pytest.mark.req("REQ-045")
def test_an_unmapped_application_produces_no_radius_rather_than_the_whole_estate():
    """With no node matching the application, the walk finds nothing and says so.

    Seeding from every node instead would call the entire estate a blast radius,
    which reads as a catastrophe on every incident and therefore on none.
    """
    services, truncated = downstream_services(graph(), app_id="settlement")
    assert services == () and truncated is False
    assert downstream_services(None, app_id=APP_ID) == ((), False)


@pytest.mark.req("REQ-045")
def test_a_truncated_walk_says_it_was_truncated():
    """An under-stated blast radius looks exactly like a small one."""
    big = TopologySnapshot(
        version="v1",
        captured_at=NOW,
        nodes=[
            TopologyNode(
                node_id="payments-api",
                name="payments-api",
                node_type=TopologyNodeType.SERVICE,
                labels={"app": APP_ID},
            )
        ]
        + [
            TopologyNode(
                node_id=f"svc-{index}",
                name=f"svc-{index}",
                node_type=TopologyNodeType.SERVICE,
            )
            for index in range(10)
        ],
        edges=[
            TopologyEdge(source=f"svc-{index}", target="payments-api")
            for index in range(10)
        ],
    )
    services, truncated = downstream_services(big, app_id=APP_ID, max_services=4)
    assert len(services) == 4 and truncated is True


# --- resolving roles to people ------------------------------------------------


@pytest.mark.req("REQ-045")
async def test_declaring_opens_a_room_holding_the_people_the_radius_called_for(api):
    """Creation on declare, through the real API. No room is opened by hand."""
    owner_token, _tenant = owner(api)
    onboard(api, owner_token)
    wire_paging(api, owner_token)
    ic = commander(api, owner_token)
    sme = responder(api, owner_token, email="sme@acme.io", display_name="Sam SME")
    lead = responder(
        api,
        owner_token,
        email="lead@acme.io",
        roles=("tech_lead",),
        display_name="Lee Lead",
    )
    incident = declare(api, ic["tokens"]["access_token"], severity="P0")

    room = war_room(api, ic["tokens"]["access_token"], incident["id"])
    members = {member["user_id"]: member for member in room["members"]}
    assert sme["user"]["id"] in members
    assert lead["user"]["id"] in members
    assert ic["user"]["id"] in members

    # Every member carries the sentence that says why they are there. Somebody
    # woken at 03:00 is told why *they* were.
    assert all(member["reason"] for member in room["members"])
    assert members[lead["user"]["id"]]["role"] == "tech_lead"
    assert members[lead["user"]["id"]]["required"] is True

    # The declarer is in the room and present, whatever their bindings say.
    assert members[ic["user"]["id"]]["present"] is True


@pytest.mark.req("REQ-045")
async def test_a_required_role_nobody_holds_is_a_stated_gap(api):
    """The failure this design exists to make visible.

    Nobody is bound as a comms lead, so a customer-facing P0 opens a room that
    says so - on the room, on the timeline and in the audit chain - rather than
    rendering a shorter member list that looks complete.
    """
    owner_token, tenant_id = owner(api)
    onboard(api, owner_token)
    wire_paging(api, owner_token)
    ic = commander(api, owner_token)
    incident = declare(api, ic["tokens"]["access_token"], severity="P0")

    room = war_room(api, ic["tokens"]["access_token"], incident["id"])
    gaps = {gap["role"] for gap in room["gaps"]}
    assert "tech_lead" in gaps, "nobody is bound as a tech lead"
    assert "sme" in gaps
    for gap in room["gaps"]:
        assert gap["reason"], "a gap says which role and why the radius wanted it"

    published = topics(api, owner_token, "warroom.membership.gap")
    assert {event["payload"]["role"] for event in published} == gaps
    assert "warroom.membership.gap" in api.audit_topics(tenant_id)


@pytest.mark.req("REQ-045")
async def test_a_person_holding_two_required_roles_is_one_person_in_the_room(api):
    owner_token, _tenant = owner(api)
    onboard(api, owner_token)
    wire_paging(api, owner_token)
    both = responder(
        api,
        owner_token,
        email="both@acme.io",
        roles=("incident_commander", "tech_lead"),
        display_name="Dana Double",
    )
    incident = declare(api, both["tokens"]["access_token"], severity="P1")

    room = war_room(api, both["tokens"]["access_token"], incident["id"])
    appearances = [
        member for member in room["members"] if member["user_id"] == both["user"]["id"]
    ]
    assert len(appearances) == 1
    # The earlier requirement wins the reason: commander is the more senior
    # reason to be in the room than technical ownership.
    assert appearances[0]["role"] == "incident_commander"


@pytest.mark.req("REQ-045")
async def test_a_binding_scoped_to_another_application_does_not_join_the_room(api):
    """A per-application role binding is evaluated against the application the
    incident is about, exactly as it is everywhere else in this platform."""
    owner_token, _tenant = owner(api)
    onboard(api, owner_token, app_id=APP_ID)
    onboard(api, owner_token, app_id="settlement")
    wire_paging(api, owner_token)
    ic = commander(api, owner_token)
    elsewhere = responder(
        api,
        owner_token,
        email="elsewhere@acme.io",
        roles=("sme",),
        app_id="settlement",
        display_name="Sal Settlement",
    )
    here = responder(
        api, owner_token, email="here@acme.io", roles=("sme",), app_id=APP_ID
    )
    incident = declare(api, ic["tokens"]["access_token"], app_id=APP_ID, severity="P0")

    room = war_room(api, ic["tokens"]["access_token"], incident["id"])
    ids = {member["user_id"] for member in room["members"]}
    assert here["user"]["id"] in ids
    assert elsewhere["user"]["id"] not in ids


@pytest.mark.req("REQ-045")
async def test_joining_is_recorded_once_and_marks_the_room_present(api):
    """A screen polling every ten seconds must not fill the timeline people are
    reading during an incident."""
    owner_token, _tenant = owner(api)
    onboard(api, owner_token)
    wire_paging(api, owner_token)
    ic = commander(api, owner_token)
    sme = responder(api, owner_token, email="sme@acme.io")
    incident = declare(api, ic["tokens"]["access_token"], severity="P0")

    token = sme["tokens"]["access_token"]
    first = join(api, token, incident["id"], note="picking up the database angle")
    joined = {member["user_id"]: member for member in first["members"]}
    assert joined[sme["user"]["id"]]["present"] is True
    assert first["present_count"] >= 2  # the declarer and the SME

    join(api, token, incident["id"])
    join(api, token, incident["id"])
    events = [
        event
        for event in topics(api, owner_token, "warroom.member.joined")
        if event["payload"]["user_id"] == sme["user"]["id"]
    ]
    assert len(events) == 1, "only the first arrival is an event"


@pytest.mark.req("REQ-045")
async def test_the_room_names_the_services_downstream_of_the_mapped_application(api):
    """Written failing-first. The room's topology loader read an attribute the
    stored version does not have, swallowed the AttributeError as "the stored
    topology could not be read", and every room opened with no downstream
    services however good the map was."""
    from signalforge.tests.onboarding.conftest import upload_topology

    owner_token, _tenant = owner(api)
    onboard(api, owner_token)
    upload_topology(
        api,
        owner_token,
        {
            "version": "v1",
            "captured_at": "2026-09-01T11:00:00+00:00",
            "nodes": [
                {"node_id": APP_ID, "name": APP_ID, "node_type": "service"},
                {"node_id": "mobile-bff", "name": "mobile-bff", "node_type": "service"},
                {"node_id": "ledger-db", "name": "ledger-db", "node_type": "database"},
            ],
            "edges": [
                {"source": "mobile-bff", "target": APP_ID, "edge_type": "depends_on"},
                {"source": APP_ID, "target": "ledger-db", "edge_type": "depends_on"},
            ],
        },
        APP_ID,
    )
    ic = commander(api, owner_token)
    incident = declare(api, ic["tokens"]["access_token"], severity="P1")

    room = war_room(api, ic["tokens"]["access_token"], incident["id"])
    assert room["blast_radius"]["services"] == ["mobile-bff"], (
        "what depends on the application is its blast radius; what it depends "
        "on is not"
    )
