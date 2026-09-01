"""Incident audit replay (REQ-072, REQ-016).

Given an incident, reconstruct what happened from the hash-chained audit log:
in order, in the incident's own time, with the actor, the justification, the
effect and the references for every line.

Three properties, and each of them is a refusal:

**It refuses to look outside the tenant.** The tenant comes from the
authenticated principal and there is no parameter for it, so a cross-tenant
replay is not a permission check that could be got wrong - it is a query that
cannot be expressed.

**It refuses to look clean when it is not.** The chain is verified over the
tenant's whole log up to the point being replayed, not over the incident's
subset, because a chain verified over a filtered selection verifies nothing.
When verification fails the document still comes back - hiding it would be
worse - but it comes back marked invalid, naming the first record that no
longer holds and why. Nothing downstream is allowed to treat an invalid
document as evidence: `PirService` refuses to build a review from one.

**It refuses to invent.** Every line is one stored record. There is no
enrichment, no summarisation and no model anywhere on this path.
"""

from __future__ import annotations

from typing import Callable, Optional

from signalforge.application.events.uow import EventUnitOfWork
from signalforge.application.identity.principal import Principal
from signalforge.domain.audit.classify import classify
from signalforge.domain.audit.entries import (
    ChainStatus,
    Consequence,
    ReplayDocument,
    ReplayEntry,
)
from signalforge.domain.identity.ports import AuditReader, Clock

MAX_REPLAY_ENTRIES = 2_000


class IncidentReplayService:
    def __init__(
        self,
        uow_factory: Callable[[], EventUnitOfWork],
        *,
        reader_factory: Callable[[object], AuditReader],
        clock: Clock,
        directory: Optional[Callable[[str], dict[str, str]]] = None,
    ):
        self._uow_factory = uow_factory
        self._reader_factory = reader_factory
        self._clock = clock
        self._directory = directory

    async def replay(
        self,
        principal: Principal,
        incident_id: str,
        *,
        until_seq: Optional[int] = None,
        limit: int = MAX_REPLAY_ENTRIES,
    ) -> ReplayDocument:
        tenant_id = principal.tenant_id
        labels = await self._labels(tenant_id)
        async with self._uow_factory() as uow:
            reader = self._reader_factory(uow)
            checked, broken = await reader.verify(tenant_id, until_seq=until_seq)
            records = list(
                await reader.read(
                    tenant_id,
                    incident_id=incident_id,
                    until_seq=until_seq,
                    limit=limit,
                )
            )
            seals = [
                {
                    "seal_id": seal.id,
                    "from_seq": seal.from_seq,
                    "to_seq": seal.to_seq,
                    "event_count": seal.event_count,
                    "seal_hash": seal.seal_hash,
                    "sealed_at": seal.sealed_at,
                    "sealed_by": seal.sealed_by,
                }
                for seal in await uow.seals.list(
                    tenant_id=tenant_id, incident_id=incident_id
                )
            ]

        entries = tuple(classify(record, actor_labels=labels) for record in records)
        return ReplayDocument(
            incident_id=incident_id,
            tenant_id=tenant_id,
            generated_at=self._clock.now(),
            chain=_status(checked, broken),
            entries=entries,
            from_seq=entries[0].seq if entries else 0,
            to_seq=entries[-1].seq if entries else 0,
            seals=tuple(seals),
        )

    async def _labels(self, tenant_id: str) -> dict[str, str]:
        """Actor ids become names, where the tenant has a name for them.

        A replay that said `user 4f1c8a2b` for the person who approved a
        rollback would be technically complete and practically useless.
        """
        if self._directory is None:
            return {}
        return await self._directory(tenant_id)


def _status(checked: int, broken: tuple[int, ...]) -> ChainStatus:
    if not broken:
        return ChainStatus(
            valid=True,
            checked=checked,
            first_broken_seq=None,
            broken_seqs=(),
            reason=f"{checked} audit record(s) verify against their stored hashes",
        )
    first = broken[0]
    return ChainStatus(
        valid=False,
        checked=checked,
        first_broken_seq=first,
        broken_seqs=broken,
        reason=(
            f"the audit chain no longer verifies from seq {first}: a payload was "
            f"edited, or a record was removed, after it was written. "
            f"{len(broken)} record(s) are affected. This replay is NOT evidence."
        ),
    )


def consequential_only(document: ReplayDocument) -> tuple[ReplayEntry, ...]:
    return tuple(
        entry
        for entry in document.entries
        if entry.consequence is Consequence.CONSEQUENTIAL
    )
