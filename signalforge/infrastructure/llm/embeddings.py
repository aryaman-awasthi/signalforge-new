"""kb-index's embedding provider, served by the gateway (REQ-035, REQ-051).

This exists so that there is exactly one place model traffic leaves from. It
would have been shorter to give the knowledge adapter its own embeddings client,
and that is precisely the shape in which a codebase ends up talking to the
gateway for chat and to somewhere else for vectors. Instead the adapter keeps
its existing port and this thin object satisfies it from the same provider, with
the same endpoint check, cache, budget and degraded event.

The port's contract is "raise on failure, the caller falls back to BM25", while
the provider's is "never raise, return `degraded`". Translating between the two
is this class's entire job.
"""

from __future__ import annotations

from typing import Optional, Sequence

from signalforge.contracts.errors import AdapterError
from signalforge.domain.integrations.ports import EmbeddingProvider
from signalforge.domain.llm.entities import ModelClass
from signalforge.domain.llm.ports import LlmProvider


class GatewayEmbeddingProvider(EmbeddingProvider):
    def __init__(self, provider: LlmProvider, *, tenant_id: Optional[str] = None):
        self._provider = provider
        self._tenant_id = tenant_id

    @property
    def model(self) -> str:
        return self._provider.models[ModelClass.EMBEDDINGS]

    def for_tenant(self, tenant_id: str) -> "GatewayEmbeddingProvider":
        """A view onto the same provider that knows whose call it is serving, so
        the cache row is written under the calling tenant (REQ-013)."""
        if tenant_id == self._tenant_id:
            return self
        return GatewayEmbeddingProvider(self._provider, tenant_id=tenant_id)

    async def embed(self, texts: Sequence[str]) -> Sequence[Sequence[float]]:
        result = await self._provider.embed(
            list(texts), tenant_id=self._tenant_id, purpose="kb-index"
        )
        if result.degraded:
            # kb-index catches this, indexes or searches lexically and says so
            # in the hit's source. The reason is already on the timeline as
            # system.llm.degraded; this exception only steers the ranking.
            raise AdapterError(
                f"embeddings unavailable ({result.reason.value if result.reason else 'unknown'})"
            )
        return [list(vector) for vector in result.vectors]
