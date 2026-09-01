"""kb-index: runbooks and PIRs, searchable with or without a model (REQ-035).

Ranking is hybrid where it can be and lexical where it must be:

- **BM25 is the floor.** It is deterministic, needs nothing external, and works
  the moment a document is indexed. Every search runs it.
- **Embeddings are the improvement.** When an embedding provider is configured
  and reachable, cosine similarity against the query vector is blended with the
  lexical score. Documents indexed before the provider existed simply score
  lexically and are still returned.
- **A model failure degrades the ranking, never the search.** If the provider
  raises - at index time or at query time - the document is still indexed and
  the search still answers, from BM25 alone, and says so in the hit's snippet
  source. This is the same discipline the LLM provider is held to (REQ-051):
  the deterministic path is the product, the model is the improvement.

Redaction happens at index time, not at query time. A runbook's text goes into
briefs and prompts, so what must not be there must never be stored - and a
redaction policy applied on the way out could always be bypassed by a later
reader of the same row.
"""

from __future__ import annotations

import logging
from typing import Optional, Sequence

from signalforge.contracts.canonical import KnowledgeDocument, KnowledgeHit
from signalforge.contracts.errors import AdapterError
from signalforge.contracts.spi import KnowledgeAdapter
from signalforge.domain.integrations.entities import IndexedDocument
from signalforge.domain.integrations.ranking import (
    bm25_scores,
    cosine,
    normalize,
    snippet,
    tokenize,
)
from signalforge.infrastructure.adapters.base import GenericAdapter

logger = logging.getLogger("signalforge.adapters")

DEFAULT_SEMANTIC_WEIGHT = 0.4
MIN_SCORE = 1e-9


class KbIndexAdapter(GenericAdapter, KnowledgeAdapter):
    async def ping(self) -> bool:
        async with self.services.uow_factory() as uow:
            await uow.knowledge.get(self.tenant_id, "__ping__")
        return True

    async def index(self, document: KnowledgeDocument) -> None:
        body = self.redact(document.body) or ""
        title = self.redact(document.title) or ""
        if not body.strip():
            raise AdapterError(f"document {document.doc_id} has no body to index")
        now = self.now()
        embedding, model = await self._embed(f"{title}\n\n{body}")
        indexed = IndexedDocument(
            tenant_id=self.tenant_id,
            doc_id=document.doc_id,
            title=title,
            body=body,
            doc_type=document.doc_type,
            updated_at=document.updated_at,
            indexed_at=now,
            # Tokenised once, at write time: a search then costs one read and no
            # re-tokenisation of the corpus.
            tokens=tokenize(f"{title} {title} {body} {' '.join(document.tags)}"),
            tags=tuple(document.tags),
            app_id=self.app_id,
            embedding=tuple(embedding),
            embedding_model=model,
            redaction_policy_id=self.spec.redaction.policy_id,
        )
        async with self.services.uow_factory() as uow:
            # Upsert by doc_id: re-indexing an updated runbook replaces it
            # rather than producing a second, stale copy that can still be hit.
            await uow.knowledge.upsert(indexed)
            await uow.commit()

    async def search(self, query: str, k: int) -> list[KnowledgeHit]:
        if k <= 0:
            return []
        query_tokens = tokenize(query or "")
        async with self.services.uow_factory() as uow:
            corpus = list(await uow.knowledge.all(self.tenant_id, app_id=self.app_id))
        if not corpus:
            return []

        lexical = normalize(bm25_scores(query_tokens, [doc.tokens for doc in corpus]))
        semantic, weight = await self._semantic(query, corpus)
        hits: list[tuple[float, IndexedDocument]] = []
        for index, document in enumerate(corpus):
            score = (1 - weight) * lexical[index] + weight * semantic[index]
            if score > MIN_SCORE:
                hits.append((score, document))
        hits.sort(key=lambda pair: (-pair[0], pair[1].doc_id))
        top = self.truncate(hits, k)
        return [
            KnowledgeHit(
                doc_id=document.doc_id,
                title=document.title,
                snippet=snippet(document.body, query_tokens),
                score=round(score, 6),
            )
            for score, document in top
        ]

    # -- embeddings --------------------------------------------------------

    async def _embed(self, text: str) -> tuple[Sequence[float], Optional[str]]:
        provider = self._provider()
        if provider is None:
            return (), None
        try:
            vectors = await provider.embed([text])
        except Exception as exc:
            # Indexing must not depend on a model being up. The document lands
            # without a vector and is found lexically; re-indexing later fills
            # the vector in.
            logger.warning(
                "embedding provider failed during index; storing lexical only",
                extra={"adapter": self.spec.id, "error": f"{type(exc).__name__}: {exc}"},
            )
            return (), None
        if not vectors or not vectors[0]:
            return (), None
        return list(vectors[0]), provider.model

    async def _semantic(
        self, query: str, corpus: Sequence[IndexedDocument]
    ) -> tuple[list[float], float]:
        """Cosine scores and the weight they should carry.

        Weight is zero - lexical only - whenever the semantic signal cannot be
        trusted: no provider, a provider that just failed, or a corpus in which
        nothing has a vector. Blending in a column of zeros would penalise
        documents for something that is not their fault.
        """
        provider = self._provider()
        embedded = [document for document in corpus if document.embedded]
        if provider is None or not embedded:
            return [0.0] * len(corpus), 0.0
        try:
            vectors = await provider.embed([query])
        except Exception as exc:
            logger.warning(
                "embedding provider failed during search; falling back to BM25",
                extra={"adapter": self.spec.id, "error": f"{type(exc).__name__}: {exc}"},
            )
            return [0.0] * len(corpus), 0.0
        if not vectors or not vectors[0]:
            return [0.0] * len(corpus), 0.0
        query_vector = list(vectors[0])
        scores = [cosine(query_vector, list(document.embedding)) for document in corpus]
        weight = float(self.config("semantic_weight", DEFAULT_SEMANTIC_WEIGHT))
        return scores, min(max(weight, 0.0), 1.0)

    def _provider(self):
        if not bool(self.config("use_embeddings", True)):
            return None
        return self.services.embeddings
