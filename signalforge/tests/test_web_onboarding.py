"""S3-T05: the onboarding wizard, apps overview and integration health screens.

REQ-036 (wizard + integration health), REQ-030 (adapter registry section),
REQ-032 (ingest section), REQ-033 (simulated labelling).

Structural rules in the same spirit as the S0-S2 web tests: thin routes, logic
in features, everything through the typed cookie proxy, and - the part that does
the real work here - the cockpit's types pinned against the API's own response
models, so a contract that gains a field fails a test rather than quietly going
unrendered.
"""

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
WEB = ROOT / "web"

FEATURES = ("apps", "integrations", "onboarding")


def read(*parts: str) -> str:
    return (WEB.joinpath(*parts)).read_text(encoding="utf-8")


@pytest.mark.req("REQ-036")
def test_the_wizard_apps_and_integration_screens_exist():
    for path in [
        "app/apps/page.tsx",
        "app/apps/[appId]/onboard/page.tsx",
        "app/apps/[appId]/onboard/[step]/page.tsx",
        "app/integration-health/page.tsx",
        "features/apps/api.ts",
        "features/apps/hooks/useApps.ts",
        "features/apps/components/AppsOverview.tsx",
        "features/apps/components/RegisterAppForm.tsx",
        "features/apps/components/ReadinessBar.tsx",
        "features/onboarding/steps.ts",
        "features/onboarding/hooks/useOnboarding.ts",
        "features/onboarding/components/OnboardWizard.tsx",
        "features/onboarding/components/WizardStepper.tsx",
        "features/onboarding/components/ConfigFields.tsx",
        "features/onboarding/components/PullConnectorCard.tsx",
        "features/onboarding/components/PushConnectorCard.tsx",
        "features/onboarding/components/SimulatedConnectorCard.tsx",
        "features/onboarding/components/ActionsPanel.tsx",
        "features/integrations/api.ts",
        "features/integrations/hooks/useIntegrationHealth.ts",
        "features/integrations/hooks/useRegistry.ts",
        "features/integrations/components/IntegrationHealthScreen.tsx",
        "features/integrations/components/AdapterRegistryPanel.tsx",
        "features/integrations/components/IngestKeysPanel.tsx",
        "features/integrations/components/IngestCredentialOnce.tsx",
        "features/integrations/components/CopyField.tsx",
        "features/integrations/components/SimulatedBadge.tsx",
    ]:
        assert (WEB / path).is_file(), f"missing web/{path}"


@pytest.mark.req("REQ-036")
def test_route_files_stay_thin():
    for route in [
        "app/apps/page.tsx",
        "app/apps/[appId]/onboard/[step]/page.tsx",
        "app/integration-health/page.tsx",
    ]:
        source = read(*route.split("/"))
        assert "features/" in source, f"{route} must render a feature component"
        assert "fetch(" not in source, f"{route} must not fetch"
        assert "useState" not in source, f"{route} must hold no state"


@pytest.mark.req("REQ-036", "REQ-030", "REQ-032")
def test_every_new_feature_goes_through_the_typed_cookie_proxy():
    for feature in FEATURES:
        api = WEB / "features" / feature / "api.ts"
        if not api.is_file():
            continue
        text = api.read_text(encoding="utf-8")
        assert "proxyApi" in text, f"{feature} must use the cookie proxy"
        assert "fetch(" not in text, f"{feature} must not call fetch directly"
        assert "API_BASE_URL" not in text, (
            f"{feature} must not talk to the API origin from the browser"
        )


@pytest.mark.req("REQ-036")
def test_the_seven_steps_are_the_domains_own_and_are_nested_routes():
    """The stepper reads per-step progress off `readiness.steps`, which is keyed
    by the domain's WizardStep. A step key invented by the cockpit would render
    a step that can never show progress."""
    from signalforge.domain.onboarding.profile import WIZARD_STEP_ORDER

    steps = read("features", "onboarding", "steps.ts")
    for step in WIZARD_STEP_ORDER:
        assert f'key: "{step.value}"' in steps, f"wizard step {step.value} missing"
    assert steps.count("key: \"") == len(WIZARD_STEP_ORDER), "exactly seven steps"

    wizard = read("features", "onboarding", "components", "OnboardWizard.tsx")
    for component in (
        "BasicsStep",
        "ConnectStep",
        "MapStep",
        "TopologyStep",
        "GovernanceStep",
        "BaselineStep",
        "TestStep",
    ):
        assert component in wizard, f"{component} not wired into the wizard"
    assert "/onboard/${step + 1}" in wizard, "steps are routes, not tab state"


@pytest.mark.req("REQ-036", "REQ-030")
def test_pull_cards_render_fields_from_the_adapters_own_schema():
    """Nothing about a specific integration is written in the cockpit: a new
    adapter dropped into the adapters tree gets its fields with no web change."""
    fields = read("features", "onboarding", "components", "ConfigFields.tsx")
    assert "schema.properties" in fields and "schema.required" in fields
    assert "coerceConfig" in fields, "an integer field must not be posted as a string"

    card = read("features", "onboarding", "components", "PullConnectorCard.tsx")
    assert "adapter.config_schema" in card
    assert "adapter.auth.credential_fields" in card
    assert 'type="password"' in card, "credential inputs are write-only"
    assert "credential_scopes" in card, "read-only vs remediation is a real choice"
    assert "runConnectionTests" in card, "a bound connector must be testable here"


@pytest.mark.req("REQ-036", "REQ-032")
def test_push_cards_carry_a_copyable_url_a_masked_token_and_rotate():
    card = read("features", "onboarding", "components", "PushConnectorCard.tsx")
    assert "Ingest URL" in card and "CopyField" in card
    assert "token_prefix" in card and "masked" in card
    assert "rotateIngestKey" in card and "revokeIngestKey" in card
    assert "issueIngestKey" in card
    assert ".token}" not in card and ".secret}" not in card, (
        "the card must never render credential material; only the prefix exists"
    )

    once = read("features", "integrations", "components", "IngestCredentialOnce.tsx")
    assert "issued.token" in once and "issued.secret" in once
    assert "time either can be read" in once, "shown once must say so"
    assert "X-SignalForge-Signature" in once, "the sender needs the signing rule"


@pytest.mark.req("REQ-036", "REQ-033")
def test_simulated_integrations_are_labelled_built_in_and_simulated():
    card = read("features", "onboarding", "components", "SimulatedConnectorCard.tsx")
    assert "built-in" in card
    assert "SimulatedBadge" in card
    assert "config_schema" not in card, "a simulated adapter has nothing to configure"

    badge = read("features", "integrations", "components", "SimulatedBadge.tsx")
    assert "simulated" in badge


@pytest.mark.req("REQ-036")
def test_the_apps_overview_shows_readiness_and_leads_into_the_wizard():
    overview = read("features", "apps", "components", "AppsOverview.tsx")
    assert "readiness_score" in overview and "ReadinessBar" in overview
    assert "/onboard/1" in overview, "a row must lead into the wizard"
    assert "/integration-health" in overview

    bar = read("features", "apps", "components", "ReadinessBar.tsx")
    assert "ready" in bar and "not ready" in bar, (
        "the score and the verdict are two facts; a bar alone would imply 90 is ready"
    )


@pytest.mark.req("REQ-036")
def test_the_integration_health_screen_has_every_column_the_task_names():
    screen = read("features", "integrations", "components", "IntegrationHealthScreen.tsx")
    for column in ("Direction", "Last pull", "Last push", "Freshness", "Errors", "Simulated"):
        assert f">{column}<" in screen, f"S23 is missing the {column} column"
    assert "event_freshness_s" in screen, "a push row is measured by what arrived"
    assert "last_ok_at" in screen, "a pull row is measured by when it was read"
    assert "SimulatedBadge" in screen
    assert "key.binding_id" in screen, (
        "an ingest key with no binding is an integration in its own right"
    )


@pytest.mark.req("REQ-036", "REQ-030")
def test_the_health_row_type_tracks_the_api_response_model():
    """If the API's health row gains a field, the cockpit decodes it rather than
    a reshaped copy invented for the screen."""
    from signalforge.api.schemas.adapters import ConnectorHealthOut

    api = read("features", "integrations", "api.ts")
    for field in ConnectorHealthOut.model_fields:
        assert f"{field}:" in api, f"ConnectorHealthOut.{field} missing from the web type"


@pytest.mark.req("REQ-032")
def test_the_ingest_key_type_tracks_the_listing_model():
    from signalforge.api.schemas.ingest import IngestKeyOut

    api = read("features", "integrations", "api.ts")
    for field in IngestKeyOut.model_fields:
        assert f"{field}:" in api, f"IngestKeyOut.{field} missing from the web type"


@pytest.mark.req("REQ-036")
def test_the_readiness_type_tracks_the_api_response_model():
    from signalforge.api.schemas.onboarding import ReadinessOut

    api = read("features", "apps", "api.ts")
    for field in ReadinessOut.model_fields:
        assert f"{field}:" in api, f"ReadinessOut.{field} missing from the web type"


@pytest.mark.req("REQ-030", "REQ-032", "REQ-025")
def test_settings_gained_the_adapter_registry_and_ingest_sections():
    screen = read("features", "platform", "components", "SettingsScreen.tsx")
    assert '"Adapters"' in screen and '"Ingest"' in screen
    assert "AdapterRegistryPanel" in screen and "IngestKeysPanel" in screen
    # S2's panes are still there.
    assert "PlatformHealthPanel" in screen and "DeadLettersPanel" in screen

    registry = read("features", "integrations", "components", "AdapterRegistryPanel.tsx")
    assert "registry.errors" in registry, (
        "a manifest that would not load must be shown, not silently dropped"
    )
    assert "matrix.coverage" in registry and "uncovered" in registry

    ingest = read("features", "integrations", "components", "IngestKeysPanel.tsx")
    assert "rotateIngestKey" in ingest and "revokeIngestKey" in ingest
    assert "bare 401" in ingest, (
        "the operator screen is the only place a refusal reason is readable"
    )


@pytest.mark.req("REQ-012")
def test_the_new_screens_are_guarded_by_the_org_role_group():
    middleware = read("middleware.ts")
    assert '"/onboard"' in middleware and '"/integration-health"' in middleware
    assert "org_owner" in middleware
    assert "UX layer only" in middleware, (
        "the guard must stay documented as UX over the API's real enforcement"
    )


@pytest.mark.req("REQ-036")
def test_failure_states_are_rendered_rather_than_swallowed():
    """Failure design reaches the screens too: each of these is a state the
    platform can genuinely be in, and a wizard that renders none of them turns a
    designed outcome into a mystery."""
    connect = read("features", "onboarding", "components", "steps", "ConnectStep.tsx")
    assert "registryErrors" in connect, "a manifest that failed to load"
    assert "Available, not installed" in connect, "an interface with no runtime"

    topology = read("features", "onboarding", "components", "steps", "TopologyStep.tsx")
    assert "result.unchanged" in topology, "a duplicate upload is one version"

    baseline = read("features", "onboarding", "components", "steps", "BaselineStep.tsx")
    assert "run.missed" in baseline and "Partially complete" in baseline

    test_step = read("features", "onboarding", "components", "steps", "TestStep.tsx")
    for outcome in ("passed", "failed", "pending", "skipped"):
        assert f'{outcome}:' in test_step, f"the {outcome} outcome must be distinct"

    wizard = read("features", "onboarding", "components", "OnboardWizard.tsx")
    assert "state.status === 403" in wizard, "a permission narrowed mid-flow"
    assert "saved is intact" in wizard
