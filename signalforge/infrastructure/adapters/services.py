"""What the composition root hands every shipped adapter runtime.

An adapter factory is `AdapterContext -> Adapter`, and `AdapterContext` carries
only the manifest, the binding and the decrypted credentials - deliberately, so
that a credential cannot end up cached on a long-lived service. Everything an
adapter needs that is *not* about one call - the clock, its own persistence, the
live notification fan-out, the call budget - arrives here instead, closed over
by the factory at wiring time.

Keeping the two apart is what stops an adapter from reaching sideways into the
platform: there is no unit of work for incidents here, no event bus and no
repository for anything an adapter has no business writing.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

from signalforge.domain.identity.ports import Clock
from signalforge.domain.integrations.ports import (
    EmbeddingProvider,
    IntegrationUnitOfWork,
    NotificationSink,
)
from signalforge.infrastructure.adapters.limits import CallBudget
from signalforge.infrastructure.observability.metrics import MetricsRegistry


@dataclass
class AdapterServices:
    """Mutable on purpose: this is the composition root's wiring bundle, not a
    value object. A deployment that gains an embedding provider or points the
    adapters at a different HTTP client sets the field; the adapters read it at
    call time, so nothing has to be re-registered."""

    uow_factory: Callable[[], IntegrationUnitOfWork]
    clock: Clock
    budget: CallBudget
    notifications: NotificationSink
    metrics: Optional[MetricsRegistry] = None
    embeddings: Optional[EmbeddingProvider] = None
    verify_tls: bool = True
    client_factory: Optional[Callable[[float], object]] = None
    """Returns an httpx.AsyncClient. Set by the reference harness and by tests so
    the adapters are exercised against a real ASGI server over real HTTP
    semantics rather than against a mocked-out method."""
