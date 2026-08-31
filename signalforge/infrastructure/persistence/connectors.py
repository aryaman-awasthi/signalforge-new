"""Connector binding repository (REQ-030, REQ-013).

Tenant isolation lives here, not in the handlers: every read and write that
touches a tenant's bindings carries the tenant_id predicate inside this class.
The one deliberate exception is `list_enabled_for_health`, which is documented
at its definition in the port and in docs/10 section 6: one health loop serves
the whole install, and everything it writes back goes through the scoped methods.
"""

from __future__ import annotations

from typing import Optional, Sequence

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from signalforge.contracts.canonical import HealthStatus
from signalforge.domain.adapters.bindings import ConnectorBinding
from signalforge.domain.adapters.manifest import CredentialScope
from signalforge.domain.adapters.ports import ConnectorBindingRepository
from signalforge.infrastructure.persistence.models import ConnectorBindingRow


class SqlConnectorBindingRepository(ConnectorBindingRepository):
    def __init__(self, session: AsyncSession):
        self._s = session

    async def add(self, binding: ConnectorBinding) -> None:
        self._s.add(
            ConnectorBindingRow(
                id=binding.id,
                tenant_id=binding.tenant_id,
                app_id=binding.app_id,
                adapter_id=binding.adapter_id,
                name=binding.name,
                credential_scope=binding.credential_scope.value,
                config=dict(binding.config),
                credential_fields=list(binding.credential_fields),
                enabled=binding.enabled,
                created_at=binding.created_at,
                created_by=binding.created_by,
                updated_at=binding.updated_at or binding.created_at,
                status=binding.status.value,
                status_detail=binding.status_detail,
                last_checked_at=binding.last_checked_at,
                last_ok_at=binding.last_ok_at,
                last_error=binding.last_error,
                consecutive_failures=binding.consecutive_failures,
            )
        )
        await self._s.flush()

    async def get(self, tenant_id: str, binding_id: str) -> Optional[ConnectorBinding]:
        row = (
            await self._s.execute(
                select(ConnectorBindingRow).where(
                    ConnectorBindingRow.tenant_id == tenant_id,
                    ConnectorBindingRow.id == binding_id,
                )
            )
        ).scalars().first()
        return _to_entity(row)

    async def list(
        self,
        tenant_id: str,
        *,
        app_id: Optional[str] = None,
        adapter_id: Optional[str] = None,
        enabled_only: bool = False,
    ) -> Sequence[ConnectorBinding]:
        statement = select(ConnectorBindingRow).where(
            ConnectorBindingRow.tenant_id == tenant_id
        )
        if app_id is not None:
            statement = statement.where(ConnectorBindingRow.app_id == app_id)
        if adapter_id is not None:
            statement = statement.where(ConnectorBindingRow.adapter_id == adapter_id)
        if enabled_only:
            statement = statement.where(ConnectorBindingRow.enabled.is_(True))
        rows = (
            await self._s.execute(
                statement.order_by(
                    ConnectorBindingRow.app_id,
                    ConnectorBindingRow.adapter_id,
                    ConnectorBindingRow.credential_scope,
                )
            )
        ).scalars().all()
        return [_to_entity(row) for row in rows]

    async def find_conflict(
        self,
        tenant_id: str,
        *,
        app_id: str,
        adapter_id: str,
        scope: CredentialScope,
    ) -> Optional[ConnectorBinding]:
        row = (
            await self._s.execute(
                select(ConnectorBindingRow).where(
                    ConnectorBindingRow.tenant_id == tenant_id,
                    ConnectorBindingRow.app_id == app_id,
                    ConnectorBindingRow.adapter_id == adapter_id,
                    ConnectorBindingRow.credential_scope == scope.value,
                )
            )
        ).scalars().first()
        return _to_entity(row)

    async def update(self, binding: ConnectorBinding) -> None:
        await self._s.execute(
            update(ConnectorBindingRow)
            .where(
                ConnectorBindingRow.tenant_id == binding.tenant_id,
                ConnectorBindingRow.id == binding.id,
            )
            .values(
                name=binding.name,
                config=dict(binding.config),
                credential_fields=list(binding.credential_fields),
                enabled=binding.enabled,
                updated_at=binding.updated_at,
                status=binding.status.value,
                status_detail=binding.status_detail,
                last_checked_at=binding.last_checked_at,
                last_ok_at=binding.last_ok_at,
                last_error=binding.last_error,
                consecutive_failures=binding.consecutive_failures,
            )
        )
        await self._s.flush()

    async def delete(self, tenant_id: str, binding_id: str) -> bool:
        result = await self._s.execute(
            delete(ConnectorBindingRow).where(
                ConnectorBindingRow.tenant_id == tenant_id,
                ConnectorBindingRow.id == binding_id,
            )
        )
        return bool(result.rowcount)

    async def list_enabled_for_health(self) -> Sequence[ConnectorBinding]:
        rows = (
            await self._s.execute(
                select(ConnectorBindingRow)
                .where(ConnectorBindingRow.enabled.is_(True))
                .order_by(ConnectorBindingRow.tenant_id, ConnectorBindingRow.id)
            )
        ).scalars().all()
        return [_to_entity(row) for row in rows]


def _to_entity(row: Optional[ConnectorBindingRow]) -> Optional[ConnectorBinding]:
    if row is None:
        return None
    return ConnectorBinding(
        id=row.id,
        tenant_id=row.tenant_id,
        app_id=row.app_id,
        adapter_id=row.adapter_id,
        name=row.name,
        credential_scope=CredentialScope(row.credential_scope),
        config=dict(row.config or {}),
        credential_fields=tuple(row.credential_fields or ()),
        enabled=row.enabled,
        created_at=row.created_at,
        created_by=row.created_by,
        updated_at=row.updated_at,
        status=HealthStatus(row.status),
        status_detail=row.status_detail,
        last_checked_at=row.last_checked_at,
        last_ok_at=row.last_ok_at,
        last_error=row.last_error,
        consecutive_failures=row.consecutive_failures,
    )
