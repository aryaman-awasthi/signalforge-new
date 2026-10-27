"""Tell the people who can declare that the platform has proposed (REQ-042, REQ-045).

Detection publishes `detection.severity.proposed` and, until now, nobody
listened who could do anything about it: the brain dropped it for having no
incident id, and the cockpit socket consumer leaves `detection.*` out on
purpose because the application page polls the stance anyway. So an Incident
Commander reading the estate screen - or nothing at all - learned that the
platform believed in a P0 only by opening the right application.

This consumer delivers one in-app notification per proposal to every holder of
a role that may declare, scoped to the application or to the whole
organisation. It is addressed to a person, the way a page is, and it goes
through the same hub the pager uses, so the shell's toast stack and `/my/pages`
show it with no new transport.

What it deliberately is not: a page. No countdown, no escalation, no
acknowledgement, no delivery receipt. A proposal is the platform asking a
person to look; a page is the platform asking a person to answer, and the
difference is the whole of the human-declares rule. Paging starts when
somebody declares, and nothing here changes that.
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from signalforge.application.events.uow import EventUnitOfWork
from signalforge.contracts.canonical import NotificationChannel
from signalforge.contracts.envelope import EventEnvelope
from signalforge.contracts.roles import Role
from signalforge.domain.events.ports import EventHandler

logger = logging.getLogger("signalforge.detection.alerts")

PROPOSAL_ALERT_GROUP = "proposal-alerts"

DECLARING_ROLES: frozenset[Role] = frozenset({Role.INCIDENT_COMMANDER, Role.TECH_LEAD})
"""Who is told. The two roles that hold `declare_incident` in the frozen
matrix - the people a proposal is addressed to, because they are the only ones
who can answer it."""


class ProposalAlertConsumer(EventHandler):
    name = PROPOSAL_ALERT_GROUP

    def __init__(
        self,
        uow_factory: Callable[[], Any],
        hub,
        clock,
        metrics=None,
    ) -> None:
        self._uow_factory = uow_factory
        self._hub = hub
        self._clock = clock
        self._metrics = metrics

    async def handle(self, envelope: EventEnvelope, uow: EventUnitOfWork) -> None:
        app_id = envelope.app_id or str(envelope.payload.get("app_id") or "")
        if not app_id:
            return
        recipients = await self._recipients(envelope.tenant_id, app_id)
        if not recipients:
            logger.info(
                "severity proposed and nobody holds a declaring role",
                extra={"tenant_id": envelope.tenant_id, "app_id": app_id},
            )
            return
        frame = self._frame(envelope, app_id)
        delivered = 0
        for user_id in recipients:
            delivered += self._hub.deliver(
                envelope.tenant_id, user_id, NotificationChannel.IN_APP, dict(frame)
            )
        if self._metrics is not None:
            self._metrics.increment(
                "signalforge_proposal_alerts_total",
                {"delivered": "yes" if delivered else "no"},
            )

    async def _recipients(self, tenant_id: str, app_id: str) -> tuple[str, ...]:
        """Everyone holding a declaring role for this application or for the
        organisation. A binding with no app_id means every application, which
        is what it means everywhere else in this platform."""
        async with self._uow_factory() as uow:
            bindings = await uow.bindings.list_for_tenant(tenant_id)
        return tuple(
            dict.fromkeys(
                binding.user_id
                for binding in bindings
                if binding.role in DECLARING_ROLES
                and binding.app_id in (None, "", app_id)
            )
        )

    def _frame(self, envelope: EventEnvelope, app_id: str) -> dict:
        """The same shape a channel adapter emits, so the cockpit renders it
        with the code it already has. `incident_id` is null on purpose: there
        is no incident until a person declares one."""
        payload = envelope.payload
        severity = str(payload.get("severity") or "").upper() or "a severity"
        impact = dict(payload.get("impact") or {})
        families = [str(item) for item in (payload.get("families") or [])]
        journeys = [str(item) for item in (impact.get("affected_journeys") or [])]
        regions = [str(item) for item in (impact.get("affected_regions") or [])]
        rate = impact.get("success_rate_pct")
        parts = []
        if families:
            parts.append(f"{', '.join(families)} corroborate it")
        if journeys:
            parts.append(f"journeys affected: {', '.join(journeys)}")
        if regions:
            parts.append(f"regions: {', '.join(regions)}")
        if isinstance(rate, (int, float)):
            parts.append(f"success rate {float(rate):.0f}%")
        body = (
            "; ".join(parts) + ". " if parts else ""
        ) + "Proposed, not declared - only a person declares. Open the application to read the proposal."
        return {
            "type": "notification",
            "replay": False,
            "notification_id": f"proposal:{app_id}:{envelope.seq or envelope.event_id}",
            "adapter_id": "signalforge-detection",
            "channel": NotificationChannel.IN_APP.value,
            "title": f"SignalForge proposes {severity} on {app_id}",
            "body": body,
            "incident_id": None,
            "app_id": app_id,
            "requires_ack": False,
            "simulated": False,
            "status": "sent",
            "at": self._clock.now().isoformat(),
        }
