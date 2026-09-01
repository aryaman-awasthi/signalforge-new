"""A drill is not a production capability (REQ-070, REQ-018).

The drill engine can make an application unhealthy, make a path time out, and
inject a declared fault. If any of that were reachable from an ordinary
authenticated session, SignalForge would ship a route whose purpose is to break
a customer's estate - which is a far better outrage than any of the incidents it
manages.

So the boundary is structural rather than a permission check:

1. the API application does not import the drill engine at all, so there is no
   route that could reach it whatever a caller sends;
2. no published route mentions drills or chaos, and the OpenAPI document is the
   proof, since that document is generated from the live app;
3. the chaos adapter offers exactly one credential scope, `drill`, so a
   connector bound with a read-only or remediation credential cannot inject;
4. a scenario whose scope is not `drill:<id>` is refused at load, before a
   single step runs.
"""

from __future__ import annotations

import importlib
import pkgutil

import pytest

from signalforge.domain.drills.errors import ScenarioError
from signalforge.domain.drills.scenario import load_scenario
from signalforge.tests.adapters.conftest import connect  # noqa: F401
from signalforge.tests.onboarding.conftest import register_app

DRILL_PACKAGES = ("signalforge.application.drills", "signalforge.domain.drills")


@pytest.mark.req("REQ-070", "REQ-018")
def test_the_api_never_imports_the_drill_engine():
    """Import the whole api package and read what it pulled in.

    A grep would miss an indirect import; this walks every api module, imports
    it, and then asks the interpreter what is loaded - so an engine reachable
    through three hops is still caught.
    """
    import sys

    import signalforge.api as api_package

    for module in pkgutil.walk_packages(api_package.__path__, "signalforge.api."):
        importlib.import_module(module.name)

    leaked = sorted(
        name
        for name in sys.modules
        if any(name.startswith(package) for package in DRILL_PACKAGES)
        and sys.modules[name] is not None
        and _reached_from_api(name)
    )
    assert not leaked, f"the API application reaches the drill engine: {leaked}"


def _reached_from_api(name: str) -> bool:
    """True when an api module names the drill package in its own source."""
    import pathlib

    import signalforge.api as api_package

    root = pathlib.Path(api_package.__path__[0])
    for path in root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if "drills" in text and "import" in text:
            for line in text.splitlines():
                if line.strip().startswith(("import ", "from ")) and "drills" in line:
                    return True
    return False


@pytest.mark.req("REQ-070", "REQ-018")
def test_no_published_route_offers_chaos_or_drills(api):
    """The published surface, read from the live application."""
    document = api.client.get("/openapi.json")
    assert document.status_code == 200, document.text
    paths = list(document.json()["paths"])
    offending = [
        path
        for path in paths
        if "drill" in path.lower() or "chaos" in path.lower() or "inject" in path.lower()
    ]
    assert not offending, f"drill-only controls are published as routes: {offending}"


@pytest.mark.req("REQ-070", "REQ-033")
def test_the_chaos_adapter_can_only_be_bound_with_a_drill_credential(api, observe):
    """A chaos connector asked for on a read-only credential is refused.

    The manifest offers `drill` and nothing else, and the connector service
    validates the requested scope against the manifest - so the refusal comes
    from the contract rather than from a rule somebody could relax.
    """
    owner = api.signup()
    token = owner["tokens"]["access_token"]
    register_app(api, token, app_id="checkout", name="Checkout")

    manifest = api.container.adapter_registry.get("http-observe-chaos")
    assert [scope.value for scope in manifest.credential_scopes] == ["drill"]

    for refused_scope in ("read_only", "remediation"):
        connect(
            api,
            token,
            "http-observe-chaos",
            app_id="checkout",
            scope=refused_scope,
            expect=422,
        )

    connect(api, token, "http-observe-chaos", app_id="checkout", scope="drill")


@pytest.mark.req("REQ-070")
def test_a_scenario_outside_a_drill_scope_is_refused_at_load():
    document = {
        "drill_id": "sneaky",
        "scope": "production",
        "steps": [
            {
                "id": "one",
                "kind": "inject",
                "label": "break production",
                "inject": {"scenario_id": "fabric-link-loss"},
            }
        ],
    }
    with pytest.raises(ScenarioError) as raised:
        load_scenario(document)
    assert "drill:" in str(raised.value)
