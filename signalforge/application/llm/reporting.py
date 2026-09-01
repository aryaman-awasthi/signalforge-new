"""`system.llm.degraded`: the platform saying out loud that it is thinking
without a model (REQ-051).

The provider knows *that* a call degraded; only the application layer can say
so on the bus, because that is where the event unit of work lives. Keeping the
two apart is what lets a node with no bus - a script, a test, a future worker
split - still degrade correctly and silently.

Two decisions worth stating:

- **One event per episode, not per call.** A gateway outage during an incident
  can degrade a hundred calls a minute. Publishing each one would bury the
  timeline the incident commander is reading in the noise of the thing that
  broke. So a `(tenant, incident, model class, reason)` that is already known to
  be degraded is re-announced only after `cooldown_s`, which keeps a long outage
  visible without turning it into a flood.
- **Announcing can never fail a call.** Everything here is inside the provider's
  own try/except; a bus that cannot be written to costs the timeline an entry,
  not the commander an answer.

The event carries the reason, the model class, the model id and the endpoint
description - never the prompt, the answer or the credential (`DegradedNotice`
is built by the provider from `LlmSettings.redacted` material).
"""

from __future__ import annotations

import logging
from typing import Callable, Optional

from signalforge.application.events.bus import EventBus
from signalforge.application.events.uow import EventUnitOfWork
from signalforge.contracts.envelope import (
    Actor,
    ActorType,
    Authority,
    EventEnvelope,
    Plane,
    Source,
    Topics,
)
from signalforge.domain.identity.ports import Clock
from signalforge.domain.llm.entities import DegradedNotice
from signalforge.domain.llm.ports import DegradationListener
from signalforge.domain.observability.health import PLATFORM_TENANT_ID

logger = logging.getLogger("signalforge.llm")

DEFAULT_COOLDOWN_S = 60.0

LLM_ACTOR_ID = "llm.provider"


class LlmDegradationReporter(DegradationListener):
    def __init__(
        self,
        uow_factory: Callable[[], EventUnitOfWork],
        bus: EventBus,
        clock: Clock,
        *,
        cooldown_s: float = DEFAULT_COOLDOWN_S,
        metrics: Optional[object] = None,
    ):
        self._uow_factory = uow_factory
        self._bus = bus
        self._clock = clock
        self._cooldown_s = max(0.0, cooldown_s)
        self._metrics = metrics
        self._announced: dict[tuple, float] = {}

    async def on_degraded(self, notice: DegradedNotice) -> None:
        if self._metrics is not None:
            self._metrics.increment(
                "signalforge_llm_degraded_total", {"reason": notice.reason.value}
            )
        if not self._should_announce(notice):
            return
        envelope = self.envelope(notice)
        async with self._uow_factory() as uow:
            await self._bus.publish(uow, envelope)
            await uow.commit()
        logger.warning(
            "published system.llm.degraded",
            extra={"reason": notice.reason.value, "purpose": notice.purpose},
        )

    def envelope(self, notice: DegradedNotice) -> EventEnvelope:
        tenant_id = notice.tenant_id or PLATFORM_TENANT_ID
        return EventEnvelope(
            topic=Topics.LLM_DEGRADED,
            tenant_id=tenant_id,
            incident_id=notice.incident_id,
            # A degradation inside an incident is part of that incident's
            # record and is kept and sealed with it; one outside it is signal
            # and ages out with the rest.
            plane=Plane.INCIDENT if notice.incident_id else Plane.SIGNAL,
            occurred_at=self._clock.now(),
            actor=Actor(type=ActorType.SYSTEM, id=LLM_ACTOR_ID, display="LLM provider"),
            source=Source(
                system=LLM_ACTOR_ID,
                authority=Authority.OBSERVED,
            ),
            payload={
                "reason": notice.reason.value,
                "purpose": notice.purpose,
                "model_class": notice.model_class.value,
                "model": notice.model,
                "provider": notice.provider,
                "detail": notice.detail,
                "deterministic_fallback": True,
            },
        )

    def _should_announce(self, notice: DegradedNotice) -> bool:
        key = (
            notice.tenant_id or PLATFORM_TENANT_ID,
            notice.incident_id or "",
            notice.model_class.value,
            notice.reason.value,
        )
        now = self._clock.now().timestamp()
        last = self._announced.get(key)
        if last is not None and now - last < self._cooldown_s:
            return False
        self._announced[key] = now
        return True

    def forget(self, tenant_id: Optional[str] = None) -> None:
        """Drop the suppression window - used when a run ends, so the next
        outage announces immediately rather than inheriting an old cooldown."""
        if tenant_id is None:
            self._announced.clear()
            return
        for key in [k for k in self._announced if k[0] == tenant_id]:
            self._announced.pop(key, None)
