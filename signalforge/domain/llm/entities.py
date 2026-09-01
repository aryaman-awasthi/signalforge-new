"""The value objects a model call is made of (REQ-051).

Two rules shape this module and both are ground rules rather than taste:

1. **No model id appears here.** A call names a *classification* - general,
   reasoning, embeddings - and configuration decides which model id serves it.
   That is what makes the model swappable without touching a line of domain or
   application code (CLAUDE.md, Environment).
2. **The deterministic answer travels with the request.** A caller that asks for
   a model's help states, in the same breath, what the product should say if
   there is no model. Degradation is therefore a property of every call site by
   construction, not a fallback somebody remembered to write.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class ModelClass(str, Enum):
    """What kind of thinking a call needs, not which model does it.

    GENERAL is the fast path: classification, extraction, short rewrites.
    REASONING is for multi-step judgement, which costs more and is worth it
    less often than it feels like it is. EMBEDDINGS is the vector path.
    """

    GENERAL = "general"
    REASONING = "reasoning"
    EMBEDDINGS = "embeddings"


class DegradedReason(str, Enum):
    """Why a call fell back to the deterministic answer.

    A closed vocabulary because it is a metric label and an event field: an
    open-ended string here would let a failing gateway mint metric series, and
    would put a vendor's error text into an audited event.
    """

    UNCONFIGURED = "unconfigured"        # no key, or no approved endpoint
    TIMEOUT = "timeout"
    TRANSPORT = "transport"              # connection refused, TLS, DNS
    UPSTREAM_ERROR = "upstream_error"    # the gateway answered 4xx/5xx
    INVALID_RESPONSE = "invalid_response"  # unparseable, or off-schema
    BUDGET_EXHAUSTED = "budget_exhausted"


@dataclass(frozen=True)
class Message:
    role: str
    content: str

    def __post_init__(self) -> None:
        if self.role not in {"system", "user", "assistant"}:
            raise ValueError(f"unsupported message role {self.role!r}")


@dataclass(frozen=True)
class TokenUsage:
    prompt: int = 0
    completion: int = 0

    @property
    def total(self) -> int:
        return self.prompt + self.completion

    def __add__(self, other: "TokenUsage") -> "TokenUsage":
        return TokenUsage(self.prompt + other.prompt, self.completion + other.completion)


@dataclass(frozen=True)
class CompletionRequest:
    """One call to a language model.

    `purpose` names the call site ("join-brief", "statement-classification").
    It is provenance, part of the cache key and a metric label, so it is a short
    stable slug rather than free text.

    `fallback` is the deterministic answer. `schema` makes the call structured:
    the provider asks the gateway for JSON matching it and refuses an answer
    that does not, because a half-parsed object reaching the incident graph is
    worse than no answer at all.
    """

    purpose: str
    messages: tuple[Message, ...]
    model_class: ModelClass = ModelClass.GENERAL
    schema: Optional[dict[str, Any]] = None
    fallback: Optional[Any] = None
    max_tokens: int = 800
    temperature: float = 0.0
    tenant_id: Optional[str] = None
    incident_id: Optional[str] = None
    prompt_version: str = "1"
    cacheable: bool = True

    def __post_init__(self) -> None:
        if not self.purpose:
            raise ValueError("a completion must name its purpose")
        if not self.messages:
            raise ValueError("a completion needs at least one message")
        if self.model_class is ModelClass.EMBEDDINGS:
            raise ValueError("embeddings are requested through embed(), not complete()")


@dataclass(frozen=True)
class CompletionResult:
    """An answer plus everything needed to say where it came from.

    `degraded` and `cached` are separate flags on purpose: a cached answer is a
    real model answer served again, while a degraded one never involved a model.
    A cockpit that conflated them would show DEGRADED for a warm cache.
    """

    purpose: str
    text: str
    model_class: ModelClass
    model: str
    provider: str
    cached: bool = False
    degraded: bool = False
    reason: Optional[DegradedReason] = None
    usage: TokenUsage = field(default_factory=TokenUsage)
    data: Optional[Any] = None

    @property
    def provenance(self) -> dict[str, Any]:
        """What a graph node, an event or an audit row records about this answer.

        Deliberately excludes the prompt and the answer: this is the "who said
        it" half, and it must be safe to attach to anything.
        """
        return {
            "purpose": self.purpose,
            "provider": self.provider,
            "model": self.model,
            "model_class": self.model_class.value,
            "cached": self.cached,
            "degraded": self.degraded,
            "reason": self.reason.value if self.reason else None,
            "tokens": self.usage.total,
        }


@dataclass(frozen=True)
class EmbeddingResult:
    vectors: tuple[tuple[float, ...], ...]
    model: str
    provider: str
    cached: bool = False
    degraded: bool = False
    reason: Optional[DegradedReason] = None
    usage: TokenUsage = field(default_factory=TokenUsage)


@dataclass(frozen=True)
class DegradedNotice:
    """What the platform announces when the model path stops working.

    `detail` is written by infrastructure and is required to be free of both
    credentials and prompt content: this becomes `system.llm.degraded`, which
    lands in the audit chain and on the cockpit.
    """

    reason: DegradedReason
    purpose: str
    model_class: ModelClass
    model: str
    provider: str
    detail: str = ""
    tenant_id: Optional[str] = None
    incident_id: Optional[str] = None
