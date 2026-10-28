"""kb-index: knowledge that survives a model outage (REQ-035).

The load-bearing claim is the fallback. Ranking is hybrid when an embedding
provider is reachable and lexical when it is not, and a provider that raises -
at index time or at query time - must degrade the *ordering* and never the
search. The tests below therefore spend most of their time on the failure paths.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Sequence

import pytest

from signalforge.contracts.canonical import KnowledgeDocType, KnowledgeDocument
from signalforge.contracts.errors import AdapterError
from signalforge.domain.integrations.ports import EmbeddingProvider
from signalforge.domain.integrations.ranking import bm25_scores, snippet, tokenize
from signalforge.tests.adapters.conftest import connect

pytestmark = pytest.mark.anyio

NOW = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)

RUNBOOKS = [
    (
        "rb-checkout",
        "Checkout error rate runbook",
        "When checkout error rate climbs, check the billing dependency first and "
        "roll back the most recent deploy. Escalate to the payments team.",
        ["checkout", "errors"],
    ),
    (
        "rb-latency",
        "Latency runbook",
        "Latency regressions usually follow a cache eviction. Warm the cache and "
        "watch p99 for ten minutes.",
        ["latency"],
    ),
    (
        "pir-2026-08",
        "PIR: August checkout outage",
        "Root cause was a billing schema migration. The rollback was blocked by an "
        "in-flight deploy. Lesson: pause deploys during an incident.",
        ["pir", "checkout"],
    ),
]


class StubEmbeddings(EmbeddingProvider):
    """A deterministic provider. Not a model - a fixture that lets the blending
    and the failure handling be asserted without a network call."""

    model = "stub-embeddings-1"

    def __init__(self, vectors: dict[str, list[float]], fail: bool = False):
        self.vectors = vectors
        self.fail = fail
        self.calls = 0

    async def embed(self, texts: Sequence[str]) -> Sequence[Sequence[float]]:
        self.calls += 1
        if self.fail:
            raise RuntimeError("model endpoint unreachable")
        return [self.vectors.get(_key(text), [0.0, 0.0, 1.0]) for text in texts]


def _key(text: str) -> str:
    lowered = text.lower()
    if "checkout error rate runbook" in lowered:
        return "checkout"
    if "latency runbook" in lowered:
        return "latency"
    if "pir" in lowered:
        return "pir"
    return lowered.strip()


def document(doc_id, title, body, tags, doc_type=KnowledgeDocType.RUNBOOK):
    return KnowledgeDocument(
        doc_id=doc_id,
        title=title,
        body=body,
        doc_type=doc_type,
        tags=tags,
        updated_at=NOW,
    )


@pytest.fixture
def kb(api):
    signup = api.signup()
    token = signup["tokens"]["access_token"]
    tenant_id = signup["org"]["id"]

    class Kb:
        tenant = tenant_id
        access_token = token
        container = api.container

        def bind(self, **config):
            return connect(
                api, token, "kb-index", credentials={}, config=config or {}
            )

        async def invoke(self, binding, capability, *args, **kwargs):
            return await api.container.invoker.invoke(
                tenant_id, binding["id"], capability, *args, **kwargs
            )

        async def load(self, binding):
            for doc_id, title, body, tags in RUNBOOKS:
                await self.invoke(binding, "index", document(doc_id, title, body, tags))

        def provider(self, provider):
            api.container.adapter_services.embeddings = provider

    return Kb()


# --- ranking primitives -------------------------------------------------------


@pytest.mark.req("REQ-035")
def test_tokenisation_keeps_the_words_that_matter_in_incident_text():
    tokens = tokenize("Checkout-api p99.9 was 3.2s for v1.2.3 and the cache")
    assert "checkout-api" in tokens
    assert "p99.9" in tokens
    assert "v1.2.3" in tokens
    assert "the" not in tokens and "was" not in tokens


@pytest.mark.req("REQ-035")
def test_a_term_in_every_document_never_scores_negative():
    """Without the smoothing, a word in most of the corpus scores negative and
    a runbook is penalised for containing the word that was searched for."""
    corpus = [tokenize("checkout is down"), tokenize("checkout is slow")]
    everywhere = bm25_scores(tokenize("checkout"), corpus)
    assert all(score > 0 for score in everywhere)
    assert everywhere[0] == everywhere[1]

    rare = bm25_scores(tokenize("down"), corpus)
    assert rare[0] > everywhere[0], "a discriminating term outscores a universal one"
    assert rare[1] == 0.0


@pytest.mark.req("REQ-035")
def test_trailing_punctuation_is_not_part_of_a_word():
    assert tokenize("roll back the deploy.") == ("roll", "back", "deploy")


@pytest.mark.req("REQ-035")
def test_the_snippet_is_taken_around_the_match_not_from_the_top():
    body = ("boilerplate " * 40) + "the rollback was blocked by an in-flight deploy"
    text = snippet(body, tokenize("rollback"))
    assert "rollback" in text
    assert text.startswith("...")


# --- indexing and search ------------------------------------------------------


@pytest.mark.req("REQ-035")
async def test_search_returns_scored_hits_without_any_model(kb):
    binding = kb.bind()
    await kb.load(binding)

    hits = await kb.invoke(binding, "search", "checkout error rate rollback", 3)
    assert hits, "BM25 is the floor and always runs"
    assert hits[0].doc_id == "rb-checkout"
    assert hits[0].score > 0
    assert "checkout" in hits[0].snippet.lower()
    assert [hit.score for hit in hits] == sorted(
        (hit.score for hit in hits), reverse=True
    )


@pytest.mark.req("REQ-035")
async def test_k_bounds_the_result_and_zero_returns_nothing(kb):
    binding = kb.bind()
    await kb.load(binding)
    assert len(await kb.invoke(binding, "search", "deploy", 1)) == 1
    assert await kb.invoke(binding, "search", "deploy", 0) == []


@pytest.mark.req("REQ-035")
async def test_a_query_that_matches_nothing_returns_nothing(kb):
    binding = kb.bind()
    await kb.load(binding)
    assert await kb.invoke(binding, "search", "kubernetes etcd quorum", 5) == []


@pytest.mark.req("REQ-035")
async def test_reindexing_replaces_rather_than_duplicating(kb):
    binding = kb.bind()
    await kb.load(binding)
    await kb.invoke(
        binding,
        "index",
        document(
            "rb-checkout",
            "Checkout error rate runbook",
            "Superseded: the billing dependency check moved to the payments runbook.",
            ["checkout"],
        ),
    )
    hits = await kb.invoke(binding, "search", "checkout", 5)
    assert [hit.doc_id for hit in hits].count("rb-checkout") == 1
    hit = next(h for h in hits if h.doc_id == "rb-checkout")
    assert "Superseded" in hit.snippet, "the stale copy cannot still be hit"


@pytest.mark.req("REQ-035")
async def test_an_empty_document_is_refused(kb):
    binding = kb.bind()
    with pytest.raises(AdapterError):
        await kb.invoke(binding, "index", document("empty", "Empty", "   ", []))


@pytest.mark.req("REQ-035", "REQ-018")
async def test_redaction_happens_at_index_time(kb):
    """A runbook's text reaches briefs and prompts, so what must not be there
    must never be stored - a policy applied on read could be bypassed."""
    binding = kb.bind()
    await kb.invoke(
        binding,
        "index",
        document(
            "rb-secrets",
            "Access runbook",
            "Contact ada@example.com. api_key: sk-live-9999. Card 4111 1111 1111 1111.",
            ["access"],
        ),
    )
    # Read the row itself: redaction has to be true in the database, not only
    # in what search happens to return.
    async with kb.container.integration_uow_factory() as uow:
        stored = await uow.knowledge.get(kb.tenant, "rb-secrets")
    assert "ada@example.com" not in stored.body
    assert "sk-live-9999" not in stored.body
    assert "4111" not in stored.body
    assert stored.redaction_policy_id == "kb-index-default"


# --- embeddings and their failure ---------------------------------------------


@pytest.mark.req("REQ-035")
async def test_embeddings_are_blended_when_a_provider_is_installed(kb):
    provider = StubEmbeddings(
        {
            "checkout": [1.0, 0.0, 0.0],
            "latency": [0.0, 1.0, 0.0],
            "pir": [0.9, 0.1, 0.0],
            "cache eviction": [0.0, 1.0, 0.0],
        }
    )
    kb.provider(provider)
    binding = kb.bind(semantic_weight=0.9)
    await kb.load(binding)

    hits = await kb.invoke(binding, "search", "cache eviction", 3)
    assert hits[0].doc_id == "rb-latency"
    assert provider.calls == len(RUNBOOKS) + 1, "one embed per index, one per query"


@pytest.mark.req("REQ-035", "REQ-051")
async def test_a_provider_that_fails_at_index_time_still_indexes(kb):
    kb.provider(StubEmbeddings({}, fail=True))
    binding = kb.bind()
    await kb.load(binding)

    hits = await kb.invoke(binding, "search", "billing schema migration", 3)
    assert hits[0].doc_id == "pir-2026-08", "found lexically, with no vector at all"


@pytest.mark.req("REQ-035", "REQ-051")
async def test_a_provider_that_fails_at_query_time_falls_back_to_bm25(kb):
    provider = StubEmbeddings(
        {
            "checkout": [1.0, 0.0, 0.0],
            "latency": [0.0, 1.0, 0.0],
            "pir": [0.9, 0.1, 0.0],
        }
    )
    kb.provider(provider)
    binding = kb.bind()
    await kb.load(binding)

    provider.fail = True
    hits = await kb.invoke(binding, "search", "checkout error rate", 3)
    assert hits, "the search answered; only the ranking lost a signal"
    assert hits[0].doc_id == "rb-checkout"


@pytest.mark.req("REQ-035")
async def test_embeddings_can_be_switched_off_for_a_deterministic_ranking(kb):
    provider = StubEmbeddings({"checkout": [1.0, 0.0, 0.0]})
    kb.provider(provider)
    binding = kb.bind(use_embeddings=False)
    await kb.load(binding)
    await kb.invoke(binding, "search", "checkout", 3)
    assert provider.calls == 0, "a drill that pins ranking must not call a model"


@pytest.mark.req("REQ-035", "REQ-013")
async def test_one_tenants_knowledge_never_scores_against_anothers(api, kb):
    binding = kb.bind()
    await kb.load(binding)

    other = api.signup(org="Other Corp", email="owner@other.io")
    other_binding = connect(
        api, other["tokens"]["access_token"], "kb-index", credentials={}
    )
    hits = await api.container.invoker.invoke(
        other["org"]["id"], other_binding["id"], "search", "checkout", 5
    )
    assert hits == []
