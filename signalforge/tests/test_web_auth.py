"""S1-T04: web auth + org setup structure (REQ-010, REQ-012, REQ-014).

Structural rules mirroring S0's web tests: thin routes, features own the
logic, server code only in app/api route handlers, tokens never reach
browser JavaScript.
"""

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
WEB = ROOT / "web"


@pytest.mark.req("REQ-010")
def test_auth_pages_and_cookie_proxy_exist():
    for path in [
        "app/login/page.tsx",
        "app/signup/page.tsx",
        "app/setup/page.tsx",
        "app/invite/[token]/page.tsx",
        "app/api/auth/login/route.ts",
        "app/api/auth/signup/route.ts",
        "app/api/auth/logout/route.ts",
        "app/api/auth/accept-invite/route.ts",
        "app/api/proxy/[...path]/route.ts",
        "app/api/_lib/session.ts",
        "middleware.ts",
        "features/auth/api.ts",
        "features/org-setup/api.ts",
    ]:
        assert (WEB / path).is_file(), f"missing web/{path}"


@pytest.mark.req("REQ-010")
def test_route_files_are_thin():
    for page in ["login", "signup", "setup"]:
        source = (WEB / "app" / page / "page.tsx").read_text(encoding="utf-8")
        assert "features/" in source, f"{page} route must render a feature component"
        assert "fetch(" not in source, "route files must not fetch"
    invite = (WEB / "app" / "invite" / "[token]" / "page.tsx").read_text(encoding="utf-8")
    assert "features/auth" in invite and "fetch(" not in invite


@pytest.mark.req("REQ-010")
def test_tokens_live_in_httponly_cookies_never_browser_js():
    session = (WEB / "app" / "api" / "_lib" / "session.ts").read_text(encoding="utf-8")
    assert "httpOnly: true" in session
    assert session.count("httpOnly: true") >= 3, "access, refresh and roles cookies"

    for client_file in (WEB / "features").rglob("*.ts*"):
        source = client_file.read_text(encoding="utf-8")
        assert "localStorage" not in source, f"{client_file} must not store tokens"
        assert "access_token" not in source, (
            f"{client_file}: tokens must stay server-side in the cookie proxy"
        )


@pytest.mark.req("REQ-010")
def test_proxy_attaches_bearer_and_refreshes_once():
    proxy = (WEB / "app" / "api" / "proxy" / "[...path]" / "route.ts").read_text(
        encoding="utf-8"
    )
    assert "Authorization" in proxy and "Bearer" in proxy
    assert "/auth/refresh" in proxy, "proxy must attempt one refresh on 401"
    for method in ["GET", "POST", "PUT", "PATCH", "DELETE"]:
        assert f"export async function {method}" in proxy


@pytest.mark.req("REQ-012")
def test_middleware_guards_setup_for_the_org_role_group():
    middleware = (WEB / "middleware.ts").read_text(encoding="utf-8")
    assert "/setup" in middleware
    assert "sf_access" in middleware, "unauthenticated users are redirected"
    assert "sf_roles" in middleware and "org_owner" in middleware, (
        "the org role group guards /setup"
    )
    assert "/login" in middleware


@pytest.mark.req("REQ-014")
def test_org_setup_screens_exist_and_use_the_typed_client():
    for component in [
        "components/OrgSetupScreen.tsx",
        "components/InvitesPanel.tsx",
        "components/RoleMatrixEditor.tsx",
        "components/OnCallPanel.tsx",
        "components/EscalationPanel.tsx",
        "hooks/useOrgSetup.ts",
    ]:
        assert (WEB / "features" / "org-setup" / component).is_file()

    api_source = (WEB / "features" / "org-setup" / "api.ts").read_text(encoding="utf-8")
    assert "lib/api/client" in api_source, "feature data must use the typed client"
    for component_file in (WEB / "features" / "org-setup" / "components").glob("*.tsx"):
        assert "fetch(" not in component_file.read_text(encoding="utf-8"), (
            f"{component_file.name}: components must go through the feature api"
        )


@pytest.mark.req("REQ-014")
def test_oncall_editor_exposes_order_and_escalation_timers():
    oncall = (WEB / "features" / "org-setup" / "components" / "OnCallPanel.tsx").read_text(
        encoding="utf-8"
    )
    assert "order" in oncall and "handoff" in oncall.lower()
    escalation = (
        WEB / "features" / "org-setup" / "components" / "EscalationPanel.tsx"
    ).read_text(encoding="utf-8")
    assert "timeout_s" in escalation and "page_offsets_s" in escalation
