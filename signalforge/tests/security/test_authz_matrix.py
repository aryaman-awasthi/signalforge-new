"""S1-T05: API-level authorization matrix (REQ-012).

Every role exercises representative permission-gated endpoints; expectations
come from the frozen roles matrix. Also proves every product route hangs off
the RBAC/principal dependency and that each denial writes an audit event.
"""

import pytest

from signalforge.api import deps
from signalforge.api.main import app
from signalforge.api.routers.health import ComponentOut, HealthResponse

# Endpoints under test -> (method, path template, permission needed).
GATED = {
    "create_invite": ("POST", "/invites", "manage_org"),
    "rename_org": ("PATCH", "/orgs/current", "manage_org"),
    "set_roles": ("PUT", "/users/{member_id}/roles", "manage_org"),
    "create_rotation": ("POST", "/oncall/rotations", "manage_org"),
    "create_policy": ("POST", "/escalation/policies", "manage_org"),
    "test_fire": ("POST", "/escalation/policies/{policy_id}/test", "run_drills"),
}

# Which of the gated endpoints each role may use (from the contracts matrix:
# manage_org is org_owner-only; run_drills is org_owner + incident_commander).
EXPECTED_ALLOWED = {
    "org_owner": set(GATED),
    "incident_commander": {"test_fire"},
    "tech_lead": set(),
    "sme": set(),
    "comms_lead": set(),
    "executive": set(),
    "auditor": set(),
}

BODIES = {
    "create_invite": lambda ctx: {"email": f"x-{ctx['n']}@acme.io", "roles": ["sme"]},
    "rename_org": lambda ctx: {"name": "Renamed Org"},
    "set_roles": lambda ctx: {"bindings": [{"role": "sme"}]},
    "create_rotation": lambda ctx: {"name": f"r-{ctx['n']}", "user_ids": [ctx["member_id"]]},
    "create_policy": lambda ctx: {
        "name": f"p-{ctx['n']}",
        "steps": [{"target_type": "user", "target_id": ctx["member_id"], "timeout_s": 60}],
    },
    "test_fire": lambda ctx: None,
}


@pytest.mark.req("REQ-012")
def test_authz_matrix_across_roles_and_endpoints(api):
    owner = api.signup()
    owner_token = owner["tokens"]["access_token"]
    tenant_id = owner["org"]["id"]

    tokens = {"org_owner": owner_token}
    member_ids = {}
    for role in EXPECTED_ALLOWED:
        if role == "org_owner":
            continue
        joined = api.invite_and_accept(
            owner_token, email=f"{role}@acme.io", roles=[role], display_name=role
        )
        tokens[role] = joined["tokens"]["access_token"]
        member_ids[role] = joined["user"]["id"]

    rotation = api.client.post(
        "/oncall/rotations",
        json={"name": "primary", "user_ids": [member_ids["incident_commander"]]},
        headers=api.auth_header(owner_token),
    ).json()
    policy = api.client.post(
        "/escalation/policies",
        json={"name": "sev1", "steps": [
            {"target_type": "rotation", "target_id": rotation["id"], "timeout_s": 300}
        ]},
        headers=api.auth_header(owner_token),
    ).json()

    denials_expected = 0
    failures = []
    n = 0
    for role, token in tokens.items():
        for endpoint, (method, template, permission) in GATED.items():
            n += 1
            ctx = {"n": n, "member_id": member_ids["sme"], "policy_id": policy["id"]}
            path = template.format(member_id=member_ids["sme"], policy_id=policy["id"])
            body = BODIES[endpoint](ctx)
            response = api.client.request(
                method, path,
                json=body,
                headers=api.auth_header(token),
            )
            allowed = endpoint in EXPECTED_ALLOWED[role]
            if allowed and response.status_code == 403:
                failures.append(f"{role} unexpectedly denied {endpoint}")
            if not allowed:
                denials_expected += 1
                if response.status_code != 403:
                    failures.append(
                        f"{role} got {response.status_code} on {endpoint}, wanted 403"
                    )
                else:
                    payload = response.json()
                    if payload.get("detail") != "permission denied":
                        failures.append(f"{role} {endpoint}: unclean 403 body {payload}")
                    if payload.get("permission") != permission:
                        failures.append(
                            f"{role} {endpoint}: 403 names {payload.get('permission')}"
                        )
    assert not failures, "\n".join(failures)

    denied_events = [
        r for r in api.audit_rows(tenant_id) if r["topic"] == "audit.authz.denied"
    ]
    assert len(denied_events) == denials_expected, (
        f"every denial must land in the audit chain: "
        f"{len(denied_events)} events for {denials_expected} denials"
    )


def _depends_on_principal(dependant) -> bool:
    if dependant.call is deps.get_principal:
        return True
    return any(_depends_on_principal(sub) for sub in dependant.dependencies)


def _walk_api_routes(routes):
    """Flattens app.routes across nested/included routers (this FastAPI
    version wraps included routers rather than inlining their routes)."""
    for route in routes:
        if getattr(route, "dependant", None) is not None:
            yield route
        inner = getattr(route, "original_router", None) or route
        nested = getattr(inner, "routes", None)
        if nested:
            yield from _walk_api_routes(nested)


PUBLIC_ROUTES = {
    "/", "/docs", "/openapi.json", "/docs/oauth2-redirect", "/redoc",
    # Bootstrap: these are how a caller gets a session in the first place.
    "/auth/signup", "/auth/login", "/auth/refresh", "/auth/accept-invite",
    # Probes and scrapes have no session by design. Their compensating control
    # is that neither can name a tenant: /health returns component statuses
    # only, and /metrics is rendered by a registry that rejects identifying
    # labels at declaration time. Both are asserted below.
    "/health", "/metrics",
}


@pytest.mark.req("REQ-012", "REQ-025")
def test_unauthenticated_platform_endpoints_cannot_name_a_tenant():
    """The two routes with no principal are only safe because of what they are
    incapable of saying. If that ever changes, this fails before the leak ships."""
    from signalforge.infrastructure.observability.metrics import (
        FORBIDDEN_LABEL_NAMES,
        build_registry,
    )

    for metric in build_registry().declared:
        assert not FORBIDDEN_LABEL_NAMES & set(metric.label_names), metric.name

    health_fields = set(HealthResponse.model_fields)
    assert health_fields == {"status", "service", "version", "time", "components"}
    assert set(ComponentOut.model_fields) == {"component", "status", "detail"}


@pytest.mark.req("REQ-012")
def test_every_product_route_carries_the_rbac_dependency():
    public = PUBLIC_ROUTES
    unguarded = []
    covered = 0
    for route in _walk_api_routes(app.routes):
        path = getattr(route, "path", None)
        if path is None or path in public:
            continue
        covered += 1
        if not _depends_on_principal(route.dependant):
            unguarded.append(path)
    assert not unguarded, f"routes missing the RBAC/principal dependency: {unguarded}"
    assert covered >= 15, "the guard walk must actually cover the API surface"
