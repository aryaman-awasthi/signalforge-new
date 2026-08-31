"""Audit hash-chain writer (REQ-016).

Every entry stores the canonical hash of its payload and the payload hash of
the previous entry in the same tenant's chain (per-tenant chains never cross:
prev_hash of a genesis entry is NULL). verify_chain recomputes both links, so
editing a payload, or rewriting a payload together with its hash, is detected.
There is intentionally no enable/disable switch: the policy floor says audit
cannot be turned off, so the capability does not exist.
"""

from __future__ import annotations

import json
import uuid
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from signalforge.contracts.envelope import canonical_payload_hash
from signalforge.domain.identity.ports import AuditWriter, Clock
from signalforge.infrastructure.persistence.models import AuditLogRow


class AuditChainWriter(AuditWriter):
    def __init__(self, session: AsyncSession, clock: Clock):
        self._s = session
        self._clock = clock

    async def append(
        self,
        *,
        tenant_id: str,
        topic: str,
        actor_type: str,
        actor_id: str,
        payload: dict[str, Any],
        app_id: Optional[str] = None,
    ) -> None:
        last_hash = (
            await self._s.execute(
                select(AuditLogRow.payload_hash)
                .where(AuditLogRow.tenant_id == tenant_id)
                .order_by(AuditLogRow.seq.desc())
                .limit(1)
            )
        ).scalars().first()
        self._s.add(
            AuditLogRow(
                tenant_id=tenant_id,
                event_id=str(uuid.uuid4()),
                topic=topic,
                actor_type=actor_type,
                actor_id=actor_id,
                app_id=app_id,
                occurred_at=self._clock.now(),
                payload=json.dumps(payload, sort_keys=True, ensure_ascii=False),
                payload_hash=canonical_payload_hash(payload),
                prev_hash=last_hash,
            )
        )
        await self._s.flush()


async def verify_chain(session: AsyncSession, tenant_id: str) -> list[int]:
    """Returns the seq numbers of entries whose payload hash or prev link no
    longer verifies; empty list means the chain is intact."""
    rows = (
        await session.execute(
            select(AuditLogRow)
            .where(AuditLogRow.tenant_id == tenant_id)
            .order_by(AuditLogRow.seq)
        )
    ).scalars().all()
    broken: list[int] = []
    prev_hash: Optional[str] = None
    for index, row in enumerate(rows):
        recomputed = canonical_payload_hash(json.loads(row.payload))
        expected_prev = None if index == 0 else prev_hash
        if recomputed != row.payload_hash or row.prev_hash != expected_prev:
            broken.append(row.seq)
        prev_hash = row.payload_hash
    return broken
