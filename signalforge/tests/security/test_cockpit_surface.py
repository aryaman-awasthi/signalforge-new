"""Security properties of the cockpit surface added by S3-T05 (artifact 18).

The wizard is the first screen in this product that *handles a credential*: it
takes connector credentials on the way in, and it is the one place an ingest
token and an HMAC secret are ever readable. Three ways that can go wrong, and a
test for each:

- the browser could keep credential material somewhere it outlives the moment
  (storage, a URL, a query cache) - so nothing in the cockpit may name a
  storage API, and the listing types must have no token or secret field at all;
- a health or listing response could carry credential material back out, which
  would make masking on the screen cosmetic - so it is asserted against the
  live API with a real credential in play;
- a screen could be reachable by somebody the API would refuse. The route guard
  is UX only and always was, so the assertion that matters is the API's: every
  route these screens call is behind `manage_apps`, read out of the route's own
  dependency rather than from a list kept here.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from signalforge.api.main import app as api_app
from signalforge.contracts.roles import Permission
from signalforge.tests.adapters.conftest import connect, observe  # noqa: F401
from signalforge.tests.ingest.conftest import Sender, alert, batch, issue_key
from signalforge.tests.onboarding.conftest import APP_ID, owner, register_app

ROOT = Path(__file__).resolve().parents[3]
WEB = ROOT / "web"

STORAGE_APIS = ("localStorage", "sessionStorage", "indexedDB", "document.cookie")

COCKPIT_FEATURES = ("apps", "integrations", "onboarding")

CONNECTOR_CREDENTIAL = "an-observe-token-nobody-should-see"


def cockpit_sources() -> list[Path]:
    files: list[Path] = []
    for feature in COCKPIT_FEATURES:
        files.extend((WEB / "features" / feature).rglob("*.ts"))
        files.extend((WEB / "features" / feature).rglob("*.tsx"))
    files.extend((WEB / "app" / "apps").rglob("*.tsx"))
    files.extend((WEB / "app" / "integration-health").rglob("*.tsx"))
    return files


@pytest.mark.req("REQ-015", "REQ-036")
def test_no_cockpit_code_puts_credential_material_anywhere_durable():
    """The token and the secret exist for one render. A cockpit that stashed
    either would turn "shown once" into "shown once, then kept forever"."""
    for source in cockpit_sources():
        text = source.read_text(encoding="utf-8")
        for api in STORAGE_APIS:
            assert api not in text, f"{source.name} must not touch {api}"
        assert "access_token" not in text, f"{source.name} must not handle a token"


@pytest.mark.req("REQ-015", "REQ-032")
def test_the_listing_types_have_no_token_or_secret_field():
    """Masking on the screen is only as good as what the type can hold. The
    listing shape has no `token` and no `secret` because the endpoint has none;
    the credential is a separate type that only ever comes from a 201."""
    api = (WEB / "features" / "integrations" / "api.ts").read_text(encoding="utf-8")
    listing = api.split("export type IngestKey = {")[1].split("};")[0]
    assert "token:" not in listing and "secret:" not in listing
    assert "token_prefix:" in listing

    issued = api.split("export type IssuedIngestKey")[1].split("\n")[0]
    assert "token: string" in issued and "secret: string" in issued

    connector = api.split("export type Connector = {")[1].split("};")[0]
    assert "credential_fields:" in connector, "names only"
    assert "credentials:" not in connector, "a binding never returns its values"


@pytest.mark.req("REQ-015", "REQ-036")
def test_no_screen_this_task_added_can_read_a_credential_back_out(api, observe):
    """Asserted against the live API with a real credential in play, not against
    the cockpit's rendering of it: what the screens are handed is what matters."""
    token, _tenant_id = owner(api)
    register_app(api, token)
    connect(
        api,
        token,
        "http-observe-metrics",
        app_id=APP_ID,
        credentials={"token": CONNECTOR_CREDENTIAL},
    )
    issued = issue_key(api, token, stream="alerts", app_id=APP_ID)
    Sender(api=api, key=issued).post(batch(alert()))

    header = api.auth_header(token)
    for path in (
        "/apps",
        f"/apps/{APP_ID}",
        "/connectors",
        "/connectors/health",
        "/ingest-keys",
        "/adapters",
    ):
        body = api.client.get(path, headers=header).text
        assert CONNECTOR_CREDENTIAL not in body, f"{path} leaked a connector credential"
        assert issued["token"] not in body, f"{path} leaked an ingest token"
        assert issued["secret"] not in body, f"{path} leaked an HMAC secret"

    # The prefix is the identifier the screen shows, and it is not a credential.
    listed = api.client.get("/ingest-keys", headers=header).json()[0]
    assert listed["token_prefix"] and listed["token_prefix"] != issued["token"]


@pytest.mark.req("REQ-012", "REQ-036")
def test_every_route_the_new_screens_call_is_behind_manage_apps():
    """Read out of each route's own RBAC dependency closure, so a screen wired
    to a route somebody forgot to guard fails here rather than in production."""
    wanted = {
        "/apps",
        "/apps/{app_id}",
        "/apps/{app_id}/discover",
        "/apps/{app_id}/connection-tests",
        "/apps/{app_id}/mapping",
        "/apps/{app_id}/actions/import",
        "/apps/{app_id}/topology",
        "/apps/{app_id}/governance",
        "/apps/{app_id}/baseline",
        "/apps/{app_id}/test",
        "/apps/{app_id}/readiness",
        "/adapters",
        "/adapters/capability-matrix",
        "/connectors",
        "/connectors/health",
        "/ingest-keys",
        "/ingest-keys/{key_id}/rotate",
    }
    from signalforge.tests.security.test_authz_matrix import _walk_api_routes
    from signalforge.tests.security.test_onboarding_boundary import _dependency_names

    seen: set[str] = set()
    unguarded: list[str] = []
    for route in _walk_api_routes(api_app.routes):
        path = getattr(route, "path", "")
        if path not in wanted:
            continue
        seen.add(path)
        if Permission.MANAGE_APPS.value not in _dependency_names(route.dependant):
            unguarded.append(f"{route.methods} {path}")

    assert not unguarded, f"routes the new screens call, unguarded: {unguarded}"
    assert seen == wanted, f"routes the screens call are missing: {wanted - seen}"


@pytest.mark.req("REQ-013", "REQ-036")
def test_the_health_screen_reads_only_the_asking_tenants_integrations(api):
    """The S23 join is a second place a tenant predicate has to hold, and the
    screen has no app_id of its own to scope by - it asks for everything."""
    first_token, _first = owner(api)
    register_app(api, first_token)
    connect(api, first_token, "itsm-lite", app_id=APP_ID)
    issue_key(api, first_token, stream="alerts", app_id=APP_ID)

    second = api.signup(org="Rival", email="owner@rival.io")
    second_token = second["tokens"]["access_token"]

    assert api.client.get(
        "/connectors/health", headers=api.auth_header(second_token)
    ).json() == []
    assert api.client.get(
        "/ingest-keys", headers=api.auth_header(second_token)
    ).json() == []
