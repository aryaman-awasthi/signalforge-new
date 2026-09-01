"""Paging, acknowledgement and escalation (REQ-045).

Three things happen here and each has a rule that shapes the code around it.

**Paging must not be able to fail an incident.** `page_room` is called after the
war room commits and swallows every failure the same way the ITSM sync does. A
notification adapter that is down means somebody was not reached, which is
recorded, visible on the board and escalated from - not an exception that
unwinds a declaration.

**A page is sent once, however many times the code runs.** Notification ids are
*derived* from the page id and the adapter, so re-running the sweep, restarting
the process or two workers racing all hand the same id to `NotifyAdapter.send`,
which returns the first receipt without transmitting again. A responder paged
twice for one thing stops trusting the pager, and that is the failure that
matters most here.

**Escalation is arithmetic, not a timer.** The sweep asks the repository for
pages whose stored deadline has passed. Nothing is held in memory, nothing is
lost on restart, and a drill on a virtual clock at 100x escalates at 100x with
no drill-aware code anywhere in this file.
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Optional, Sequence

from signalforge.application.adapters.invoker import ConnectorInvoker
from signalforge.application.events.bus import EventBus
from signalforge.application.identity.principal import Principal
from signalforge.application.identity.uow import IdentityUnitOfWork
from signalforge.application.warroom.common import (
    PAGE_ACKED_TOPIC,
    PAGE_ESCALATED_TOPIC,
    PAGE_EXHAUSTED_TOPIC,
    PAGE_FAILED_TOPIC,
    PAGE_SENT_TOPIC,
    publish_system,
    record,
)
from signalforge.contracts.canonical import (
    DeliveryStatus,
    Notification,
    NotificationChannel,
)
from signalforge.contracts.errors import AdapterError, NotSupportedError
from signalforge.contracts.envelope import AdapterKind
from signalforge.contracts.roles import Role
from signalforge.domain.adapters.bindings import ConnectorBinding
from signalforge.domain.adapters.ports import AdapterRegistry
from signalforge.domain.identity.oncall import EscalationPolicy, OnCallRotation
from signalforge.domain.identity.ports import Clock
from signalforge.domain.incidents.entities import Incident
from signalforge.domain.shared.errors import DomainError
from signalforge.domain.warroom.entities import (
    MemberSource,
    RoomMember,
    WarRoom,
)
from signalforge.domain.warroom.errors import (
    EscalationExhaustedError,
    PageAlreadySettledError,
    PageNotFoundError,
)
from signalforge.domain.warroom.paging import (
    DEFAULT_ACK_TIMEOUT_S,
    Page,
    PageDelivery,
    PageState,
    channels_for,
    first_timeout_s,
    next_target,
)

logger = logging.getLogger("signalforge.warroom")

SEND = "send"
MAX_SWEEP = 100


@dataclass(frozen=True)
class PagingSweep:
    considered: int = 0
    escalated: int = 0
    exhausted: int = 0
    acked_elsewhere: int = 0
    """Pages the sweep found already answered - over the socket, on a phone -
    and closed rather than escalated. Counted separately because it is the
    reconciliation working, not the escalation failing."""

    def __str__(self) -> str:
        return (
            f"{self.considered} overdue page(s): {self.escalated} escalated, "
            f"{self.exhausted} with nobody left to page, {self.acked_elsewhere} "
            "answered on another channel"
        )


class PagingService:
    def __init__(
        self,
        uow_factory: Callable[[], IdentityUnitOfWork],
        bus: EventBus,
        clock: Clock,
        registry: AdapterRegistry,
        invoker: ConnectorInvoker,
        rooms=None,
        metrics=None,
    ):
        self._uow_factory = uow_factory
        self._bus = bus
        self._clock = clock
        self._registry = registry
        self._invoker = invoker
        self._rooms = rooms
        self._metrics = metrics

    # -- paging the room -----------------------------------------------------

    async def page_room(
        self, room: WarRoom, incident: Incident, members: Sequence[RoomMember]
    ) -> tuple[Page, ...]:
        """Page everybody the blast radius said should be paged. Never raises.

        Wired as a hook on `WarRoomService.on_opened` rather than called from it,
        so the dependency points one way: the room knows who should be there, and
        paging knows how to reach them.
        """
        from signalforge.domain.warroom.blast import required_roles

        paged_roles = {
            requirement.role
            for requirement in required_roles(room.radius)
            if requirement.paged
        }
        policy, rotation = await self._escalation_for(room.tenant_id, room.app_id)
        timeout = first_timeout_s(policy)

        pages: list[Page] = []
        for member in members:
            if member.role is None or member.role not in paged_roles:
                # The declarer and any executive are in the room without being
                # woken. Being present and being paged are different things.
                continue
            try:
                pages.append(
                    await self.page(
                        tenant_id=room.tenant_id,
                        incident=incident,
                        recipient=member.user_id,
                        role=member.role,
                        reason=member.reason,
                        recipient_display=member.display_name,
                        timeout_s=timeout,
                        rotation_id=rotation.id if rotation else None,
                        policy_id=policy.id if policy else None,
                        requested_by=incident.declared_by,
                        requested_by_type="human",
                    )
                )
            except Exception as exc:  # pragma: no cover - defensive by design
                logger.warning(
                    "a page could not be raised; the room stands",
                    extra={
                        "incident_id": incident.id,
                        "recipient": member.user_id,
                        "error": f"{type(exc).__name__}: {exc}",
                    },
                )
        return tuple(pages)

    async def page(
        self,
        *,
        tenant_id: str,
        incident: Incident,
        recipient: str,
        role: Optional[Role],
        reason: str,
        recipient_display: str = "",
        timeout_s: Optional[int] = None,
        rotation_id: Optional[str] = None,
        policy_id: Optional[str] = None,
        policy_step: int = 0,
        hop: int = 0,
        escalated_from: Optional[str] = None,
        requested_by: str = "",
        requested_by_type: str = "system",
    ) -> Page:
        """Raise one page and send it. The row exists before anything transmits.

        That ordering is the same promise the S3 delivery receipt makes, one
        level up: a page whose transmission succeeded and whose row did not would
        be a countdown nobody is running, and an escalation built on the
        transport rather than on the record would silence itself on a lost frame.
        """
        now = self._clock.now()
        page = Page(
            id=str(uuid.uuid4()),
            tenant_id=tenant_id,
            incident_id=incident.id,
            app_id=incident.app_id,
            recipient=recipient,
            recipient_display=recipient_display,
            role=role,
            reason=reason,
            severity=incident.severity,
            state=PageState.PENDING,
            created_at=now,
            ack_timeout_s=int(timeout_s) if timeout_s else DEFAULT_ACK_TIMEOUT_S,
            rotation_id=rotation_id,
            policy_id=policy_id,
            policy_step=policy_step,
            hop=hop,
            escalated_from_page_id=escalated_from,
            requested_by=requested_by,
            requested_by_type=requested_by_type,
        )
        async with self._uow_factory() as uow:
            await uow.incident_pages.add(page)
            await uow.commit()

        deliveries = await self._transmit(page, incident)
        sent = (
            page.sent(at=self._clock.now(), deliveries=deliveries)
            if deliveries
            else page.failed(
                at=self._clock.now(),
                detail=(
                    "no notification connector bound to this application could "
                    "carry a page; nobody was reached"
                ),
            )
        )
        async with self._uow_factory() as uow:
            await uow.incident_pages.update(sent)
            await publish_system(
                uow,
                self._bus,
                self._clock,
                tenant_id=tenant_id,
                app_id=incident.app_id,
                incident_id=incident.id,
                topic=PAGE_SENT_TOPIC if deliveries else PAGE_FAILED_TOPIC,
                payload=_page_payload(sent, incident),
            )
            await uow.commit()
        self._count(sent)
        if self._rooms is not None:
            await self._rooms.add_member(
                tenant_id,
                incident.id,
                recipient,
                role=role,
                reason=reason,
                source=(
                    MemberSource.ESCALATION if hop else MemberSource.BLAST_RADIUS
                ),
                display_name=recipient_display,
                paged=True,
            )
        return sent

    async def _transmit(
        self, page: Page, incident: Incident
    ) -> tuple[PageDelivery, ...]:
        """Hand the page to every bound channel the severity calls for.

        A channel an adapter does not carry is not an error - `NotSupportedError`
        means "this connector is not a pager", and skipping it silently is
        correct. A channel that is bound and *fails* is recorded on the page, so
        the board can show that the SMS bounced while the in-app landed.
        """
        bindings = await self._notification_bindings(page.tenant_id, page.app_id)
        if not bindings:
            return ()
        wanted = channels_for(page.severity)
        title, body = _page_text(page, incident)

        deliveries: list[PageDelivery] = []
        for binding in bindings:
            for channel in wanted:
                notification_id = _notification_id(page.id, binding.id, channel)
                try:
                    receipt = await self._invoker.invoke(
                        page.tenant_id,
                        binding.id,
                        SEND,
                        Notification(
                            notification_id=notification_id,
                            channel=channel,
                            recipient=page.recipient,
                            title=title,
                            body=body,
                            incident_id=page.incident_id,
                            requires_ack=True,
                        ),
                    )
                except NotSupportedError:
                    # This connector does not carry this channel. Expected, and
                    # the reason the loop is a loop.
                    continue
                except (AdapterError, DomainError) as exc:
                    deliveries.append(
                        PageDelivery(
                            notification_id=notification_id,
                            adapter_id=binding.adapter_id,
                            channel=channel,
                            status=DeliveryStatus.FAILED.value,
                            detail=f"{type(exc).__name__}: {exc}"[:300],
                        )
                    )
                    continue
                deliveries.append(
                    PageDelivery(
                        notification_id=notification_id,
                        adapter_id=binding.adapter_id,
                        channel=channel,
                        status=receipt.status.value,
                        detail=(receipt.detail or "")[:300],
                    )
                )
        return tuple(deliveries)

    # -- acknowledgement -----------------------------------------------------

    async def acknowledge(
        self, principal: Principal, page_id: str, *, note: str = ""
    ) -> Page:
        """The human override, and the one act a role cannot confer.

        The route behind this carries no permission, so the authorization *is*
        the entity's own check that the page names the caller. Idempotent and
        terminal: a second tap returns the same page and writes no second event.
        """
        tenant_id = principal.tenant_id
        async with self._uow_factory() as uow:
            page = await uow.incident_pages.get(tenant_id, page_id)
        if page is None:
            raise PageNotFoundError(f"page {page_id} not found")
        if page.state is PageState.ACKED:
            # Checked before the entity so an already-acknowledged page is not
            # a second event, and so acknowledging twice cannot be told from
            # acknowledging once by anything a client can see.
            page.acknowledged(at=self._clock.now(), by=principal.user_id, note=note)
            return page

        now = self._clock.now()
        acked = page.acknowledged(at=now, by=principal.user_id, note=note)
        async with self._uow_factory() as uow:
            incident = await uow.incidents.get(tenant_id, page.incident_id)
            await uow.incident_pages.update(acked)
            await record(
                uow,
                self._bus,
                self._clock,
                principal=principal,
                app_id=page.app_id,
                incident_id=page.incident_id,
                topic=PAGE_ACKED_TOPIC,
                payload={
                    **_page_payload(acked, incident),
                    "answered_in_s": (
                        (now - page.paged_at).total_seconds() if page.paged_at else None
                    ),
                    "note": note,
                },
            )
            await uow.commit()
        self._count(acked)
        if self._rooms is not None:
            # Answering a page puts you in the room. It is the whole point of
            # answering one.
            await self._rooms.mark_present(
                tenant_id,
                page.incident_id,
                principal.user_id,
                display_name=principal.display_name or "",
            )
        return acked

    async def list_for_recipient(
        self, principal: Principal, *, open_only: bool = True, limit: int = 50
    ) -> Sequence[Page]:
        """What is on this person's phone. Self-scoped by construction: the
        recipient is the session, never a parameter."""
        async with self._uow_factory() as uow:
            return list(
                await uow.incident_pages.list_for_recipient(
                    principal.tenant_id,
                    principal.user_id,
                    open_only=open_only,
                    limit=limit,
                )
            )

    # -- escalation ----------------------------------------------------------

    async def sweep(self, limit: int = MAX_SWEEP) -> PagingSweep:
        """Escalate every page whose countdown ran out. One job per install.

        Reconciles before it escalates. A page may have been answered over the
        /ws socket rather than through the page route - the S3 notification ack
        is a real acknowledgement by a real person - and escalating past that
        would wake a second person for work that already has an owner.
        """
        now = self._clock.now()
        async with self._uow_factory() as uow:
            overdue = list(await uow.incident_pages.overdue_across_tenants(now, limit))
        escalated = exhausted = elsewhere = 0
        for page in overdue:
            settled = await self._settle_if_answered(page)
            if settled is not None:
                elsewhere += 1
                continue
            try:
                await self.escalate(page, by=None)
                escalated += 1
            except EscalationExhaustedError:
                exhausted += 1
            except PageAlreadySettledError:
                # Somebody answered between the work list and here. Correct, and
                # not an error: the ack won the race, which is the outcome the
                # race is supposed to have.
                elsewhere += 1
            except Exception as exc:  # pragma: no cover - defensive by design
                logger.warning(
                    "a page could not be escalated",
                    extra={
                        "page_id": page.id,
                        "error": f"{type(exc).__name__}: {exc}",
                    },
                )
        return PagingSweep(
            considered=len(overdue),
            escalated=escalated,
            exhausted=exhausted,
            acked_elsewhere=elsewhere,
        )

    async def _settle_if_answered(self, page: Page) -> Optional[Page]:
        """Did a delivery for this page get acknowledged somewhere else?

        The S3 delivery receipt is the record of whether a person answered, and
        an acknowledgement over the socket is as real as one through this API.
        Reading it back here is reconcile-before-act: the same shape as the ITSM
        sync asking the queue what it holds before creating a second ticket.
        """
        if not page.deliveries:
            return None
        async with self._uow_factory() as uow:
            for link in page.deliveries:
                delivery = await uow.notifications.get(
                    page.tenant_id, link.notification_id
                )
                if delivery is None or delivery.status is not DeliveryStatus.ACKED:
                    continue
                acked = page.acknowledged(
                    at=delivery.acked_at or self._clock.now(),
                    by=delivery.acked_by or page.recipient,
                    note=f"acknowledged on the {link.channel.value} channel",
                )
                await uow.incident_pages.update(acked)
                await uow.commit()
                return acked
        return None

    async def escalate(
        self, page: Page, *, by: Optional[Principal], reason: str = ""
    ) -> Page:
        """Page the next person, and record that this one did not answer.

        Raises `EscalationExhaustedError` when the chain runs out. That is a
        finding, not a failure: somebody has to be told that nobody answered,
        and it is published on the timeline before it is raised.
        """
        if page.state.settled:
            raise PageAlreadySettledError(
                f"page {page.id} is {page.state.value}; there is nothing to escalate"
            )
        tenant_id = page.tenant_id
        async with self._uow_factory() as uow:
            incident = await uow.incidents.get(tenant_id, page.incident_id)
            chain = list(await uow.incident_pages.chain(tenant_id, page.id))
        if incident is None:
            raise PageNotFoundError(f"page {page.id} names an incident that is gone")

        policy, rotation = await self._escalation_for(tenant_id, page.app_id)
        candidates = await self._role_candidates(tenant_id, page)
        try:
            target = next_target(
                page,
                rotation=rotation,
                policy=policy,
                already_paged=[link.recipient for link in chain],
                candidates=candidates,
            )
        except EscalationExhaustedError as exc:
            await self._publish_exhausted(page, incident, str(exc))
            raise

        successor = await self.page(
            tenant_id=tenant_id,
            incident=incident,
            recipient=target.user_id,
            role=page.role,
            reason=target.reason,
            timeout_s=target.timeout_s,
            rotation_id=target.rotation_id or page.rotation_id,
            policy_id=target.policy_id or page.policy_id,
            policy_step=target.policy_step,
            hop=page.hop + 1,
            escalated_from=page.id,
            requested_by=by.user_id if by else "",
            requested_by_type="human" if by else "system",
        )

        now = self._clock.now()
        moved = page.escalated(at=now, to_page_id=successor.id)
        async with self._uow_factory() as uow:
            await uow.incident_pages.update(moved)
            payload = {
                **_page_payload(moved, incident),
                "escalated_to": target.user_id,
                "escalated_to_page_id": successor.id,
                "escalation_reason": reason or target.reason,
                "hop": successor.hop,
                "unanswered_for_s": (
                    (now - page.paged_at).total_seconds() if page.paged_at else None
                ),
                "by_human": by is not None,
            }
            if by is not None:
                await record(
                    uow,
                    self._bus,
                    self._clock,
                    principal=by,
                    app_id=page.app_id,
                    incident_id=page.incident_id,
                    topic=PAGE_ESCALATED_TOPIC,
                    payload=payload,
                )
            else:
                await publish_system(
                    uow,
                    self._bus,
                    self._clock,
                    tenant_id=tenant_id,
                    app_id=page.app_id,
                    incident_id=page.incident_id,
                    topic=PAGE_ESCALATED_TOPIC,
                    payload=payload,
                )
            await uow.commit()
        self._count(moved)
        return successor

    async def escalate_now(self, principal: Principal, page_id: str) -> Page:
        """A commander deciding not to wait for the countdown."""
        async with self._uow_factory() as uow:
            page = await uow.incident_pages.get(principal.tenant_id, page_id)
        if page is None:
            raise PageNotFoundError(f"page {page_id} not found")
        return await self.escalate(
            page, by=principal, reason="escalated by hand before the countdown ended"
        )

    async def cancel_open_pages(self, incident: Incident) -> int:
        """The incident closed. Stop ringing phones for it.

        Acknowledged pages are left alone - the transition table refuses to move
        them - because what somebody took responsibility for is not un-taken by
        the incident ending.
        """
        cancelled = 0
        now = self._clock.now()
        async with self._uow_factory() as uow:
            pages = list(await uow.incident_pages.list(incident.tenant_id, incident.id))
            for page in pages:
                if not page.state.awaiting:
                    continue
                await uow.incident_pages.update(page.cancelled(at=now))
                cancelled += 1
            if cancelled:
                await uow.commit()
        return cancelled

    async def _publish_exhausted(
        self, page: Page, incident: Incident, detail: str
    ) -> None:
        async with self._uow_factory() as uow:
            await publish_system(
                uow,
                self._bus,
                self._clock,
                tenant_id=page.tenant_id,
                app_id=page.app_id,
                incident_id=page.incident_id,
                topic=PAGE_EXHAUSTED_TOPIC,
                payload={
                    **_page_payload(page, incident),
                    "detail": detail,
                    "needs_human": True,
                },
            )
            await uow.commit()
        if self._metrics is not None:
            self._metrics.increment(
                "signalforge_page_escalations_total", {"outcome": "exhausted"}
            )

    # -- wiring --------------------------------------------------------------

    async def _escalation_for(
        self, tenant_id: str, app_id: str
    ) -> tuple[Optional[EscalationPolicy], Optional[OnCallRotation]]:
        """The policy and rotation covering this application.

        An application-scoped one wins over a tenant-wide one, which is how every
        other per-application setting in this platform resolves; a tenant-wide
        one is used when there is no specific one, because "we have one rotation"
        is the common case and making every application configure its own would
        mean most applications escalate to nobody.
        """
        async with self._uow_factory() as uow:
            policies = list(await uow.escalations.list(tenant_id))
            rotations = list(await uow.oncall.list(tenant_id))
        policy = _most_specific(policies, app_id)
        rotation = _most_specific(rotations, app_id)
        return policy, rotation

    async def _role_candidates(self, tenant_id: str, page: Page) -> list[str]:
        """Anybody else holding the paged person's role for this application.

        The last resort in `next_target`, so that an org with a bound tech lead
        and no configured rotation still escalates to somebody rather than
        stopping at the first unanswered page.
        """
        if page.role is None:
            return []
        async with self._uow_factory() as uow:
            bindings = await uow.bindings.list_for_tenant(tenant_id)
        return sorted(
            {
                binding.user_id
                for binding in bindings
                if binding.role is page.role
                and binding.app_id in (None, "", page.app_id)
                and binding.user_id != page.recipient
            }
        )

    async def _notification_bindings(
        self, tenant_id: str, app_id: str
    ) -> list[ConnectorBinding]:
        async with self._uow_factory() as uow:
            bindings = await uow.connectors.list(
                tenant_id, app_id=app_id, enabled_only=True
            )
        chosen: list[ConnectorBinding] = []
        for binding in bindings:
            manifest = self._registry.find(binding.adapter_id)
            if (
                manifest is not None
                and manifest.kind is AdapterKind.NOTIFICATIONS
                and manifest.supports(SEND)
            ):
                chosen.append(binding)
        return chosen

    def _count(self, page: Page) -> None:
        if self._metrics is None:
            return
        self._metrics.increment(
            "signalforge_pages_total",
            {"state": page.state.value, "severity": page.severity.value},
        )
        if page.state is PageState.ESCALATED:
            self._metrics.increment(
                "signalforge_page_escalations_total", {"outcome": "escalated"}
            )

    async def refresh_gauges(self) -> None:
        """Install-wide counts for the unauthenticated scrape. Counts only."""
        if self._metrics is None:
            return
        now = self._clock.now()
        async with self._uow_factory() as uow:
            overdue = await uow.incident_pages.overdue_across_tenants(now, MAX_SWEEP)
        self._metrics.set_gauge("signalforge_pages_overdue", float(len(overdue)))


# --- pure helpers -------------------------------------------------------------


def _most_specific(items: Sequence, app_id: str):
    """An application-scoped item, else a tenant-wide one, else nothing."""
    for item in items:
        if getattr(item, "app_id", None) == app_id:
            return item
    for item in items:
        if not getattr(item, "app_id", None):
            return item
    return None


def _notification_id(page_id: str, binding_id: str, channel: NotificationChannel) -> str:
    """Derived, never random.

    This is the whole duplicate-page guard: the same page handed to the same
    connector on the same channel produces the same id however many times the
    code runs, and `NotifyAdapter.send` returns the first receipt without
    transmitting again.
    """
    digest = hashlib.sha256(
        f"{page_id}|{binding_id}|{channel.value}".encode("utf-8")
    ).hexdigest()
    return f"pg-{digest[:24]}"


def _page_text(page: Page, incident: Optional[Incident]) -> tuple[str, str]:
    """What the responder actually reads, on a screen or through a phone.

    Severity, application and reference first, because on a locked phone that is
    all that is visible - and "P0 payments INC-000012" is enough to decide
    whether to get up.
    """
    reference = incident.reference if incident else page.incident_id[:8]
    title = f"{page.severity.value} {page.app_id} {reference}"
    lines = [incident.title if incident else "An incident needs you."]
    if page.reason:
        lines.append(f"You: {page.reason}.")
    if page.hop:
        lines.append(f"Escalated to you (hop {page.hop}) - the previous page went unanswered.")
    lines.append(f"Acknowledge within {page.ack_timeout_s // 60} minutes or it escalates.")
    return title, " ".join(lines)


def _page_payload(page: Page, incident: Optional[Incident]) -> dict:
    return {
        "page_id": page.id,
        "incident_id": page.incident_id,
        "reference": incident.reference if incident else None,
        "app_id": page.app_id,
        "recipient": page.recipient,
        "role": page.role.value if page.role else None,
        "reason": page.reason,
        "state": page.state.value,
        "severity": page.severity.value,
        "hop": page.hop,
        "ack_timeout_s": page.ack_timeout_s,
        "ack_deadline_at": (
            page.ack_deadline_at.isoformat() if page.ack_deadline_at else None
        ),
        "channels": sorted({link.channel.value for link in page.deliveries}),
        "reached": page.reached_anyone,
        "detail": page.detail,
    }
