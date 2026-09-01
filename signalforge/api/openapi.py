"""The published API document (artifact 11, S3-T07).

FastAPI already describes the shape of every request and response. What a stock
export cannot say is the thing an integrator asks first: *what credential does
this route want, and what may that credential do?* That answer exists in the
code - it is the guard hanging off the route - so it is read from there rather
than kept in a list somebody has to remember to update:

- `x-signalforge-auth` - `session` (a human's bearer token), `ingest-key` (a
  sending application's token plus the HMAC over the body) or `none`;
- `x-signalforge-permission` - the permission from the roles matrix that the
  route's RBAC dependency closes over, absent when the route only needs a
  session (`/auth/me`, `/roles/matrix`);
- `x-signalforge-app-scope` - whether a per-application role binding is
  evaluated against `?app_id=` or against the application named in the path.

Because the annotations come from `deps.require` / `deps.require_app` /
`deps.require_ingest` themselves, a route guarded by the wrong permission is
published as guarded by the wrong permission, and an *unguarded* route is
published as `none` - which the security suite then fails on, since the set of
routes needing no credential is a closed list.

The same builder serves `GET /openapi.json` and `scripts/gen_openapi.py`, so
the exported artifact and what the running server hands a client are the same
document rather than two things that agree on the day they were written.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterator, Optional

from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi
from fastapi.routing import APIRoute, APIWebSocketRoute

from signalforge.api import deps
from signalforge.contracts.ingest import HDR_KEY_ID, HDR_SIGNATURE, HDR_TIMESTAMP

AUTH_SESSION = "session"
AUTH_INGEST_KEY = "ingest-key"
AUTH_NONE = "none"

WEBSOCKET_METHOD = "WS"

# Routes that are the documentation itself, not product surface.
DOC_ROUTES = frozenset({"/openapi.json", "/docs", "/docs/oauth2-redirect", "/redoc"})

DESCRIPTION = """
SignalForge is an enterprise incident command centre. Any application is
onboarded through the public contracts in `signalforge/contracts/`; nothing in
this API names a vendor or an application.

**Authentication.** Human callers present a bearer access token from
`/auth/signup`, `/auth/login` or `/auth/refresh` (15 minutes, refreshed by a
rotating refresh token). Sending applications present an *ingest key*: a bearer
token plus an HMAC-SHA256 over `"<unix seconds>.<raw body>"` inside a 300-second
replay window. Every operation states which it wants, and which permission from
the roles matrix it requires, in the `x-signalforge-*` fields.

**Errors** are mapped once, in `signalforge/api/errors.py`: 401 authentication,
403 permission denied (with `permission` and `reason`), 404 not found, 409
conflict, 413 body too large, 422 domain validation *and* every policy-floor
refusal, 429 rate limited with `Retry-After`, 502 an integrated system failed,
504 an integrated system did not answer in time - which means the outcome is
unknown, not failed.

**Prose, decisions and the event catalogue** live in `docs/11-api-spec.md`;
this document is generated from the running application by
`scripts/gen_openapi.py` and is the same document `GET /openapi.json` serves.
""".strip()

TAGS: list[dict[str, str]] = [
    {"name": "auth", "description":
        "Session bootstrap: signup, login, refresh, invite acceptance, logout "
        "and the caller's own identity. The only routes that need no credential "
        "are here, because they are how a caller gets one."},
    {"name": "orgs", "description": "The calling session's own tenant."},
    {"name": "users", "description":
        "Members of the tenant and their role bindings, org-wide or per application."},
    {"name": "invites", "description":
        "Invitations into the tenant. The invite token is in the 201 and nowhere "
        "else; revoking one is immediate."},
    {"name": "roles", "description":
        "The frozen roles and permissions matrix, read-only for everyone."},
    {"name": "oncall", "description": "Rotations that escalation policies page."},
    {"name": "escalation", "description":
        "Escalation policies, their ordered steps and a test fire that pages nobody."},
    {"name": "events", "description":
        "Replay of the event log, the causal chain of one event, and the seals "
        "that make a stored chain provable."},
    {"name": "adapters", "description":
        "The adapter registry read off the manifests on disk, the capability "
        "matrix, and per-application connector bindings with their health."},
    {"name": "onboarding", "description":
        "The seven wizard steps: basics, connect and discover, map, topology, "
        "SLOs and policy, baseline, test - plus the readiness score."},
    {"name": "detection", "description":
        "What the platform currently believes about an application: every mapped "
        "signal against its rolling baseline, the weighted health score per "
        "region, SLO burn rates, corroboration and the severity the application's "
        "own matrix would propose. Detection proposes; only a human declares."},
    {"name": "ingest", "description":
        "Ingest keys, and the five frozen push endpoints a sending application "
        "posts to."},
    {"name": "notifications", "description":
        "The cockpit notification socket. A WebSocket is not expressible in "
        "OpenAPI; its frame protocol is in docs/11-api-spec.md."},
    {"name": "platform", "description":
        "Liveness, the Prometheus scrape, platform status, the dead-letter "
        "queue and agent-run traces."},
]

SECURITY_SCHEMES: dict[str, dict] = {
    "sessionBearer": {
        "type": "http",
        "scheme": "bearer",
        "bearerFormat": "JWT",
        "description":
            "A human session's access token. Short-lived; refresh with "
            "POST /auth/refresh, which rotates the refresh token.",
    },
    "ingestKey": {
        "type": "http",
        "scheme": "bearer",
        "description":
            "An ingest key's bearer token (prefix `sfi_`), scoped to one tenant, "
            "one application and one stream. Not enough on its own: the request "
            "must also be signed.",
    },
    "ingestSignature": {
        "type": "apiKey",
        "in": "header",
        "name": HDR_SIGNATURE,
        "description":
            "Hex HMAC-SHA256 of \"<timestamp>.<raw body>\" under the key's shared "
            "secret, computed over the exact bytes sent.",
    },
    "ingestTimestamp": {
        "type": "apiKey",
        "in": "header",
        "name": HDR_TIMESTAMP,
        "description":
            "Unix seconds at signing time. Outside the replay window the request "
            "is refused with the same bare 401 as a forged signature.",
    },
}

SECURITY_REQUIREMENTS: dict[str, list[dict[str, list]]] = {
    AUTH_SESSION: [{"sessionBearer": []}],
    # One object, three schemes: all three are required together.
    AUTH_INGEST_KEY: [{"ingestKey": [], "ingestSignature": [], "ingestTimestamp": []}],
    AUTH_NONE: [],
}


@dataclass(frozen=True)
class RouteGuard:
    """What a route demands before its handler runs."""

    auth: str
    permission: Optional[str] = None
    app_scope: Optional[str] = None
    ingest_stream: Optional[str] = None


@dataclass(frozen=True)
class RouteEntry:
    """One published route, as the catalogue in docs/11 lists it."""

    method: str
    path: str
    group: str
    guard: RouteGuard
    purpose: str

    @property
    def in_openapi(self) -> bool:
        """A WebSocket has no OpenAPI operation to carry the annotations."""
        return self.method != WEBSOCKET_METHOD


def _flatten(dependant) -> Iterator:
    yield dependant
    for sub in dependant.dependencies:
        yield from _flatten(sub)


def guard_of(dependant) -> RouteGuard:
    """Read the credential and the permission out of the route's own guard."""
    auth = AUTH_NONE
    permission: Optional[str] = None
    app_scope: Optional[str] = None
    stream: Optional[str] = None
    for dep in _flatten(dependant):
        call = dep.call
        if call in (deps.get_principal, deps.get_ws_principal):
            auth = AUTH_SESSION
        if getattr(call, "resolves_ingest_credential", False):
            auth = AUTH_INGEST_KEY
            declared_stream = getattr(call, "ingest_stream", None)
            stream = getattr(declared_stream, "value", declared_stream)
        declared = getattr(call, "required_permission", None)
        if declared is not None:
            permission = declared.value
            app_scope = getattr(call, "app_id_source", None)
    return RouteGuard(
        auth=auth, permission=permission, app_scope=app_scope, ingest_stream=stream
    )


def _walk(routes) -> Iterator:
    """Flatten app.routes across included routers.

    This FastAPI version wraps an included router rather than inlining its
    routes, so a walk that only looked at the top level would publish four
    documentation endpoints and nothing else.
    """
    for route in routes:
        if isinstance(route, (APIRoute, APIWebSocketRoute)):
            yield route
        inner = getattr(route, "original_router", None) or route
        nested = getattr(inner, "routes", None)
        if nested:
            yield from _walk(nested)


_SENTENCE = re.compile(r"(?<=[.?!])\s")


def _first_sentence(text: str, limit: int = 150) -> str:
    collapsed = " ".join(text.split())
    if not collapsed:
        return ""
    sentence = _SENTENCE.split(collapsed, maxsplit=1)[0]
    if len(sentence) > limit:
        sentence = sentence[: limit - 1].rsplit(" ", 1)[0] + "..."
    return sentence


def _purpose(route) -> str:
    """What the route is for: its own docstring first, its name as a fallback."""
    doc = (getattr(route.endpoint, "__doc__", None) or "").strip()
    if doc:
        return _first_sentence(doc)
    summary = getattr(route, "summary", None)
    if summary:
        return summary
    return (route.name or "").replace("_", " ").strip().capitalize()


def route_catalogue(app: FastAPI) -> list[RouteEntry]:
    """Every route this application serves, product routes and the socket.

    Sorted by group then path then method so the generated catalogue is stable
    between runs: a diff in docs/11 means a change in the API, not a change in
    dictionary order.
    """
    entries: list[RouteEntry] = []
    for route in _walk(app.routes):
        path = route.path
        if path in DOC_ROUTES or not getattr(route, "include_in_schema", True):
            continue
        guard = guard_of(route.dependant)
        group = (list(getattr(route, "tags", []) or ["platform"]))[0]
        purpose = _purpose(route)
        if isinstance(route, APIWebSocketRoute):
            entries.append(
                RouteEntry(WEBSOCKET_METHOD, path, str(group), guard, purpose)
            )
            continue
        for method in sorted(route.methods - {"HEAD", "OPTIONS"}):
            entries.append(RouteEntry(method, path, str(group), guard, purpose))
    entries.sort(key=lambda e: (e.group, e.path, e.method))
    return entries


def build_openapi(app: FastAPI, *, use_cache: bool = True) -> dict:
    """The document the server serves and `scripts/gen_openapi.py` exports."""
    if use_cache and app.openapi_schema:
        return app.openapi_schema

    schema = get_openapi(
        title=app.title,
        version=app.version,
        description=DESCRIPTION,
        routes=app.routes,
        tags=TAGS,
    )
    schema.setdefault("components", {})["securitySchemes"] = dict(SECURITY_SCHEMES)

    paths = schema.get("paths", {})
    for entry in route_catalogue(app):
        if not entry.in_openapi:
            continue
        operation = paths.get(entry.path, {}).get(entry.method.lower())
        if operation is None:  # pragma: no cover - a route excluded from the schema
            continue
        operation["security"] = SECURITY_REQUIREMENTS[entry.guard.auth]
        operation["x-signalforge-auth"] = entry.guard.auth
        if entry.guard.permission:
            operation["x-signalforge-permission"] = entry.guard.permission
        if entry.guard.app_scope:
            operation["x-signalforge-app-scope"] = entry.guard.app_scope
        if entry.guard.ingest_stream:
            # Which stream a key must be scoped to. A correctly signed request
            # from a key bound to another stream is a 403, so this is part of
            # the contract rather than a label.
            operation["x-signalforge-ingest-stream"] = entry.guard.ingest_stream
        if entry.guard.auth == AUTH_INGEST_KEY:
            operation["x-signalforge-optional-headers"] = [HDR_KEY_ID]

    if use_cache:
        app.openapi_schema = schema
    return schema
