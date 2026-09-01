"""itsm-lite: a working ticket queue, clearly labelled SIMULATED (REQ-033).

An installation without ServiceNow still has to be able to rehearse the whole
incident lifecycle, including "a ticket was raised, moved and closed". This
adapter is that queue: real storage, real state machine, real references - and
`simulated: true` in its manifest, so every ticket it produces carries a
SIMULATED badge from the adapter through the event payload to the screen.

Two rules matter more than the storage:

- **Creating the same ticket twice is one ticket.** ITSM calls are retried by
  everything, and a duplicate incident ticket costs a human real time. Create is
  idempotent on `ticket_id` and returns what already exists.
- **Closed is closed.** The status machine in `domain/integrations/entities.py`
  lets a resolved ticket reopen and refuses to reopen a closed one, because
  closure records that a person signed off and a retrying machine must not
  quietly undo it.
"""

from __future__ import annotations

from typing import Any, Optional

from signalforge.contracts.canonical import Ticket, TicketStatus
from signalforge.contracts.errors import AdapterError
from signalforge.contracts.spi import ItsmAdapter
from signalforge.domain.integrations.entities import StoredTicket
from signalforge.domain.shared.errors import DomainValidationError
from signalforge.infrastructure.adapters.base import GenericAdapter

DEFAULT_PREFIX = "LITE"


class ItsmLiteAdapter(GenericAdapter, ItsmAdapter):
    async def ping(self) -> bool:
        """The queue is this deployment's own database, so the ping is a real
        read of it: if the store cannot be read the binding is genuinely down."""
        async with self.services.uow_factory() as uow:
            await uow.tickets.count(self.tenant_id)
        return True

    async def create(self, ticket: Ticket) -> Ticket:
        now = self.now()
        async with self.services.uow_factory() as uow:
            existing = await uow.tickets.get(
                self.tenant_id, self.app_id, ticket.ticket_id
            )
            if existing is not None:
                return self._to_contract(existing)
            reference = self._reference(await uow.tickets.count(self.tenant_id) + 1)
            stored = StoredTicket(
                tenant_id=self.tenant_id,
                app_id=self.app_id,
                ticket_id=ticket.ticket_id,
                reference=reference,
                title=self._required_title(ticket.title),
                status=ticket.status,
                created_at=ticket.created_at or now,
                updated_at=now,
                description=self.redact(ticket.description),
                severity=ticket.severity,
                assignee=ticket.assignee,
                links=tuple(ticket.links),
            )
            await uow.tickets.upsert(stored)
            await uow.commit()
        return self._to_contract(stored)

    async def update(self, ticket_id: str, fields: dict[str, Any]) -> Ticket:
        now = self.now()
        async with self.services.uow_factory() as uow:
            existing = await uow.tickets.get(self.tenant_id, self.app_id, ticket_id)
            if existing is None:
                raise AdapterError(f"ticket {ticket_id} does not exist")
            payload = dict(fields)
            if "description" in payload:
                payload["description"] = self.redact(str(payload["description"] or ""))
            try:
                updated = existing.with_fields(payload, at=now)
            except DomainValidationError as exc:
                # The domain refused the transition. Translated into the adapter
                # taxonomy so callers handle it like any other adapter refusal.
                raise AdapterError(str(exc)) from exc
            await uow.tickets.upsert(updated)
            await uow.commit()
        return self._to_contract(updated)

    async def get(self, ticket_id: str) -> Ticket:
        async with self.services.uow_factory() as uow:
            existing = await uow.tickets.get(self.tenant_id, self.app_id, ticket_id)
        if existing is None:
            raise AdapterError(f"ticket {ticket_id} does not exist")
        return self._to_contract(existing)

    # -- internals ---------------------------------------------------------

    def _reference(self, ordinal: int) -> str:
        prefix = str(self.config("reference_prefix", DEFAULT_PREFIX)).strip() or DEFAULT_PREFIX
        return f"{prefix}-{ordinal:06d}"

    @staticmethod
    def _required_title(title: Optional[str]) -> str:
        text = (title or "").strip()
        if not text:
            raise AdapterError("a ticket needs a title")
        return text

    def _to_contract(self, stored: StoredTicket) -> Ticket:
        return Ticket(
            ticket_id=stored.ticket_id,
            title=stored.title,
            status=TicketStatus(stored.status),
            created_at=stored.created_at,
            updated_at=stored.updated_at,
            description=stored.description,
            severity=stored.severity,
            assignee=stored.assignee,
            external_ref=stored.reference,
            links=list(stored.links),
        )
