"""HTTP replay, causal chain and seal endpoints, with RBAC and tenant scoping
(REQ-023, REQ-012, REQ-013)."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.anyio


async def _seed(api, *, tenant_id: str, incident_id: str = "inc-1"):
    """Publish a small causal chain straight through the container's bus so the
    HTTP tests exercise real stored events."""
    from signalforge.contracts.envelope import Plane
    from signalforge.tests.events.conftest import make_event

    container = api.container
    published = []
    parent = None
    for topic in ("alert.fired", "incident.declared", "incident.paged"):
        envelope = make_event(
            topic=topic,
            tenant_id=tenant_id,
            incident_id=incident_id,
            plane=Plane.INCIDENT,
            causation_id=parent.event_id if parent else None,
        )
        async with container.event_uow_factory() as uow:
            stored = await container.bus.publish(uow, envelope)
            await uow.commit()
        published.append(stored)
        parent = stored
    return published


@pytest.mark.req("REQ-023", "REQ-012")
async def test_replay_over_http_returns_the_chain_in_order(api):
    owner = api.signup()
    events = await _seed(api, tenant_id=owner["org"]["id"])

    response = api.client.get("/events", headers=api.auth_header(owner["tokens"]["access_token"]))
    assert response.status_code == 200, response.text
    body = response.json()

    assert [e["topic"] for e in body["events"]] == [
        "alert.fired",
        "incident.declared",
        "incident.paged",
    ]
    assert body["count"] == 3 and body["has_more"] is False
    assert body["events"][0]["seq"] == 1
    assert body["events"][0]["payload_hash"]
    assert body["events"][1]["prev_hash"] == body["events"][0]["payload_hash"]

    chain = api.client.get(
        f"/events/{events[2].event_id}/chain",
        headers=api.auth_header(owner["tokens"]["access_token"]),
    )
    assert chain.status_code == 200
    assert [e["topic"] for e in chain.json()["events"]] == [
        "alert.fired",
        "incident.declared",
        "incident.paged",
    ]


@pytest.mark.req("REQ-023")
async def test_replay_bounds_and_filters_over_http(api):
    owner = api.signup()
    await _seed(api, tenant_id=owner["org"]["id"])
    headers = api.auth_header(owner["tokens"]["access_token"])

    paged = api.client.get("/events?limit=2", headers=headers).json()
    assert paged["count"] == 2 and paged["has_more"] is True

    rest = api.client.get(
        f"/events?after_seq={paged['next_after_seq']}", headers=headers
    ).json()
    assert [e["topic"] for e in rest["events"]] == ["incident.paged"]

    until = api.client.get("/events?until=2", headers=headers).json()
    assert [e["seq"] for e in until["events"]] == [1, 2]

    topics = api.client.get("/events?topics=alert.fired", headers=headers).json()
    assert topics["count"] == 1

    scoped = api.client.get("/events?incident_id=inc-1", headers=headers).json()
    assert scoped["count"] == 3

    bad = api.client.get("/events?after_seq=5&until=2", headers=headers)
    assert bad.status_code == 422


@pytest.mark.req("REQ-012")
async def test_replay_requires_read_audit(api):
    owner = api.signup()
    await _seed(api, tenant_id=owner["org"]["id"])
    executive = api.invite_and_accept(
        owner["tokens"]["access_token"], email="exec@acme.io", roles=["executive"]
    )

    denied = api.client.get(
        "/events", headers=api.auth_header(executive["tokens"]["access_token"])
    )
    assert denied.status_code == 403
    assert denied.json()["permission"] == "read_audit"

    auditor = api.invite_and_accept(
        owner["tokens"]["access_token"], email="auditor@acme.io", roles=["auditor"]
    )
    allowed = api.client.get(
        "/events", headers=api.auth_header(auditor["tokens"]["access_token"])
    )
    assert allowed.status_code == 200


@pytest.mark.req("REQ-013")
async def test_replay_only_returns_this_tenants_events(api):
    owner = api.signup()
    await _seed(api, tenant_id=owner["org"]["id"])

    other = api.signup(org="Other Inc", email="owner@other.io")
    body = api.client.get(
        "/events", headers=api.auth_header(other["tokens"]["access_token"])
    ).json()
    assert body["count"] == 0


@pytest.mark.req("REQ-023", "REQ-012")
async def test_sealing_is_incident_command_work_and_verifies(api):
    owner = api.signup()
    await _seed(api, tenant_id=owner["org"]["id"])

    commander = api.invite_and_accept(
        owner["tokens"]["access_token"], email="ic@acme.io", roles=["incident_commander"]
    )
    auditor = api.invite_and_accept(
        owner["tokens"]["access_token"], email="auditor@acme.io", roles=["auditor"]
    )

    denied = api.client.post(
        "/events/seals",
        json={"incident_id": "inc-1"},
        headers=api.auth_header(auditor["tokens"]["access_token"]),
    )
    assert denied.status_code == 403, "the auditor stays read-only"
    assert denied.json()["permission"] == "close_incident"

    created = api.client.post(
        "/events/seals",
        json={"incident_id": "inc-1"},
        headers=api.auth_header(commander["tokens"]["access_token"]),
    )
    assert created.status_code == 201, created.text
    seal = created.json()
    assert seal["event_count"] == 3 and seal["from_seq"] == 1 and seal["to_seq"] == 3

    verified = api.client.post(
        f"/events/seals/{seal['id']}/verify",
        headers=api.auth_header(auditor["tokens"]["access_token"]),
    )
    assert verified.status_code == 200
    assert verified.json()["ok"] is True

    listed = api.client.get(
        "/events/seals", headers=api.auth_header(auditor["tokens"]["access_token"])
    ).json()
    assert [s["id"] for s in listed] == [seal["id"]]


@pytest.mark.req("REQ-023")
async def test_sealing_an_empty_range_is_a_conflict(api):
    owner = api.signup()
    commander = api.invite_and_accept(
        owner["tokens"]["access_token"], email="ic@acme.io", roles=["incident_commander"]
    )
    response = api.client.post(
        "/events/seals",
        json={"incident_id": "inc-nothing"},
        headers=api.auth_header(commander["tokens"]["access_token"]),
    )
    assert response.status_code == 409


@pytest.mark.req("REQ-023")
async def test_unknown_event_is_a_clean_404(api):
    owner = api.signup()
    response = api.client.get(
        "/events/00000000-0000-0000-0000-000000000000",
        headers=api.auth_header(owner["tokens"]["access_token"]),
    )
    assert response.status_code == 404
