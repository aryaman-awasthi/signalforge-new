"""S3-T07: what the published API document is allowed to say (artifact 18).

The exported document is now the thing an integrator - and an attacker - reads
first. That makes it two kinds of security artifact at once:

- a **census**. Every operation states the credential it wants, read from its own
  guard, so the set of routes reachable with no credential is a list rather than
  an assumption. It is pinned here at six, and the five ingest paths are pinned
  as the only routes a *machine* credential can reach. A new unauthenticated
  route, or a machine credential that suddenly reaches a human's endpoint, fails
  the build rather than a review;
- a **disclosure surface**. `/openapi.json` is served without a session, so
  anything in it is public: no example may carry credential material, no
  response schema may carry a field that returns one, and no host may be baked
  into it.

The permissions the document publishes are checked against the permissions the
routes enforce, so the document cannot describe a stricter API than it serves.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from signalforge.api.main import app as api_app
from signalforge.contracts.ingest import INGEST_PATHS

ROOT = Path(__file__).resolve().parents[3]
EXPORT = ROOT / "docs" / "11-openapi.json"

# The complete list of product routes that answer without a credential. Two
# probes (which can name no tenant: /health returns component statuses only and
# /metrics is rendered by a registry that rejects identifying labels) and the
# four ways to obtain a session in the first place.
NO_CREDENTIAL = {
    ("get", "/health"),
    ("get", "/metrics"),
    ("post", "/auth/signup"),
    ("post", "/auth/login"),
    ("post", "/auth/refresh"),
    ("post", "/auth/accept-invite"),
}

# A session with no permission may read what every member of a tenant may read.
# The writes it may do are the ones a permission could not correctly guard, and
# there are five.
#
# Ending your own session is the original: there is nobody else's session in the
# request, so there is nothing for a permission to protect.
#
# The other four are the war room's (S4-T03), and they exist because of a fact in
# the frozen roles matrix rather than a convenience: `ROLE_PERMISSIONS[Role.SME]`
# is empty, so the person most likely to be paged holds nothing. Acknowledging a
# page, joining the room, speaking on the bridge and ticking your own task *are*
# that person, and a permission on any of them would lock the war room out of the
# people it exists to reach - the same reasoning `/ws` records as DR-031.
#
# They are not unguarded. Each is **self-scoped**: the actor is the session and
# can never be a parameter, and the entity refuses the act when it names somebody
# else. That is stronger than a permission would be, not weaker -
# `declare_incident` would let any holder acknowledge anybody's page, whereas
# `Page.acknowledged` refuses everyone but the addressee. Every one of the four is
# asserted in both directions in tests/security/test_warroom_boundary.py, which
# also pins this same list, so an entry added here without a self-scoping test
# fails that file instead of passing quietly here.
#
# The sixth is `POST /auth/ws-ticket` (S4 carry-over): it mints a socket ticket
# for the caller's *own* session and takes no parameter at all, so there is
# nothing in the request for a permission to protect - and a permission would
# disconnect the SME's pager, which is the same reason the four below carry none.
SESSION_ONLY_WRITES = {
    ("post", "/auth/logout"),
    ("post", "/auth/ws-ticket"),
    ("post", "/incidents/{incident_id}/pages/{page_id}/ack"),
    ("post", "/incidents/{incident_id}/war-room/join"),
    ("post", "/incidents/{incident_id}/bridge"),
    ("post", "/incidents/{incident_id}/tasks/{task_id}/done"),
}

WRITE_METHODS = {"post", "put", "patch", "delete"}

CREDENTIAL_PROPERTIES = {
    "token", "secret", "password", "access_token", "refresh_token", "credentials",
    # A socket ticket is credential material even though it is worth one
    # handshake for thirty seconds. Named `ws_ticket` rather than `ticket` so
    # the census can tell it from an incident's ITSM reference (S4 carry-over).
    "ws_ticket",
}

# The four response shapes that carry credential material, each of them a
# "shown once" body: a new session's tokens, a socket ticket, an invite, and an
# ingest key.
SHOWN_ONCE = {"TokenPairOut", "WsTicketOut", "InviteCreatedOut", "IssuedKeyOut"}


def published() -> dict:
    return json.loads(EXPORT.read_text(encoding="utf-8"))


def operations(schema: dict):
    for path, methods in schema["paths"].items():
        for method, operation in methods.items():
            if method in {"get", "post", "put", "patch", "delete"}:
                yield method, path, operation


@pytest.mark.req("REQ-012")
def test_the_routes_that_need_no_credential_are_a_closed_list():
    """The document is the census. If this list grows, somebody shipped an
    unauthenticated endpoint and the build says so."""
    schema = published()
    open_routes = {
        (method, path)
        for method, path, operation in operations(schema)
        if operation.get("x-signalforge-auth") == "none"
    }
    assert open_routes == NO_CREDENTIAL, (
        f"unexpected routes reachable with no credential: "
        f"{sorted(open_routes - NO_CREDENTIAL)}; "
        f"missing: {sorted(NO_CREDENTIAL - open_routes)}"
    )
    for method, path in open_routes:
        assert schema["paths"][path][method]["security"] == [], (
            f"{method} {path} needs no credential and must say so in `security`"
        )


@pytest.mark.req("REQ-012", "REQ-032")
def test_only_the_five_frozen_ingest_paths_take_a_machine_credential():
    """An ingest key is scoped to one tenant, one app and one stream. It must not
    be presentable anywhere a human session is expected."""
    machine = {
        (method, path)
        for method, path, operation in operations(published())
        if operation.get("x-signalforge-auth") == "ingest-key"
    }
    assert machine == {("post", path) for path in INGEST_PATHS.values()}


@pytest.mark.req("REQ-012")
def test_every_route_that_writes_names_the_permission_it_enforces():
    """A write behind a session but behind no permission is a route every member
    of every tenant may perform. There is exactly one, and it ends the caller's
    own session."""
    unguarded = []
    for method, path, operation in operations(published()):
        if method not in WRITE_METHODS:
            continue
        if operation.get("x-signalforge-auth") != "session":
            continue
        if (method, path) in SESSION_ONLY_WRITES:
            continue
        if not operation.get("x-signalforge-permission"):
            unguarded.append(f"{method.upper()} {path}")
    assert not unguarded, f"writes with a session but no permission: {unguarded}"


@pytest.mark.req("REQ-012")
def test_the_document_cannot_publish_a_permission_the_route_does_not_enforce():
    """Read back the other way: every published permission is one the route's own
    dependency closure actually holds, so the document cannot describe an API
    stricter than the one being served."""
    from signalforge.tests.security.test_authz_matrix import _walk_api_routes
    from signalforge.tests.security.test_onboarding_boundary import _dependency_names

    enforced: dict[tuple[str, str], set[str]] = {}
    for route in _walk_api_routes(api_app.routes):
        for method in getattr(route, "methods", set()) or set():
            enforced[(method.lower(), route.path)] = _dependency_names(route.dependant)

    lies = []
    for method, path, operation in operations(published()):
        claimed = operation.get("x-signalforge-permission")
        if claimed and claimed not in enforced.get((method, path), set()):
            lies.append(f"{method.upper()} {path} claims {claimed}")
    assert not lies, f"the document claims permissions the routes do not enforce: {lies}"


@pytest.mark.req("REQ-015", "REQ-032")
def test_only_the_shown_once_bodies_can_carry_credential_material():
    """Masking on a screen is cosmetic if the published contract says a listing
    returns a token. Asserted against the schemas the document publishes."""
    schemas = published()["components"]["schemas"]
    carriers = {
        name
        for name, schema in schemas.items()
        if CREDENTIAL_PROPERTIES & set(schema.get("properties") or {})
    }
    responses = {name for name in carriers if name.endswith("Out")}
    assert responses == SHOWN_ONCE, (
        f"response shapes carrying credential material: {sorted(responses)}"
    )
    # The listing shape and the connector shapes are the ones that would leak in
    # bulk, so they are named rather than left to the set comparison above.
    for name in ("IngestKeyOut", "ConnectorOut", "ConnectorHealthOut"):
        assert not CREDENTIAL_PROPERTIES & set(schemas[name].get("properties") or {}), (
            f"{name} must not be able to hold credential material"
        )


@pytest.mark.req("REQ-015")
def test_the_published_document_carries_no_credential_values_and_no_host():
    """/openapi.json is served without a session, so everything in it is public.
    An `example="sfi_..."` added in good faith would publish a live-looking
    credential; a `servers` block would publish where this install runs."""
    schema = published()
    assert "servers" not in schema, "the export must not name a host"
    for name, model in schema["components"]["schemas"].items():
        for field, definition in (model.get("properties") or {}).items():
            if field not in CREDENTIAL_PROPERTIES:
                continue
            for key in ("example", "examples"):
                assert key not in definition, (
                    f"{name}.{field} publishes a sample credential ({key})"
                )
            # An empty default is a schema artifact ("this field may be omitted");
            # a non-empty one is a credential somebody typed into a model.
            assert not definition.get("default"), (
                f"{name}.{field} publishes a non-empty default"
            )
