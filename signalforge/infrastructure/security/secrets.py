"""Fernet-encrypted, tenant-scoped secret store (REQ-015).

The key comes from the environment (config.load_settings). Plaintext exists
only in memory on the way in and out; rows hold ciphertext, listings return
names, and nothing here logs or reprs a value.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional, Sequence

from cryptography.fernet import Fernet
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from signalforge.domain.identity.ports import SecretStore
from signalforge.infrastructure.persistence.models import SecretRow


class FernetSecretStore(SecretStore):
    def __init__(self, session: AsyncSession, key: str):
        self._s = session
        self._fernet = Fernet(key.encode("utf-8"))

    def __repr__(self) -> str:  # never leak key material or values
        return "FernetSecretStore(<redacted>)"

    async def put(self, tenant_id: str, name: str, value: str) -> None:
        ciphertext = self._fernet.encrypt(value.encode("utf-8"))
        now = datetime.now(timezone.utc)
        row = await self._s.get(SecretRow, (tenant_id, name))
        if row is None:
            self._s.add(
                SecretRow(
                    tenant_id=tenant_id, name=name, ciphertext=ciphertext,
                    created_at=now, updated_at=now,
                )
            )
        else:
            row.ciphertext = ciphertext
            row.updated_at = now
        await self._s.flush()

    async def get(self, tenant_id: str, name: str) -> Optional[str]:
        row = await self._s.get(SecretRow, (tenant_id, name))
        if row is None:
            return None
        return self._fernet.decrypt(row.ciphertext).decode("utf-8")

    async def list_names(self, tenant_id: str) -> Sequence[str]:
        rows = (
            await self._s.execute(
                select(SecretRow.name)
                .where(SecretRow.tenant_id == tenant_id)
                .order_by(SecretRow.name)
            )
        ).scalars().all()
        return list(rows)

    async def delete(self, tenant_id: str, name: str) -> bool:
        result = await self._s.execute(
            delete(SecretRow).where(
                SecretRow.tenant_id == tenant_id, SecretRow.name == name
            )
        )
        return bool(result.rowcount)
