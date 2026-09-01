"""Ports for the model path (CLAUDE.md rule 1).

There is exactly one of these that application code may hold - `LlmProvider` -
and the rest exist so that the provider's *own* collaborators can be swapped
without it importing infrastructure. The direction is the usual one: this file
names capabilities, `signalforge/infrastructure/llm/` implements them.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Mapping, Optional, Sequence

from signalforge.domain.llm.entities import (
    CompletionRequest,
    CompletionResult,
    DegradedNotice,
    EmbeddingResult,
    ModelClass,
    TokenUsage,
)


class LlmProvider(ABC):
    """The single abstraction application code calls.

    Every implementation owes the caller the same contract: it never raises for
    a model failure. A call that cannot reach a model returns the request's
    deterministic fallback with `degraded=True` and a reason, and the platform
    keeps running (REQ-051). Callers therefore branch on the *result*, never on
    an exception, which is what makes degraded mode testable at every call site.
    """

    @property
    @abstractmethod
    def configured(self) -> bool:
        """True when a live, approved gateway is reachable in principle - a key
        is present and the endpoint passed the approved-host check. False means
        every call answers deterministically."""

    @property
    @abstractmethod
    def models(self) -> Mapping[ModelClass, str]:
        """The configured model id per classification. Read for provenance and
        for the platform status panel; never used to pick a model at a call
        site, which names a classification instead."""

    @abstractmethod
    async def complete(self, request: CompletionRequest) -> CompletionResult: ...

    @abstractmethod
    async def embed(
        self,
        texts: Sequence[str],
        *,
        tenant_id: Optional[str] = None,
        incident_id: Optional[str] = None,
        purpose: str = "embed",
    ) -> EmbeddingResult:
        """Vectors for `texts`. Degrades to empty vectors rather than raising,
        for the same reason `complete` does - kb-index then ranks lexically."""


class ResponseCache(ABC):
    """Answers already paid for, keyed by a hash of everything that could change
    one (REQ-051: cache by input hash)."""

    @abstractmethod
    async def get(self, tenant_id: str, key: str) -> Optional[dict]: ...

    @abstractmethod
    async def put(
        self,
        tenant_id: str,
        key: str,
        *,
        model: str,
        purpose: str,
        payload: dict,
    ) -> None: ...


class TokenLedger(ABC):
    """Per-incident token accounting (REQ-051: budgets).

    Keyed by incident because that is the unit a budget means something for: an
    incident that has burned its budget must not be able to keep spending by
    routing the same work through a different agent.
    """

    @abstractmethod
    def spent(self, key: str) -> TokenUsage: ...

    @abstractmethod
    def would_exceed(self, key: str, estimate: int) -> bool: ...

    @abstractmethod
    def charge(self, key: str, usage: TokenUsage) -> None: ...


class DegradationListener(ABC):
    """Who is told when the model path degrades.

    Implemented in the application layer, where the event bus lives; the
    provider only knows that somebody wants to hear about it, so a node with no
    bus wired still degrades correctly and silently.
    """

    @abstractmethod
    async def on_degraded(self, notice: DegradedNotice) -> None: ...
