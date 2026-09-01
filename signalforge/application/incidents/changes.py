"""Change windows: the projection, and the read behind the chip (REQ-044).

Two halves that never touch each other's transaction.

`ChangeWindowProjector` is a consumer group on the bus. It sees every
`deployment.*` and `change.*` event that push ingestion accepted, folds it into
the window its `change_id` names, and writes through the dispatcher's own unit of
work - so the projection, the idempotency-ledger row and the consumer offset
commit together. That is what makes redelivery, catch-up after a restart and an
operator requeue all harmless: the fold is idempotent, and the ledger means it
usually is not even attempted twice.

`ChangeWindowService` is the read. It never scans the event log: "is a change in
flight right now" is asked every time an application screen opens and every time
an incident is declared, and answering it by walking the timeline would make the
answer depend on how much other traffic happened to be in the way.

A payload that will not parse is skipped rather than raised. It has already been
recorded as an event by ingestion, the sender has already been told, and letting
one malformed change document dead-letter the group would stop every *other*
application's window updating.
"""

from __future__ import annotations

import logging
from typing import Callable, Optional, Sequence

from pydantic import ValidationError

from signalforge.application.events.uow import EventUnitOfWork
from signalforge.application.identity.principal import Principal
from signalforge.application.identity.uow import IdentityUnitOfWork
from signalforge.application.onboarding.common import require_application
from signalforge.contracts.canonical import ChangeEvent
from signalforge.contracts.envelope import EventEnvelope
from signalforge.domain.events.ports import EventHandler
from signalforge.domain.identity.ports import Clock
from signalforge.domain.incidents.changes import (
    CHANGE_WINDOW_MAX_S,
    ChangeWindowView,
    merge_change,
    window_view,
)
from signalforge.domain.incidents.entities import ChangeWindowLink

logger = logging.getLogger("signalforge.lifecycle")

MAX_WINDOWS_READ = 100


class ChangeWindowProjector(EventHandler):
    name = "change-windows"

    def __init__(self, metrics=None):
        self._metrics = metrics

    async def handle(self, envelope: EventEnvelope, uow: EventUnitOfWork) -> None:
        if not envelope.app_id:
            # A change that names no application belongs to no application's
            # chip. Ingest keys are scoped to one app, so this is only reachable
            # for an event some other producer wrote.
            return
        try:
            event = ChangeEvent.model_validate(envelope.payload)
        except ValidationError:
            logger.warning(
                "change event payload does not match the canonical model",
                extra={"topic": envelope.topic, "app_id": envelope.app_id},
            )
            return
        existing = await uow.changes.get(
            envelope.tenant_id, envelope.app_id, event.change_id
        )
        window = merge_change(
            existing,
            event,
            tenant_id=envelope.tenant_id,
            app_id=envelope.app_id,
            at=envelope.occurred_at,
            source_system=envelope.source.system,
            simulated=envelope.source.simulated,
        )
        await uow.changes.put(window)
        if self._metrics is not None:
            self._metrics.increment(
                "signalforge_change_windows_total",
                {"status": window.status.value, "open": str(window.open).lower()},
            )


class ChangeWindowService:
    def __init__(
        self,
        uow_factory: Callable[[], IdentityUnitOfWork],
        clock: Clock,
        *,
        max_age_s: float = CHANGE_WINDOW_MAX_S,
    ):
        self._uow_factory = uow_factory
        self._clock = clock
        self._max_age_s = max_age_s

    async def view(self, tenant_id: str, app_id: str) -> ChangeWindowView:
        async with self._uow_factory() as uow:
            windows = list(
                await uow.changes.list(tenant_id, app_id, limit=MAX_WINDOWS_READ)
            )
        return window_view(
            windows,
            app_id=app_id,
            at=self._clock.now(),
            max_age_s=self._max_age_s,
        )

    async def read(self, principal: Principal, app_id: str) -> ChangeWindowView:
        """The API read. The application is required first, so an app_id that
        belongs to another tenant answers 404 rather than an empty window."""
        async with self._uow_factory() as uow:
            await require_application(uow, principal.tenant_id, app_id)
        return await self.view(principal.tenant_id, app_id)

    async def links(
        self, tenant_id: str, app_id: str
    ) -> tuple[ChangeWindowLink, ...]:
        """What was in flight, in the shape an incident copies onto itself.

        Copied rather than referenced: "what was changing when this was
        declared" must still be answerable after the deployment finishes and the
        window closes.
        """
        view = await self.view(tenant_id, app_id)
        return tuple(
            ChangeWindowLink(
                change_id=window.change_id,
                change_type=window.change_type.value,
                service=window.service,
                version=window.version,
                started_at=window.started_at,
                actor=window.actor,
            )
            for window in view.open + view.stale
        )

    async def count_open(self) -> int:
        async with self._uow_factory() as uow:
            return await uow.changes.count_open()


def open_change_ids(windows: Sequence[ChangeWindowLink]) -> tuple[str, ...]:
    return tuple(window.change_id for window in windows)


def summarize(view: Optional[ChangeWindowView]) -> str:
    return view.detail if view is not None else "no change window read"
