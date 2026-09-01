"""Ingest key and receipt repositories (REQ-032, REQ-013).

Tenant isolation lives here, not in the router: every method that reads or
writes a tenant's keys or receipts carries the tenant predicate inside this
class. The one exception is `find_by_token_hash`, documented at the port: an
unauthenticated HTTP request has no way to name its tenant, so the token is the
selector, and the key it returns carries the tenant every subsequent call is
then scoped to.
"""

from __future__ import annotations

from typing import Optional, Sequence

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from signalforge.domain.ingest.keys import IngestKey
from signalforge.domain.ingest.ports import IngestKeyRepository, IngestReceiptStore
from signalforge.domain.ingest.receipts import IngestReceipt
from signalforge.domain.ingest.streams import IngestStream
from signalforge.infrastructure.persistence.models import IngestKeyRow, IngestReceiptRow

SEEN_CHUNK = 200
"""SQLite's default host-parameter ceiling is 999; a 500-item batch asked in one
IN clause is safe today and would stop being safe the moment MAX_BATCH_ITEMS
grew, so the question is chunked rather than assumed to fit."""


class SqlIngestKeyRepository(IngestKeyRepository):
    def __init__(self, session: AsyncSession):
        self._s = session

    async def add(self, key: IngestKey) -> None:
        self._s.add(
            IngestKeyRow(
                id=key.id,
                tenant_id=key.tenant_id,
                app_id=key.app_id,
                stream=key.stream.value,
                name=key.name,
                token_hash=key.token_hash,
                token_prefix=key.token_prefix,
                binding_id=key.binding_id,
                rotated_from=key.rotated_from,
                created_at=key.created_at,
                created_by=key.created_by,
                revoked_at=key.revoked_at,
                revoked_by=key.revoked_by,
                last_used_at=key.last_used_at,
                last_event_at=key.last_event_at,
                last_error=key.last_error,
                accepted_count=key.accepted_count,
                duplicate_count=key.duplicate_count,
                rejected_count=key.rejected_count,
            )
        )
        await self._s.flush()

    async def get(self, tenant_id: str, key_id: str) -> Optional[IngestKey]:
        row = (
            await self._s.execute(
                select(IngestKeyRow).where(
                    IngestKeyRow.tenant_id == tenant_id, IngestKeyRow.id == key_id
                )
            )
        ).scalars().first()
        return _to_key(row)

    async def find_by_token_hash(self, token_hash: str) -> Optional[IngestKey]:
        row = (
            await self._s.execute(
                select(IngestKeyRow).where(IngestKeyRow.token_hash == token_hash)
            )
        ).scalars().first()
        return _to_key(row)

    async def list(
        self,
        tenant_id: str,
        *,
        app_id: Optional[str] = None,
        stream: Optional[IngestStream] = None,
        include_revoked: bool = False,
    ) -> Sequence[IngestKey]:
        statement = select(IngestKeyRow).where(IngestKeyRow.tenant_id == tenant_id)
        if app_id is not None:
            statement = statement.where(IngestKeyRow.app_id == app_id)
        if stream is not None:
            statement = statement.where(IngestKeyRow.stream == stream.value)
        if not include_revoked:
            statement = statement.where(IngestKeyRow.revoked_at.is_(None))
        rows = (
            await self._s.execute(
                statement.order_by(
                    IngestKeyRow.app_id, IngestKeyRow.stream, IngestKeyRow.created_at
                )
            )
        ).scalars().all()
        return [_to_key(row) for row in rows]

    async def list_for_binding(
        self, tenant_id: str, binding_id: str
    ) -> Sequence[IngestKey]:
        rows = (
            await self._s.execute(
                select(IngestKeyRow)
                .where(
                    IngestKeyRow.tenant_id == tenant_id,
                    IngestKeyRow.binding_id == binding_id,
                )
                .order_by(IngestKeyRow.created_at)
            )
        ).scalars().all()
        return [_to_key(row) for row in rows]

    async def update(self, key: IngestKey) -> None:
        await self._s.execute(
            update(IngestKeyRow)
            .where(
                IngestKeyRow.tenant_id == key.tenant_id, IngestKeyRow.id == key.id
            )
            .values(
                name=key.name,
                binding_id=key.binding_id,
                revoked_at=key.revoked_at,
                revoked_by=key.revoked_by,
                last_used_at=key.last_used_at,
                last_event_at=key.last_event_at,
                last_error=key.last_error,
                accepted_count=key.accepted_count,
                duplicate_count=key.duplicate_count,
                rejected_count=key.rejected_count,
            )
        )
        await self._s.flush()


class SqlIngestReceiptStore(IngestReceiptStore):
    def __init__(self, session: AsyncSession):
        self._s = session

    async def seen(self, tenant_id: str, event_ids: Sequence[str]) -> set[str]:
        found: set[str] = set()
        unique = list(dict.fromkeys(event_ids))
        for start in range(0, len(unique), SEEN_CHUNK):
            chunk = unique[start:start + SEEN_CHUNK]
            rows = (
                await self._s.execute(
                    select(IngestReceiptRow.event_id).where(
                        IngestReceiptRow.tenant_id == tenant_id,
                        IngestReceiptRow.event_id.in_(chunk),
                    )
                )
            ).scalars().all()
            found.update(rows)
        return found

    async def add(self, receipt: IngestReceipt) -> None:
        self._s.add(
            IngestReceiptRow(
                tenant_id=receipt.tenant_id,
                event_id=receipt.event_id,
                key_id=receipt.key_id,
                app_id=receipt.app_id,
                stream=receipt.stream.value,
                source_system=receipt.source_system,
                occurred_at=receipt.occurred_at,
                received_at=receipt.received_at,
                platform_event_id=receipt.platform_event_id,
                event_seq=receipt.event_seq,
                event_count=receipt.event_count,
                simulated=receipt.simulated,
                normalized_by=receipt.normalized_by,
            )
        )
        await self._s.flush()

    async def get(self, tenant_id: str, event_id: str) -> Optional[IngestReceipt]:
        row = (
            await self._s.execute(
                select(IngestReceiptRow).where(
                    IngestReceiptRow.tenant_id == tenant_id,
                    IngestReceiptRow.event_id == event_id,
                )
            )
        ).scalars().first()
        if row is None:
            return None
        return IngestReceipt(
            tenant_id=row.tenant_id,
            event_id=row.event_id,
            key_id=row.key_id,
            app_id=row.app_id,
            stream=IngestStream(row.stream),
            source_system=row.source_system,
            occurred_at=row.occurred_at,
            received_at=row.received_at,
            platform_event_id=row.platform_event_id,
            event_seq=row.event_seq,
            event_count=row.event_count,
            simulated=row.simulated,
            normalized_by=row.normalized_by,
        )

    async def count_for_key(self, tenant_id: str, key_id: str) -> int:
        return int(
            (
                await self._s.execute(
                    select(func.count())
                    .select_from(IngestReceiptRow)
                    .where(
                        IngestReceiptRow.tenant_id == tenant_id,
                        IngestReceiptRow.key_id == key_id,
                    )
                )
            ).scalar_one()
        )


def _to_key(row: Optional[IngestKeyRow]) -> Optional[IngestKey]:
    if row is None:
        return None
    return IngestKey(
        id=row.id,
        tenant_id=row.tenant_id,
        app_id=row.app_id,
        stream=IngestStream(row.stream),
        name=row.name,
        token_hash=row.token_hash,
        token_prefix=row.token_prefix,
        binding_id=row.binding_id,
        rotated_from=row.rotated_from,
        created_at=row.created_at,
        created_by=row.created_by,
        revoked_at=row.revoked_at,
        revoked_by=row.revoked_by,
        last_used_at=row.last_used_at,
        last_event_at=row.last_event_at,
        last_error=row.last_error,
        accepted_count=row.accepted_count,
        duplicate_count=row.duplicate_count,
        rejected_count=row.rejected_count,
    )
