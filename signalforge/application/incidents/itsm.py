"""Keeping an ITSM ticket in step with an incident (REQ-043, REQ-033).

The rule that shapes every line of this file: **the sync may never decide
whether an incident exists.** A ticket queue being down, slow, misconfigured or
absent has to be survivable, because the alternative is a platform that cannot
declare a P0 while ServiceNow is having a bad afternoon. So `sync` never raises:
it records what happened on the incident's own row, publishes it on the incident
timeline, and leaves the incident exactly as declared.

The five sync states are the failure design, written down:

| State | What happened | What the retry does |
|---|---|---|
| `not_configured` | no ITSM connector is bound to this application | nothing; there is nothing to sync to |
| `pending` | never attempted yet | attempt it |
| `synced` | the ticket matches the incident | nothing until the incident moves |
| `failed` | the ITSM refused, and said why | attempt again; the reason is on the row |
| `uncertain` | a call timed out - the ticket may or may not exist | **reconcile, then retry** |

`uncertain` is the one that earns its keep. A create that timed out is not a
create that failed: retrying it blindly is how an incident ends up with two
tickets, and treating it as done is how it ends up with none. So the retry path
calls `get` first (the ITSM equivalent of the actions SPI's
reconcile-before-retry) and only creates when the queue itself says the ticket is
not there.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from typing import Any, Callable, Optional

from signalforge.application.adapters.invoker import ConnectorInvoker
from signalforge.application.identity.uow import IdentityUnitOfWork
from signalforge.application.incidents.common import (
    ITSM_FAILED_TOPIC,
    ITSM_SYNCED_TOPIC,
    ITSM_UNCERTAIN_TOPIC,
    publish_system,
)
from signalforge.application.events.bus import EventBus
from signalforge.contracts.canonical import Ticket, TicketStatus
from signalforge.contracts.envelope import AdapterKind
from signalforge.contracts.errors import AdapterError, AdapterTimeoutError
from signalforge.domain.adapters.bindings import ConnectorBinding
from signalforge.domain.adapters.ports import AdapterRegistry
from signalforge.domain.identity.ports import Clock
from signalforge.domain.incidents.entities import Incident, TicketSync, TicketSyncState
from signalforge.domain.incidents.itsm import (
    ticket_fields,
    ticket_path,
    ticket_status_for,
    ticket_title,
)
from signalforge.domain.shared.errors import DomainError

logger = logging.getLogger("signalforge.lifecycle")

CREATE = "create"
UPDATE = "update"
GET = "get"

MAX_DETAIL_CHARS = 400


@dataclass(frozen=True)
class SyncSweep:
    considered: int = 0
    synced: int = 0
    failed: int = 0
    uncertain: int = 0
    skipped: int = 0

    def __str__(self) -> str:
        return (
            f"{self.considered} incident(s) with an open ITSM link: {self.synced} "
            f"synced, {self.failed} failed, {self.uncertain} uncertain, "
            f"{self.skipped} with nothing to sync to"
        )


class IncidentTicketSync:
    def __init__(
        self,
        uow_factory: Callable[[], IdentityUnitOfWork],
        registry: AdapterRegistry,
        invoker: ConnectorInvoker,
        bus: EventBus,
        clock: Clock,
        metrics=None,
    ):
        self._uow_factory = uow_factory
        self._registry = registry
        self._invoker = invoker
        self._bus = bus
        self._clock = clock
        self._metrics = metrics

    # -- the one entry point -------------------------------------------------

    async def sync(self, incident: Incident) -> Incident:
        """Bring the ticket in line with the incident. Never raises."""
        before = incident.ticket
        binding = await self._binding_for(incident.tenant_id, incident.app_id)
        if binding is None:
            after = TicketSync(
                state=TicketSyncState.NOT_CONFIGURED,
                attempts=before.attempts,
                detail=(
                    "no ITSM connector is bound to this application; the incident "
                    "is recorded in SignalForge and nowhere else"
                ),
                last_attempt_at=self._clock.now(),
            )
        else:
            after = await self._attempt(incident, binding)
        return await self._store(incident, before, after)

    async def retry_pending(self, limit: int = 50) -> SyncSweep:
        """The scheduled recovery path. One job serves the install."""
        async with self._uow_factory() as uow:
            pending = list(await uow.incidents.needing_sync(limit=limit))
        synced = failed = uncertain = skipped = 0
        for incident in pending:
            updated = await self.sync(incident)
            state = updated.ticket.state
            if state is TicketSyncState.SYNCED:
                synced += 1
            elif state is TicketSyncState.FAILED:
                failed += 1
            elif state is TicketSyncState.UNCERTAIN:
                uncertain += 1
            else:
                skipped += 1
        return SyncSweep(
            considered=len(pending),
            synced=synced,
            failed=failed,
            uncertain=uncertain,
            skipped=skipped,
        )

    # -- the attempt ---------------------------------------------------------

    async def _attempt(
        self, incident: Incident, binding: ConnectorBinding
    ) -> TicketSync:
        before = incident.ticket
        now = self._clock.now()
        base = TicketSync(
            state=before.state,
            ticket_id=before.ticket_id or incident.id,
            reference=before.reference,
            status=before.status,
            adapter_id=binding.adapter_id,
            binding_id=binding.id,
            attempts=before.attempts + 1,
            detail=before.detail,
            last_attempt_at=now,
            synced_at=before.synced_at,
        )
        desired = ticket_status_for(incident.state)

        current = before.status
        if before.state is TicketSyncState.UNCERTAIN or (
            before.ticket_id and not before.reference
        ):
            # Reconcile before retry. Asking the queue what it holds is the only
            # way to tell "the create never landed" from "the create landed and
            # the answer was lost", and those need opposite next moves.
            reconciled, outcome = await self._reconcile(incident, binding, base)
            if outcome is not None:
                return outcome
            current = reconciled

        try:
            if current is None:
                ticket = await self._create(incident, binding, desired)
            else:
                ticket = await self._advance(
                    incident, binding, base.ticket_id or incident.id, current, desired
                )
        except AdapterTimeoutError as exc:
            return self._uncertain(base, str(exc))
        except (AdapterError, DomainError) as exc:
            return self._failed(base, f"{type(exc).__name__}: {exc}")
        except Exception as exc:  # pragma: no cover - an adapter outside the taxonomy
            logger.warning(
                "itsm adapter raised outside the error taxonomy",
                extra={"adapter": binding.adapter_id, "app_id": incident.app_id},
            )
            return self._failed(base, f"{type(exc).__name__}: {exc}")

        self._count("synced")
        return TicketSync(
            state=TicketSyncState.SYNCED,
            ticket_id=ticket.ticket_id,
            reference=ticket.external_ref or ticket.ticket_id,
            status=ticket.status,
            adapter_id=binding.adapter_id,
            binding_id=binding.id,
            attempts=base.attempts,
            detail=(
                f"{binding.adapter_id} ticket {ticket.external_ref or ticket.ticket_id} "
                f"is {ticket.status.value}"
            ),
            last_attempt_at=now,
            synced_at=now,
        )

    async def _reconcile(
        self, incident: Incident, binding: ConnectorBinding, base: TicketSync
    ) -> tuple[Optional[TicketStatus], Optional[TicketSync]]:
        """Ask the ITSM whether the ticket this incident may have created exists.

        Returns (current status, None) when the question was answered - a status
        when a ticket is there, None when it demonstrably is not - and
        (None, outcome) when it could not be answered at all, which leaves the
        link uncertain rather than guessing in either direction.
        """
        try:
            ticket: Ticket = await self._invoker.invoke(
                incident.tenant_id, binding.id, GET, base.ticket_id or incident.id
            )
        except AdapterTimeoutError as exc:
            return None, self._uncertain(
                base, f"reconcile timed out: {exc}"
            )
        except AdapterError:
            # The queue answered, and its answer is "no such ticket". That is a
            # definite negative, and it makes creating one safe.
            return None, None
        except DomainError as exc:
            return None, self._failed(base, f"{type(exc).__name__}: {exc}")
        return ticket.status, None

    async def _create(
        self, incident: Incident, binding: ConnectorBinding, desired: TicketStatus
    ) -> Ticket:
        return await self._invoker.invoke(
            incident.tenant_id,
            binding.id,
            CREATE,
            Ticket(
                ticket_id=incident.id,
                title=ticket_title(incident),
                status=desired,
                created_at=incident.declared_at,
                description=incident.summary or incident.title,
                severity=incident.severity.value,
                links=[],
            ),
        )

    async def _advance(
        self,
        incident: Incident,
        binding: ConnectorBinding,
        ticket_id: str,
        current: TicketStatus,
        desired: TicketStatus,
    ) -> Ticket:
        """Walk the ticket to the status the incident implies, one step at a time.

        ITSM systems refuse skipped states, so closing an incident whose ticket
        is in progress is two updates rather than one. The fields ride on the
        first call so a re-graded severity reaches the ticket even when the
        status does not move.
        """
        path = ticket_path(current, desired)
        fields: dict[str, Any] = ticket_fields(incident) | {"title": ticket_title(incident)}
        if not path:
            return await self._invoker.invoke(
                incident.tenant_id, binding.id, UPDATE, ticket_id, fields
            )
        ticket: Optional[Ticket] = None
        for index, status in enumerate(path):
            payload = dict(fields) if index == 0 else {}
            payload["status"] = status.value
            ticket = await self._invoker.invoke(
                incident.tenant_id, binding.id, UPDATE, ticket_id, payload
            )
        assert ticket is not None  # a non-empty path always assigns
        return ticket

    # -- outcomes ------------------------------------------------------------

    def _uncertain(self, base: TicketSync, detail: str) -> TicketSync:
        self._count("uncertain")
        return replace(
            base,
            state=TicketSyncState.UNCERTAIN,
            detail=(
                f"{detail[:MAX_DETAIL_CHARS]} - the ticket may or may not exist; "
                "the next attempt reconciles before it creates"
            ),
        )

    def _failed(self, base: TicketSync, detail: str) -> TicketSync:
        self._count("failed")
        return replace(
            base, state=TicketSyncState.FAILED, detail=detail[:MAX_DETAIL_CHARS]
        )

    def _count(self, outcome: str) -> None:
        if self._metrics is not None:
            self._metrics.increment(
                "signalforge_incident_ticket_sync_total", {"outcome": outcome}
            )

    # -- persistence ---------------------------------------------------------

    async def _store(
        self, incident: Incident, before: TicketSync, after: TicketSync
    ) -> Incident:
        """Write the ticket link onto the incident *as it is now*.

        The incident this method was handed may be minutes old: the sweep reads
        its batch, then calls the ITSM for each row, and a person can close or
        re-grade an incident while that call is in flight. Writing the whole
        stale row back would reopen a closed incident. So the ticket link is
        applied to the row re-read inside the same transaction, and only the
        ticket moves.
        """
        unchanged = (
            before.state is after.state
            and before.reference == after.reference
            and before.status == after.status
            and before.detail == after.detail
        )
        now = self._clock.now()
        topic = {
            TicketSyncState.SYNCED: ITSM_SYNCED_TOPIC,
            TicketSyncState.FAILED: ITSM_FAILED_TOPIC,
            TicketSyncState.UNCERTAIN: ITSM_UNCERTAIN_TOPIC,
        }.get(after.state)
        async with self._uow_factory() as uow:
            current = await uow.incidents.get(incident.tenant_id, incident.id)
            if current is None:
                # Removed with its application while the call was in flight.
                return incident.with_ticket(after, at=now)
            if unchanged:
                # Nothing observable changed. The row is still written so
                # `attempts` and the timestamp move, but no event: a thirty-second
                # retry loop that published every attempt would bury the
                # incident's timeline.
                updated = current.with_ticket(after, at=current.updated_at)
                await uow.incidents.update(updated)
                await uow.commit()
                return updated
            updated = current.with_ticket(after, at=now)
            await uow.incidents.update(updated)
            if topic is not None:
                await publish_system(
                    uow,
                    self._bus,
                    self._clock,
                    tenant_id=incident.tenant_id,
                    app_id=incident.app_id,
                    incident_id=incident.id,
                    topic=topic,
                    payload={
                        "incident_id": incident.id,
                        "reference": incident.reference,
                        "sync_state": after.state.value,
                        "adapter_id": after.adapter_id,
                        "ticket_reference": after.reference,
                        "ticket_status": after.status.value if after.status else None,
                        "attempts": after.attempts,
                        "detail": after.detail,
                    },
                )
            await uow.commit()
        return updated

    # -- wiring --------------------------------------------------------------

    async def _binding_for(
        self, tenant_id: str, app_id: str
    ) -> Optional[ConnectorBinding]:
        async with self._uow_factory() as uow:
            bindings = await uow.connectors.list(
                tenant_id, app_id=app_id, enabled_only=True
            )
        for binding in bindings:
            manifest = self._registry.find(binding.adapter_id)
            if (
                manifest is not None
                and manifest.kind is AdapterKind.ITSM
                and manifest.supports(CREATE)
            ):
                return binding
        return None
