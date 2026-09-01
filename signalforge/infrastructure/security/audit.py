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
from typing import Any, Optional, Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from signalforge.contracts.envelope import canonical_payload_hash
from signalforge.domain.audit.entries import AuditRecord
from signalforge.domain.identity.ports import AuditReader, AuditWriter, Clock
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


class SqlAuditReader(AuditReader):
    """The chain, read back, tenant-scoped by construction.

    Incident scoping is done on the payload rather than on a column, because the
    audit log is a *chain* first and an index second: adding an incident column
    would tempt a reader into `where incident_id = ?`, and a chain verified over
    a filtered subset verifies nothing. So verification always runs over the
    tenant's whole chain, and the incident filter is applied to what is
    displayed afterwards.
    """

    def __init__(self, session: AsyncSession):
        self._s = session

    async def read(
        self,
        tenant_id: str,
        *,
        incident_id: Optional[str] = None,
        after_seq: int = 0,
        until_seq: Optional[int] = None,
        limit: int = 1_000,
    ) -> Sequence[AuditRecord]:
        stmt = (
            select(AuditLogRow)
            .where(AuditLogRow.tenant_id == tenant_id, AuditLogRow.seq > after_seq)
            .order_by(AuditLogRow.seq)
        )
        if until_seq is not None:
            stmt = stmt.where(AuditLogRow.seq <= until_seq)
        rows = (await self._s.execute(stmt)).scalars().all()
        records = [_to_record(row) for row in rows]
        if incident_id is not None:
            records = [r for r in records if _mentions(r, incident_id)]
        return records[:limit]

    async def verify(
        self, tenant_id: str, *, until_seq: Optional[int] = None
    ) -> tuple[int, tuple[int, ...]]:
        stmt = (
            select(AuditLogRow)
            .where(AuditLogRow.tenant_id == tenant_id)
            .order_by(AuditLogRow.seq)
        )
        if until_seq is not None:
            stmt = stmt.where(AuditLogRow.seq <= until_seq)
        rows = (await self._s.execute(stmt)).scalars().all()
        broken: list[int] = []
        prev_hash: Optional[str] = None
        for index, row in enumerate(rows):
            recomputed = canonical_payload_hash(json.loads(row.payload))
            expected_prev = None if index == 0 else prev_hash
            if recomputed != row.payload_hash or row.prev_hash != expected_prev:
                broken.append(row.seq)
            prev_hash = row.payload_hash
        return len(rows), tuple(broken)


def _to_record(row: AuditLogRow) -> AuditRecord:
    return AuditRecord(
        seq=row.seq,
        event_id=row.event_id,
        tenant_id=row.tenant_id,
        topic=row.topic,
        actor_type=row.actor_type,
        actor_id=row.actor_id,
        app_id=row.app_id,
        occurred_at=row.occurred_at,
        payload=json.loads(row.payload),
        payload_hash=row.payload_hash,
        prev_hash=row.prev_hash,
    )


def _mentions(record: AuditRecord, incident_id: str) -> bool:
    """Whether this entry belongs to one incident.

    The payload names it, one way or another: `incident_id` on everything the
    lifecycle, the war room, the gateway and validation write, or `id` on the
    declaration itself.
    """
    payload = record.payload or {}
    if payload.get("incident_id") == incident_id:
        return True
    return record.topic.startswith("incident.") and payload.get("id") == incident_id


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
