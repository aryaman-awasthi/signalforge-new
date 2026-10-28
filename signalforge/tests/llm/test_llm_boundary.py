"""The provider boundary: every model call leaves through the approved gateway.

This is the security suite for the model path, written before the provider
existed. Three claims are load-bearing and each one is a regression test rather
than a convention:

1. **Traffic can only reach an approved host.** The approved set is a constant
   in the code, not an environment variable, so no deployment - and no leaked
   `.env` - can point SignalForge's prompts at a public model API. A base URL
   outside the set does not raise at boot and does not fall back to a default
   either: the provider simply has no live client and answers deterministically.
2. **The provider is never inferred from a variable name.** `OPENAI_API_KEY` is
   accepted as an alias so an existing local setup keeps working, but it selects
   nothing: the endpoint stays the TCS gateway. `OPENAI_BASE_URL` and
   `OPENAI_API_BASE` are ignored outright, because honouring them would be a
   one-line environment change from sending incident text to a third party.
3. **The key never leaves the client.** Not in a repr, not in a log record, not
   in the `system.llm.degraded` event, not in a metric label.
"""

from __future__ import annotations

import logging
from pathlib import Path

import httpx
import pytest

from signalforge.domain.llm.entities import (
    CompletionRequest,
    DegradedReason,
    ModelClass,
)
from signalforge.infrastructure.llm.hosts import (
    APPROVED_LLM_HOSTS,
    endpoint_is_approved,
)
from signalforge.tests.llm.conftest import (
    FakeGatewayTransport,
    chat_response,
    embedding_response,
    provider,
    settings,
    user,
)

pytestmark = pytest.mark.anyio

ROOT = Path(__file__).resolve().parents[3]
SECRET = "sk-not-a-real-key-0123456789"


# --- the endpoint ------------------------------------------------------------


@pytest.mark.req("REQ-051")
def test_the_default_endpoint_is_the_tcs_gateway():
    resolved = settings()
    assert resolved.base_url == "https://genailab.tcs.in/v1"
    # The Claude API joined the allowlist on an explicit instruction (S10-T19);
    # the default endpoint is still the organiser's gateway.
    assert APPROVED_LLM_HOSTS == frozenset({"genailab.tcs.in", "api.anthropic.com"})


@pytest.mark.req("REQ-051")
@pytest.mark.parametrize(
    "configured",
    [
        "https://genailab.tcs.in",
        "https://genailab.tcs.in/",
        "https://genailab.tcs.in/v1",
        "https://genailab.tcs.in/v1/",
        "  https://genailab.tcs.in/v1  ",
    ],
)
def test_base_url_is_normalized_with_or_without_the_version_suffix(configured):
    """An operator should be able to paste either form of the endpoint."""
    assert settings(GENAILAB_BASE_URL=configured).base_url == "https://genailab.tcs.in/v1"


@pytest.mark.req("REQ-051")
@pytest.mark.parametrize(
    "hostile",
    [
        "https://api.openai.com/v1",
        "https://api.openai.com",
        "http://api.openai.com/v1",
        "https://generativelanguage.googleapis.com/v1",
        "http://api.anthropic.com/v1",
        "https://api.anthropic.com.evil.example/v1",
        "https://genailab.tcs.in.evil.example/v1",
        "https://evil.example/genailab.tcs.in/v1",
    ],
)
def test_an_unapproved_ai_host_is_refused_rather_than_used(hostile):
    resolved = settings(GENAILAB_BASE_URL=hostile, GENAILAB_API_KEY=SECRET)
    assert not endpoint_is_approved(hostile)
    assert not resolved.endpoint_approved
    assert not resolved.configured, "an unapproved endpoint must never be live"


@pytest.mark.req("REQ-051")
async def test_a_misconfigured_endpoint_issues_no_request_at_all(listener):
    """The check is not advisory. With a hostile endpoint configured, the
    provider holds no client, so there is nothing to send even by accident."""
    gateway = FakeGatewayTransport()  # raises on any call it was not told about
    subject = provider(
        gateway,
        listener=listener,
        env={"GENAILAB_BASE_URL": "https://api.openai.com/v1", "GENAILAB_API_KEY": SECRET},
    )

    result = await subject.complete(
        CompletionRequest(
            purpose="join-brief", messages=user("summarise"), fallback="deterministic"
        )
    )

    assert gateway.calls == [], "a refused endpoint must not be contacted"
    assert result.degraded and result.text == "deterministic"
    assert result.reason is DegradedReason.UNCONFIGURED
    assert listener.reasons == ["unconfigured"]


@pytest.mark.req("REQ-051")
async def test_every_live_call_goes_to_the_gateway_and_nowhere_else(gateway):
    """The positive half of the same claim, across both traffic kinds."""
    gateway.responses = [chat_response("ok"), embedding_response([[0.1, 0.2]])]
    subject = provider(gateway)

    await subject.complete(CompletionRequest(purpose="join-brief", messages=user("hi")))
    await subject.embed(["a runbook"])

    assert gateway.hosts == {"genailab.tcs.in"}
    paths = [httpx.URL(call.url).path for call in gateway.calls]
    assert paths == ["/v1/chat/completions", "/v1/embeddings"]
    assert not any("openai.com" in call.url for call in gateway.calls)


@pytest.mark.req("REQ-051")
@pytest.mark.parametrize("variable", ["OPENAI_BASE_URL", "OPENAI_API_BASE"])
def test_openai_base_url_variables_are_ignored_entirely(variable):
    """A stray variable in a shell profile must not be able to redirect prompts."""
    resolved = settings(**{variable: "https://api.openai.com/v1", "GENAILAB_API_KEY": SECRET})
    assert resolved.base_url == "https://genailab.tcs.in/v1"
    assert resolved.configured


# --- the credential ----------------------------------------------------------


@pytest.mark.req("REQ-051", "REQ-015")
def test_the_genailab_key_wins_over_the_openai_alias():
    resolved = settings(GENAILAB_API_KEY="genailab-wins", OPENAI_API_KEY="alias-loses")
    assert resolved.api_key == "genailab-wins"
    assert resolved.key_source == "GENAILAB_API_KEY"


@pytest.mark.req("REQ-051", "REQ-015")
def test_the_openai_alias_is_accepted_but_still_points_at_the_gateway():
    """Compatibility with an existing local setup, without ever inferring a
    provider from a variable name."""
    resolved = settings(OPENAI_API_KEY=SECRET)
    assert resolved.api_key == SECRET
    assert resolved.key_source == "OPENAI_API_KEY"
    assert resolved.base_url == "https://genailab.tcs.in/v1"
    assert resolved.configured


@pytest.mark.req("REQ-051", "REQ-015")
async def test_the_key_never_appears_in_a_repr_a_log_or_an_event(gateway, listener, caplog):
    gateway.responses = [httpx.Response(401, json={"error": {"message": "bad key"}})]
    subject = provider(gateway, listener=listener, env={"GENAILAB_API_KEY": SECRET})

    with caplog.at_level(logging.DEBUG):
        result = await subject.complete(
            CompletionRequest(purpose="join-brief", messages=user("hi"), fallback="det")
        )

    surfaces = [
        repr(subject),
        repr(subject.settings),
        str(subject.settings),
        repr(result),
        repr(listener.notices[0]),
        listener.notices[0].detail,
        caplog.text,
    ]
    for surface in surfaces:
        assert SECRET not in surface, f"the api key leaked into {surface[:120]!r}"
    # The gateway still received it - it is a credential, not a secret from the
    # gateway - which is what makes the assertions above meaningful.
    assert gateway.calls[0].headers["authorization"] == f"Bearer {SECRET}"


@pytest.mark.req("REQ-051")
async def test_a_missing_key_degrades_the_model_path_and_boots_everything_else(listener):
    """A deployment with no key must still be a working incident platform."""
    subject = provider(listener=listener, env={"GENAILAB_API_KEY": ""})
    assert not subject.configured

    result = await subject.complete(
        CompletionRequest(purpose="join-brief", messages=user("hi"), fallback={"summary": "x"})
    )
    assert result.degraded and result.data == {"summary": "x"}
    assert result.reason is DegradedReason.UNCONFIGURED

    vectors = await subject.embed(["anything"])
    assert vectors.degraded and vectors.vectors == ()


# --- transport-level floors --------------------------------------------------


@pytest.mark.req("REQ-051")
def test_tls_verification_is_on_by_default_and_cannot_be_switched_off_by_config():
    assert settings().verify is True
    # A CA bundle is the supported way to solve a corporate-proxy certificate
    # problem; there is no variable that disables verification.
    bundled = settings(LLM_CA_BUNDLE=str(ROOT / "pyproject.toml"))
    assert bundled.verify == str(ROOT / "pyproject.toml")
    assert settings(LLM_VERIFY_TLS="0", LLM_INSECURE="1").verify is True


@pytest.mark.req("REQ-051")
def test_no_verify_false_is_hardcoded_anywhere_in_the_model_path():
    source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (ROOT / "signalforge" / "infrastructure" / "llm").glob("*.py")
    )
    assert "verify=False" not in source.replace(" ", "")


@pytest.mark.req("REQ-051")
def test_the_timeout_is_bounded_and_cannot_be_removed():
    assert settings().timeout_s == 20.0
    assert settings(LLM_TIMEOUT_S="5").timeout_s == 5.0
    assert settings(LLM_TIMEOUT_S="0").timeout_s > 0
    assert settings(LLM_TIMEOUT_S="600").timeout_s <= 120.0, "an unbounded call is a hang"


# --- the abstraction itself --------------------------------------------------


def _sources(*parts: str):
    return [p for p in (ROOT / "signalforge").joinpath(*parts).rglob("*.py")]


@pytest.mark.req("REQ-051")
def test_no_model_id_is_hardcoded_in_domain_or_application_code():
    """Model ids are configuration (CLAUDE.md, Environment). A call site names a
    classification; only infrastructure config knows which model serves it."""
    fingerprints = ("genailab-maas", "gpt-4", "gpt-5", "text-embedding", "gemini-", "deepseek")
    offenders = []
    for path in _sources("domain") + _sources("application"):
        text = path.read_text(encoding="utf-8").lower()
        for fingerprint in fingerprints:
            if fingerprint in text:
                offenders.append(f"{path.relative_to(ROOT)}: {fingerprint}")
    assert not offenders, f"model ids leaked out of configuration: {offenders}"


@pytest.mark.req("REQ-051", "REQ-000")
def test_domain_code_imports_no_vendor_sdk_and_no_http_client():
    banned = ("import httpx", "import openai", "from openai", "langchain", "import requests")
    offenders = [
        f"{path.relative_to(ROOT)}: {token}"
        for path in _sources("domain")
        for token in banned
        if token in path.read_text(encoding="utf-8")
    ]
    assert not offenders, f"the domain reached for a vendor SDK: {offenders}"


@pytest.mark.req("REQ-051")
def test_application_code_reaches_the_model_only_through_the_port():
    """One abstraction, held by the application layer as the domain port. An
    application module importing `infrastructure.llm` would be a second one."""
    offenders = [
        str(path.relative_to(ROOT))
        for path in _sources("application")
        if "infrastructure.llm" in path.read_text(encoding="utf-8")
    ]
    assert not offenders, f"application code bound itself to an implementation: {offenders}"


@pytest.mark.req("REQ-051")
def test_model_selection_is_by_classification():
    resolved = settings(
        LLM_MODEL_GENERAL="genailab-maas-gpt-5.4-mini",
        LLM_MODEL_REASONING="genailab-maas-gpt-5.4",
        LLM_MODEL_EMBEDDINGS="azure/genailab-maas-text-embedding-3-large",
    )
    assert resolved.model_for(ModelClass.GENERAL) == "genailab-maas-gpt-5.4-mini"
    assert resolved.model_for(ModelClass.REASONING) == "genailab-maas-gpt-5.4"
    assert (
        resolved.model_for(ModelClass.EMBEDDINGS)
        == "azure/genailab-maas-text-embedding-3-large"
    )


@pytest.mark.req("REQ-051")
def test_the_shipped_defaults_are_the_approved_tcs_model_ids():
    resolved = settings()
    assert resolved.model_for(ModelClass.GENERAL) == "genailab-maas-gpt-5.4-mini"
    assert resolved.model_for(ModelClass.REASONING) == "genailab-maas-gpt-5.4"
    assert (
        resolved.model_for(ModelClass.EMBEDDINGS)
        == "azure/genailab-maas-text-embedding-3-large"
    )
