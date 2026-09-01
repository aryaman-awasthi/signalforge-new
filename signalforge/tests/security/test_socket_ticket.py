"""The WebSocket ticket, as a credential (artifact 18, S4 carry-over, REQ-033).

Written failing-first, because this is the security category CLAUDE.md names
first: a new credential, and one invented specifically so that an existing
control - the session token living in an httpOnly cookie browser JavaScript
cannot read - does not have to be weakened to give the cockpit a socket.

Every property below is what makes it a credential rather than a query
parameter with better manners, and each one is asserted in the direction that
would hurt:

| Property | The failure it prevents |
|---|---|
| short-lived | a ticket scraped from a browser's memory an hour later |
| single-use | a captured handshake replayed |
| typed | a ticket presented to the HTTP API as a bearer token |
| session-bound | a ticket outliving the logout that should have killed it |
| tenant-bound | a ticket resolving a session in another tenant |
| authenticated to obtain | anyone minting one |

The reverse direction is asserted too - an *access token* redeemed as a ticket -
because a credential that is one-way in only one direction is two credentials
with one secret.
"""

from __future__ import annotations

import pytest

from signalforge.domain.identity.errors import AuthenticationError
from signalforge.infrastructure.config import SOCKET_TICKET_TTL_S
from signalforge.infrastructure.security.tokens import (
    JWTAccessTokenService,
    JWTSocketTicketService,
)

pytestmark = pytest.mark.anyio


# --- the primitive ------------------------------------------------------------


@pytest.mark.req("REQ-010", "REQ-033")
def test_a_ticket_is_short_lived_and_says_what_it_is(clock):
    tickets = JWTSocketTicketService(secret="s" * 32, clock=clock)
    ticket = tickets.issue(user_id="u1", tenant_id="t1", session_id="s1")
    claims = tickets.redeem(ticket)
    assert claims["sub"] == "u1"
    assert claims["tid"] == "t1"
    assert claims["sid"] == "s1"
    assert claims["typ"] == "ws_ticket"
    assert claims["exp"] - claims["iat"] == SOCKET_TICKET_TTL_S <= 60, (
        "a handshake credential measured in minutes is a second session token"
    )


@pytest.mark.req("REQ-010", "REQ-033")
def test_a_ticket_is_spent_by_being_redeemed(clock):
    """Single use. A handshake captured off the wire and replayed a second later
    is refused, without waiting for the ticket to expire."""
    tickets = JWTSocketTicketService(secret="s" * 32, clock=clock)
    ticket = tickets.issue(user_id="u1", tenant_id="t1", session_id="s1")
    tickets.redeem(ticket)
    with pytest.raises(AuthenticationError):
        tickets.redeem(ticket)


@pytest.mark.req("REQ-010", "REQ-033")
def test_a_ticket_expires_on_the_wall_clock(clock):
    tickets = JWTSocketTicketService(secret="s" * 32, clock=clock)
    ticket = tickets.issue(user_id="u1", tenant_id="t1", session_id="s1")
    clock.advance(seconds=SOCKET_TICKET_TTL_S + 1)
    with pytest.raises(AuthenticationError):
        tickets.redeem(ticket)


@pytest.mark.req("REQ-010", "REQ-033")
def test_the_two_credentials_are_not_interchangeable_in_either_direction(clock):
    """The same secret signs both, and the type is what separates them. Without
    this, a ticket in a log line is a session."""
    secret = "s" * 32
    access = JWTAccessTokenService(secret=secret, clock=clock)
    tickets = JWTSocketTicketService(secret=secret, clock=clock)

    ticket = tickets.issue(user_id="u1", tenant_id="t1", session_id="s1")
    with pytest.raises(AuthenticationError):
        access.verify(ticket)

    token = access.issue(user_id="u1", tenant_id="t1", session_id="s1")
    with pytest.raises(AuthenticationError):
        tickets.redeem(token)


@pytest.mark.req("REQ-010", "REQ-033")
def test_a_ticket_signed_by_another_install_is_refused(clock):
    tickets = JWTSocketTicketService(secret="s" * 32, clock=clock)
    forger = JWTSocketTicketService(secret="x" * 32, clock=clock)
    forged = forger.issue(user_id="u1", tenant_id="t1", session_id="s1")
    with pytest.raises(AuthenticationError):
        tickets.redeem(forged)


# --- the route and the handshake ---------------------------------------------


def _ticket(api, token: str, *, expect: int = 201) -> dict:
    response = api.client.post(
        "/auth/ws-ticket", json={}, headers=api.auth_header(token)
    )
    assert response.status_code == expect, response.text
    return response.json()


@pytest.mark.req("REQ-012", "REQ-033")
async def test_a_ticket_cannot_be_obtained_without_a_session(api):
    api.signup()
    assert api.client.post("/auth/ws-ticket", json={}).status_code == 401
    assert (
        api.client.post(
            "/auth/ws-ticket", json={}, headers={"Authorization": "Bearer not-a-token"}
        ).status_code
        == 401
    )


@pytest.mark.req("REQ-033")
async def test_a_valid_ticket_opens_the_socket_as_the_session_that_asked(api):
    signed = api.signup()
    token = signed["tokens"]["access_token"]
    issued = _ticket(api, token)
    assert issued["subprotocol"] == "ticket"
    assert issued["url_path"] == "/ws"
    assert 0 < issued["expires_in"] <= SOCKET_TICKET_TTL_S

    with api.client.websocket_connect(
        "/ws", subprotocols=["ticket", issued["ws_ticket"]]
    ) as socket:
        ready = socket.receive_json()
        assert ready["type"] == "ready"
        assert ready["user_id"] == signed["user"]["id"]


@pytest.mark.req("REQ-033")
async def test_a_ticket_is_refused_the_second_time_it_is_presented(api):
    """Replay. The first handshake spends it; the second is a stranger."""
    token = api.signup()["tokens"]["access_token"]
    issued = _ticket(api, token)
    with api.client.websocket_connect(
        "/ws", subprotocols=["ticket", issued["ws_ticket"]]
    ) as socket:
        assert socket.receive_json()["type"] == "ready"
    with pytest.raises(Exception):
        with api.client.websocket_connect(
            "/ws", subprotocols=["ticket", issued["ws_ticket"]]
        ):
            pass


@pytest.mark.req("REQ-033")
async def test_an_expired_ticket_is_refused(api):
    token = api.signup()["tokens"]["access_token"]
    issued = _ticket(api, token)
    api.clock.advance(seconds=SOCKET_TICKET_TTL_S + 1)
    with pytest.raises(Exception):
        with api.client.websocket_connect(
            "/ws", subprotocols=["ticket", issued["ws_ticket"]]
        ):
            pass


@pytest.mark.req("REQ-012", "REQ-033")
async def test_a_ticket_cannot_be_spent_on_the_http_api(api):
    """The whole reason it is typed. A ticket is worth one handshake and nothing
    on any route that reads a session."""
    token = api.signup()["tokens"]["access_token"]
    issued = _ticket(api, token)
    for path in ("/auth/me", "/apps", "/incidents"):
        response = api.client.get(
            path, headers={"Authorization": f"Bearer {issued['ws_ticket']}"}
        )
        assert response.status_code == 401, f"{path} accepted a socket ticket"


@pytest.mark.req("REQ-013", "REQ-033")
async def test_a_ticket_names_its_own_tenant_and_cannot_be_pointed_at_another(api):
    """Forged the only way an attacker with a valid ticket could try it: the
    claims are rewritten to another live tenant's ids and re-signed with a
    guessed secret. Both the signature and the pairing are checked, so the
    refusal does not depend on either one alone."""
    import jwt

    first = api.signup(org="Acme", email="a@acme.io")
    second = api.signup(org="Globex", email="b@globex.io")
    issued = _ticket(api, first["tokens"]["access_token"])

    claims = jwt.decode(issued["ws_ticket"], options={"verify_signature": False})
    claims["tid"] = second["org"]["id"]
    claims["sub"] = second["user"]["id"]
    forged = jwt.encode(claims, "a-guessed-secret-that-is-long-enough-000", algorithm="HS256")

    with pytest.raises(Exception):
        with api.client.websocket_connect("/ws", subprotocols=["ticket", forged]):
            pass

    # And the honest ticket resolves to its *own* tenant's user, never the other.
    with api.client.websocket_connect(
        "/ws", subprotocols=["ticket", issued["ws_ticket"]]
    ) as socket:
        assert socket.receive_json()["user_id"] == first["user"]["id"]


@pytest.mark.req("REQ-010", "REQ-033")
async def test_a_ticket_dies_with_the_session_it_was_cut_from(api):
    """A ticket is not a credential of its own: it is a pointer at a session, and
    a session that has been logged out is gone for the socket too."""
    signed = api.signup()
    token = signed["tokens"]["access_token"]
    issued = _ticket(api, token)
    assert api.client.post("/auth/logout", headers=api.auth_header(token)).status_code == 204
    with pytest.raises(Exception):
        with api.client.websocket_connect(
            "/ws", subprotocols=["ticket", issued["ws_ticket"]]
        ):
            pass


@pytest.mark.req("REQ-033")
async def test_the_handshake_still_accepts_a_bearer_token_for_server_side_callers(api):
    """The ticket is additive. Nothing that worked in S3 stopped working."""
    token = api.signup()["tokens"]["access_token"]
    with api.client.websocket_connect(
        "/ws", headers=api.auth_header(token)
    ) as socket:
        assert socket.receive_json()["type"] == "ready"


@pytest.mark.req("REQ-015", "REQ-033")
async def test_no_ticket_material_is_written_to_the_audit_chain_or_the_scrape(api):
    signed = api.signup()
    issued = _ticket(api, signed["tokens"]["access_token"])
    import json

    audit = json.dumps(api.audit_rows(signed["org"]["id"]))
    scrape = api.client.get("/metrics").text
    events = api.client.get(
        "/events?limit=200", headers=api.auth_header(signed["tokens"]["access_token"])
    ).text
    for surface in (audit, scrape, events):
        assert issued["ws_ticket"] not in surface
