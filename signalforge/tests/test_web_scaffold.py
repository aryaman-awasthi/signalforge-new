"""S0-T05: API /health and web scaffold layering (REQ-007)."""

import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from signalforge.api.main import app

ROOT = Path(__file__).resolve().parents[2]
WEB = ROOT / "web"


@pytest.mark.req("REQ-007")
def test_api_health_endpoint(api):
    """Goes through the per-test container: /health became a real readiness
    check in S2, so it needs a database to report on."""
    response = api.client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["service"] == "signalforge-api"
    assert body["version"] and body["time"]


@pytest.mark.req("REQ-007")
def test_api_cors_allows_web_dev_origin(api):
    response = api.client.get("/health", headers={"Origin": "http://localhost:3000"})
    assert response.headers.get("access-control-allow-origin") == "http://localhost:3000"


@pytest.mark.req("REQ-007")
def test_web_scaffold_structure():
    package = json.loads((WEB / "package.json").read_text(encoding="utf-8"))
    assert package["dependencies"]["next"].startswith("14."), "Next.js 14 required"
    for path in [
        "tailwind.config.ts",
        "design-system/tokens.css",
        "app/layout.tsx",
        "app/health/page.tsx",
        "lib/api/client.ts",
        "lib/ws/store.ts",
        "features/health/components/HealthPanel.tsx",
        "features/health/hooks/useApiHealth.ts",
        "features/health/api.ts",
    ]:
        assert (WEB / path).is_file(), f"missing web/{path}"


@pytest.mark.req("REQ-007")
def test_design_tokens_are_the_single_theming_surface():
    tokens = (WEB / "design-system" / "tokens.css").read_text(encoding="utf-8")
    for token in ["--sf-surface", "--sf-ink", "--sf-accent", "--sf-danger", "--sf-font-sans"]:
        assert token in tokens
    tailwind = (WEB / "tailwind.config.ts").read_text(encoding="utf-8")
    assert "--sf-surface" in tailwind, "tailwind palette must resolve to tokens"
    assert not re.search(r"#[0-9a-fA-F]{6}", tailwind), "no hardcoded hex in tailwind config"


@pytest.mark.req("REQ-007")
def test_route_files_are_thin_and_data_goes_through_typed_client():
    health_page = (WEB / "app" / "health" / "page.tsx").read_text(encoding="utf-8")
    assert "features/health" in health_page, "route must render the feature component"
    assert "fetch(" not in health_page, "route files must not fetch"
    feature_api = (WEB / "features" / "health" / "api.ts").read_text(encoding="utf-8")
    assert "lib/api/client" in feature_api, "feature data must use the typed client"
