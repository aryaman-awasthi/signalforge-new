"""Sealing and chain verification (REQ-023, REQ-016).

Sealing closes a range of the chain: the ordered (seq, event_id, payload_hash,
prev_hash) tuples are hashed together with the tenant's previous seal, so seals
form their own chain. Verifying recomputes both the links and the seal hash from
what is in the store now, which is how an auditor tells "this is the history
that was signed off" from "this is the history someone edited afterwards".

Seals are never deleted or rewritten; a later seal continues from the last one.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Callable, Optional

from signalforge.application.events.uow import EventUnitOfWork
from signalforge.application.identity.principal import Principal
from signalforge.contracts.envelope import Plane
from signalforge.domain.events.chain import seal_hash, verify_links
from signalforge.domain.events.entities import EventSeal, SealScope
from signalforge.domain.events.errors import SealConflictError
from signalforge.domain.identity.errors import NotFoundError
from signalforge.domain.identity.ports import Clock

SEAL_TOPIC = "system.bus.sealed"


@dataclass(frozen=True)
class SealVerification:
    seal_id: str
    ok: bool
    recomputed_hash: str
    broken_seqs: tuple[int, ...]
    event_count: int
    detail: str


class SealService:
    def __init__(self, uow_factory: Callable[[], EventUnitOfWork], clock: Clock):
        self._uow_factory = uow_factory
        self._clock = clock

    async def seal(
        self,
        principal: Principal,
        *,
        incident_id: Optional[str] = None,
        plane: Plane = Plane.INCIDENT,
        until: Optional[int] = None,
    ) -> EventSeal:
        scope = SealScope.INCIDENT if incident_id else SealScope.TENANT
        async with self._uow_factory() as uow:
            previous = await uow.seals.latest(principal.tenant_id)
            same_scope = await uow.seals.latest_for_scope(principal.tenant_id, incident_id)
            from_seq = same_scope.to_seq if same_scope else 0
            links = list(
                await uow.events.chain_links(
                    tenant_id=principal.tenant_id,
                    plane=plane,
                    from_seq=from_seq,
                    to_seq=until,
                    incident_id=incident_id,
                )
            )
            if not links:
                raise SealConflictError(
                    "nothing to seal: no unsealed events in this range"
                )
            seal = EventSeal(
                id=str(uuid.uuid4()),
                tenant_id=principal.tenant_id,
                scope=scope,
                plane=plane,
                incident_id=incident_id,
                from_seq=links[0].seq,
                to_seq=links[-1].seq,
                event_count=len(links),
                seal_hash=seal_hash(links, previous.seal_hash if previous else None),
                prev_seal_hash=previous.seal_hash if previous else None,
                sealed_at=self._clock.now(),
                sealed_by=principal.user_id,
            )
            stored = await uow.seals.add(seal)
            await uow.audit.append(
                tenant_id=principal.tenant_id,
                topic=SEAL_TOPIC,
                actor_type="human",
                actor_id=principal.user_id,
                payload={
                    "seal_id": stored.id,
                    "scope": stored.scope.value,
                    "incident_id": stored.incident_id,
                    "plane": stored.plane.value,
                    "from_seq": stored.from_seq,
                    "to_seq": stored.to_seq,
                    "event_count": stored.event_count,
                    "seal_hash": stored.seal_hash,
                },
            )
            await uow.commit()
        return stored

    async def list_seals(
        self, principal: Principal, incident_id: Optional[str] = None
    ) -> tuple[EventSeal, ...]:
        async with self._uow_factory() as uow:
            return tuple(
                await uow.seals.list(
                    tenant_id=principal.tenant_id, incident_id=incident_id
                )
            )

    async def verify(self, principal: Principal, seal_id: str) -> SealVerification:
        async with self._uow_factory() as uow:
            seal = await uow.seals.get(principal.tenant_id, seal_id)
            if seal is None:
                raise NotFoundError(f"seal {seal_id} not found")
            links = list(
                await uow.events.chain_links(
                    tenant_id=seal.tenant_id,
                    plane=seal.plane,
                    from_seq=seal.from_seq - 1,
                    to_seq=seal.to_seq,
                    incident_id=seal.incident_id,
                )
            )
        recomputed = seal_hash(links, seal.prev_seal_hash)
        broken = verify_links(links, anchor_hash=links[0].prev_hash if links else None)
        ok = recomputed == seal.seal_hash and not broken and len(links) == seal.event_count
        return SealVerification(
            seal_id=seal.id,
            ok=ok,
            recomputed_hash=recomputed,
            broken_seqs=broken,
            event_count=len(links),
            detail=(
                "seal verifies against the stored chain"
                if ok
                else "sealed range no longer matches the stored chain"
            ),
        )
