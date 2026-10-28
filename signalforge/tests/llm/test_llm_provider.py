"""What the provider owes every caller (REQ-051, S5-T02).

The playbook's list for `llm.provider` is a list of behaviours, not of files, so
these tests drive it as behaviour: structured output that is *validated* rather
than hoped for, retries that fire for transient failures and never for a bad
request, a cache key honest enough to invalidate itself, a budget that stops
spending, and - the one that matters at 3am - a model failure that produces the
deterministic answer instead of an exception.
"""

from __future__ import annotations

import httpx
import pytest

from signalforge.domain.llm.budget import (
    NO_INCIDENT_LANE,
    InMemoryTokenLedger,
    budget_key,
)
from signalforge.domain.llm.entities import (
    CompletionRequest,
    DegradedReason,
    Message,
    ModelClass,
    TokenUsage,
)
from signalforge.infrastructure.llm.cache import cache_key
from signalforge.infrastructure.observability.metrics import build_registry
from signalforge.tests.llm.conftest import (
    chat_response,
    embedding_response,
    provider,
    settings,
    user,
)

pytestmark = pytest.mark.anyio

SEVERITY_SCHEMA = {
    "type": "object",
    "required": ["severity", "why"],
    "properties": {
        "severity": {"type": "string", "enum": ["P1", "P2", "P3"]},
        "why": {"type": "string"},
    },
    "additionalProperties": False,
}


def ask(**overrides) -> CompletionRequest:
    fields = {
        "purpose": "severity-narrative",
        "messages": user("error rate is 12% on checkout"),
        "fallback": "deterministic summary",
    }
    fields.update(overrides)
    return CompletionRequest(**fields)


# --- structured outputs -------------------------------------------------------


@pytest.mark.req("REQ-051")
async def test_a_schema_is_requested_on_the_wire_and_validated_on_the_way_back(gateway):
    gateway.responses = [chat_response('{"severity": "P2", "why": "error budget"}')]
    result = await provider(gateway).complete(ask(schema=SEVERITY_SCHEMA))

    sent = gateway.calls[0].body
    assert sent["response_format"]["type"] == "json_schema"
    assert sent["response_format"]["json_schema"]["schema"] == SEVERITY_SCHEMA
    # The schema is restated in the prompt too, so a model that ignores
    # response_format still has a chance of answering correctly.
    assert any("JSON Schema" in m["content"] for m in sent["messages"])
    assert result.data == {"severity": "P2", "why": "error budget"}
    assert not result.degraded


@pytest.mark.req("REQ-051")
async def test_a_fenced_json_answer_is_still_accepted(gateway):
    gateway.responses = [chat_response('```json\n{"severity": "P3", "why": "ok"}\n```')]
    result = await provider(gateway).complete(ask(schema=SEVERITY_SCHEMA))
    assert result.data["severity"] == "P3"


@pytest.mark.req("REQ-051")
async def test_an_off_schema_answer_is_treated_as_no_answer(gateway, listener):
    """A half-valid object arriving in the incident graph wearing the model's
    authority is worse than the deterministic answer."""
    gateway.responses = [chat_response('{"severity": "SEV1"}')]
    result = await provider(gateway, listener=listener).complete(
        ask(schema=SEVERITY_SCHEMA, fallback={"severity": "P3", "why": "default"})
    )

    assert result.degraded and result.reason is DegradedReason.INVALID_RESPONSE
    assert result.data == {"severity": "P3", "why": "default"}
    assert listener.reasons == ["invalid_response"]


@pytest.mark.req("REQ-051")
async def test_prose_where_json_was_required_degrades(gateway):
    gateway.responses = [chat_response("I think it is probably a P2.")]
    result = await provider(gateway).complete(ask(schema=SEVERITY_SCHEMA))
    assert result.degraded and result.reason is DegradedReason.INVALID_RESPONSE


@pytest.mark.req("REQ-051")
async def test_a_gateway_that_rejects_json_schema_is_retried_as_a_json_object(gateway):
    """The fleet behind the gateway is mixed. A model that does not take
    `json_schema` gets one second attempt in the weaker mode - and the answer is
    validated identically either way, so the caller's guarantee is unchanged."""
    gateway.responses = [
        httpx.Response(400, json={"error": {"message": "response_format not supported"}}),
        chat_response('{"severity": "P1", "why": "customer impact"}'),
    ]
    result = await provider(gateway).complete(ask(schema=SEVERITY_SCHEMA))

    assert [call.body["response_format"]["type"] for call in gateway.calls] == [
        "json_schema",
        "json_object",
    ]
    assert result.data["severity"] == "P1" and not result.degraded


# --- retries: transient only --------------------------------------------------


@pytest.mark.req("REQ-051")
async def test_a_transient_failure_is_retried_and_then_succeeds(gateway):
    gateway.responses = [
        httpx.Response(503, json={"error": {"message": "upstream busy"}}),
        chat_response("recovered"),
    ]
    result = await provider(gateway, env={"LLM_MAX_ATTEMPTS": "3"}).complete(ask())
    assert len(gateway.calls) == 2
    assert result.text == "recovered" and not result.degraded


@pytest.mark.req("REQ-051")
async def test_a_bad_request_is_never_retried(gateway, listener):
    """A 401 fails identically next time; retrying only spends the budget."""
    gateway.responses = [httpx.Response(401, json={"error": {"message": "denied"}})]
    result = await provider(gateway, listener=listener).complete(ask())

    assert len(gateway.calls) == 1
    assert result.degraded and result.reason is DegradedReason.UPSTREAM_ERROR
    assert result.text == "deterministic summary"


@pytest.mark.req("REQ-051")
async def test_retries_are_bounded_and_then_the_call_degrades(gateway, listener):
    gateway.responses = [httpx.Response(503, json={}) for _ in range(3)]
    result = await provider(
        gateway, listener=listener, env={"LLM_MAX_ATTEMPTS": "3"}
    ).complete(ask())

    assert len(gateway.calls) == 3, "attempts must be bounded by configuration"
    assert result.degraded and result.reason is DegradedReason.UPSTREAM_ERROR


@pytest.mark.req("REQ-051")
async def test_a_timeout_degrades_rather_than_hanging_the_incident(gateway, listener):
    gateway.responses = [httpx.ReadTimeout("too slow"), httpx.ReadTimeout("too slow")]
    result = await provider(
        gateway, listener=listener, env={"LLM_MAX_ATTEMPTS": "2", "LLM_TIMEOUT_S": "3"}
    ).complete(ask())

    assert result.degraded and result.reason is DegradedReason.TIMEOUT
    assert result.text == "deterministic summary"
    assert listener.notices[0].detail.startswith("gateway timed out after 3.0s")


@pytest.mark.req("REQ-051")
async def test_an_unreachable_gateway_degrades(gateway, listener):
    gateway.responses = [httpx.ConnectError("no route"), httpx.ConnectError("no route")]
    result = await provider(
        gateway, listener=listener, env={"LLM_MAX_ATTEMPTS": "2"}
    ).complete(ask())
    assert result.degraded and result.reason is DegradedReason.TRANSPORT


# --- the cache ----------------------------------------------------------------


@pytest.mark.req("REQ-051")
async def test_an_identical_question_is_answered_from_cache(gateway, cache):
    gateway.responses = [chat_response("the same answer")]
    subject = provider(gateway, cache=cache)

    first = await subject.complete(ask(tenant_id="tenant-a"))
    second = await subject.complete(ask(tenant_id="tenant-a"))

    assert len(gateway.calls) == 1, "the second call must not reach the gateway"
    assert second.cached and not second.degraded
    assert second.text == first.text == "the same answer"
    assert second.usage.total == first.usage.total, "cached answers keep their cost"


@pytest.mark.req("REQ-051", "REQ-013")
async def test_one_tenants_cached_answer_is_never_served_to_another(gateway, cache):
    gateway.responses = [chat_response("tenant a answer"), chat_response("tenant b answer")]
    subject = provider(gateway, cache=cache)

    await subject.complete(ask(tenant_id="tenant-a"))
    second = await subject.complete(ask(tenant_id="tenant-b"))

    assert len(gateway.calls) == 2
    assert second.text == "tenant b answer" and not second.cached


@pytest.mark.req("REQ-051")
async def test_cosmetic_whitespace_hits_the_same_entry_but_a_changed_word_does_not(
    gateway, cache
):
    gateway.responses = [chat_response("a"), chat_response("b")]
    subject = provider(gateway, cache=cache)

    await subject.complete(ask(messages=user("error rate is 12% on checkout")))
    await subject.complete(ask(messages=user("  error rate is   12% on checkout  ")))
    assert len(gateway.calls) == 1, "normalised input must reuse the entry"

    await subject.complete(ask(messages=user("error rate is 13% on checkout")))
    assert len(gateway.calls) == 2, "a different question is a different key"


@pytest.mark.req("REQ-051")
def test_the_cache_key_covers_model_config_version_and_schema():
    """Everything that could change an answer must change the key, or a warm
    cache quietly serves answers produced by rules that no longer exist."""
    base = dict(provider="genailab", model="model-a", config_version="1")
    key = cache_key(ask(), **base)

    assert cache_key(ask(), **{**base, "model": "model-b"}) != key
    assert cache_key(ask(), **{**base, "config_version": "2"}) != key
    assert cache_key(ask(), **{**base, "provider": "other"}) != key
    assert cache_key(ask(schema=SEVERITY_SCHEMA), **base) != key
    assert cache_key(ask(prompt_version="2"), **base) != key
    assert cache_key(ask(temperature=0.9), **base) != key
    assert cache_key(ask(model_class=ModelClass.REASONING), **base) != key
    assert cache_key(ask(purpose="something-else"), **base) != key
    # ...and nothing that cannot. The fallback is the caller's business.
    assert cache_key(ask(fallback="a different deterministic answer"), **base) == key
    assert cache_key(ask(), **base) == key, "the key must be a pure function"


@pytest.mark.req("REQ-051")
async def test_a_degraded_answer_is_never_cached(gateway, cache):
    gateway.responses = [httpx.Response(401, json={}), chat_response("live answer")]
    subject = provider(gateway, cache=cache)

    await subject.complete(ask(tenant_id="tenant-a"))
    assert cache.writes == 0, "a deterministic fallback must not be remembered as an answer"

    second = await subject.complete(ask(tenant_id="tenant-a"))
    assert second.text == "live answer" and not second.degraded


@pytest.mark.req("REQ-051")
async def test_the_cache_can_be_switched_off_by_configuration(gateway, cache):
    gateway.responses = [chat_response("one"), chat_response("two")]
    subject = provider(gateway, cache=cache, env={"LLM_CACHE": "false"})

    await subject.complete(ask(tenant_id="tenant-a"))
    await subject.complete(ask(tenant_id="tenant-a"))
    assert len(gateway.calls) == 2 and cache.writes == 0


@pytest.mark.req("REQ-051")
async def test_a_warm_cache_still_answers_when_the_model_is_gone(gateway, cache):
    """A rehearsed drill keeps working after the key is revoked, which is what
    makes the cache worth having on the demo path (REQ-071)."""
    gateway.responses = [chat_response("warm answer")]
    warm = provider(gateway, cache=cache)
    await warm.complete(ask(tenant_id="tenant-a"))

    cold = provider(cache=cache, env={"GENAILAB_API_KEY": ""})
    served = await cold.complete(ask(tenant_id="tenant-a"))
    assert served.cached and not served.degraded and served.text == "warm answer"


# --- budgets ------------------------------------------------------------------


@pytest.mark.req("REQ-051")
async def test_usage_is_charged_to_the_incident_that_spent_it(gateway):
    ledger = InMemoryTokenLedger(100_000)
    gateway.responses = [chat_response("ok", prompt_tokens=30, completion_tokens=12)]
    subject = provider(gateway, ledger=ledger)

    result = await subject.complete(ask(incident_id="inc-1"))

    assert result.usage == TokenUsage(30, 12)
    assert ledger.spent("inc-1").total == 42
    assert ledger.spent(NO_INCIDENT_LANE).total == 0, "another lane must not be charged"


@pytest.mark.req("REQ-051")
async def test_a_spent_budget_stops_the_call_before_it_reaches_the_gateway(
    gateway, listener
):
    ledger = InMemoryTokenLedger(1_000)
    ledger.charge("inc-1", TokenUsage(prompt=999, completion=0))
    subject = provider(gateway, ledger=ledger, listener=listener)

    result = await subject.complete(ask(incident_id="inc-1"))

    assert gateway.calls == [], "a spent budget must not spend more"
    assert result.degraded and result.reason is DegradedReason.BUDGET_EXHAUSTED
    assert result.text == "deterministic summary"
    assert listener.reasons == ["budget_exhausted"]


@pytest.mark.req("REQ-051")
async def test_one_incidents_exhausted_budget_does_not_stop_another(gateway):
    ledger = InMemoryTokenLedger(1_000)
    ledger.charge("inc-1", TokenUsage(prompt=1_000, completion=0))
    gateway.responses = [chat_response("still working")]

    result = await provider(gateway, ledger=ledger).complete(ask(incident_id="inc-2"))
    assert not result.degraded


@pytest.mark.req("REQ-051")
def test_work_outside_an_incident_is_accounted_for_in_its_own_lane():
    assert budget_key(None) == NO_INCIDENT_LANE
    assert budget_key("inc-9") == "inc-9"
    ledger = InMemoryTokenLedger(10)
    assert not ledger.would_exceed("inc-9", 10)
    assert ledger.would_exceed("inc-9", 11)


# --- model selection, provenance and metrics ----------------------------------


@pytest.mark.req("REQ-051")
async def test_the_classification_chooses_the_model_id(gateway):
    gateway.responses = [chat_response("a"), chat_response("b")]
    subject = provider(
        gateway,
        env={
            "LLM_MODEL_GENERAL": "fast-model",
            "LLM_MODEL_REASONING": "deep-model",
        },
    )

    await subject.complete(ask(model_class=ModelClass.GENERAL))
    await subject.complete(ask(model_class=ModelClass.REASONING))

    assert [call.body["model"] for call in gateway.calls] == ["fast-model", "deep-model"]


@pytest.mark.req("REQ-051")
async def test_the_answer_carries_provenance_a_graph_node_can_record(gateway):
    gateway.responses = [chat_response("narrative", model="genailab-maas-gpt-5.4")]
    result = await provider(gateway).complete(ask())

    assert result.provenance == {
        "purpose": "severity-narrative",
        "provider": "genailab",
        # The id the gateway says it served, not the one that was asked for.
        "model": "genailab-maas-gpt-5.4",
        "model_class": "general",
        "cached": False,
        "degraded": False,
        "reason": None,
        "tokens": 18,
    }


@pytest.mark.req("REQ-051", "REQ-025")
async def test_metrics_count_calls_tokens_and_degradations_without_identifying_anyone(
    gateway,
):
    metrics = build_registry()
    gateway.responses = [chat_response("ok"), httpx.Response(401, json={})]
    subject = provider(gateway, metrics=metrics)

    await subject.complete(ask(tenant_id="tenant-a", incident_id="inc-1"))
    await subject.complete(ask(tenant_id="tenant-a", incident_id="inc-1"))

    assert metrics.value(
        "signalforge_llm_calls_total",
        {"model_class": "general", "outcome": "ok", "reason": ""},
    ) == 1
    assert metrics.value(
        "signalforge_llm_calls_total",
        {"model_class": "general", "outcome": "degraded", "reason": "upstream_error"},
    ) == 1
    assert metrics.value(
        "signalforge_llm_tokens_total", {"model_class": "general", "kind": "prompt"}
    ) == 11
    rendered = metrics.render()
    assert "inc-1" not in rendered and "tenant-a" not in rendered


# --- embeddings ---------------------------------------------------------------


@pytest.mark.req("REQ-051", "REQ-035")
async def test_embeddings_go_to_the_gateway_with_the_configured_model(gateway):
    gateway.responses = [embedding_response([[0.1, 0.2], [0.3, 0.4]])]
    subject = provider(gateway, env={"LLM_MODEL_EMBEDDINGS": "embed-model"})

    result = await subject.embed(["one", "two"], tenant_id="tenant-a")

    assert gateway.calls[0].body == {"model": "embed-model", "input": ["one", "two"]}
    assert result.vectors == ((0.1, 0.2), (0.3, 0.4))
    assert not result.degraded


@pytest.mark.req("REQ-051", "REQ-035")
async def test_vectors_are_ordered_by_the_index_the_gateway_returned(gateway):
    """The contract allows any order. A silently transposed corpus would poison
    every similarity score that follows it."""
    gateway.responses = [
        httpx.Response(
            200,
            json={
                "model": "embed-model",
                "data": [
                    {"index": 1, "embedding": [9.0]},
                    {"index": 0, "embedding": [1.0]},
                ],
                "usage": {"prompt_tokens": 2},
            },
        )
    ]
    result = await provider(gateway).embed(["first", "second"])
    assert result.vectors == ((1.0,), (9.0,))


@pytest.mark.req("REQ-051", "REQ-035")
async def test_an_embedding_failure_returns_no_vectors_rather_than_raising(
    gateway, listener
):
    gateway.responses = [httpx.Response(500, json={}), httpx.Response(500, json={})]
    result = await provider(
        gateway, listener=listener, env={"LLM_MAX_ATTEMPTS": "2"}
    ).embed(["a runbook"])

    assert result.degraded and result.vectors == ()
    assert listener.notices[0].model_class is ModelClass.EMBEDDINGS


@pytest.mark.req("REQ-051", "REQ-035")
async def test_embeddings_are_cached_by_input_hash(gateway, cache):
    gateway.responses = [embedding_response([[0.5]])]
    subject = provider(gateway, cache=cache)

    await subject.embed(["a runbook"], tenant_id="tenant-a")
    again = await subject.embed(["a runbook"], tenant_id="tenant-a")

    assert len(gateway.calls) == 1
    assert again.cached and again.vectors == ((0.5,),)


@pytest.mark.req("REQ-051")
async def test_an_empty_embedding_request_costs_nothing(gateway):
    result = await provider(gateway).embed([])
    assert result.vectors == () and gateway.calls == []


# --- the shape of a request ---------------------------------------------------


@pytest.mark.req("REQ-051")
def test_a_request_must_name_its_purpose_and_carry_a_message():
    with pytest.raises(ValueError):
        CompletionRequest(purpose="", messages=user("hi"))
    with pytest.raises(ValueError):
        CompletionRequest(purpose="p", messages=())
    with pytest.raises(ValueError):
        Message(role="root", content="hi")
    with pytest.raises(ValueError):
        # Embeddings are a different call, not a classification of completion.
        CompletionRequest(
            purpose="p", messages=user("hi"), model_class=ModelClass.EMBEDDINGS
        )


@pytest.mark.req("REQ-051")
def test_the_provider_reports_a_credential_free_status():
    subject = provider(env={"GENAILAB_API_KEY": "a-secret-value"})
    status = subject.status
    assert status["key_present"] is True and "a-secret-value" not in str(status)
    assert status["endpoint"] == "https://genailab.tcs.in/v1"
    assert status["tls_verify"] is True
    assert settings().provider == "genailab"
