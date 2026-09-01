"""Ingest ports (CLAUDE.md rule 1). The domain says what it needs; the SQL
implementations live in infrastructure.

Tenant isolation (REQ-013) is inside the implementations, with one deliberate
cross-tenant read - `find_by_token_hash` - documented at its definition below.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional, Sequence

from signalforge.domain.ingest.keys import IngestKey
from signalforge.domain.ingest.receipts import IngestReceipt
from signalforge.domain.ingest.streams import IngestStream


class IngestKeyRepository(ABC):
    @abstractmethod
    async def add(self, key: IngestKey) -> None: ...

    @abstractmethod
    async def get(self, tenant_id: str, key_id: str) -> Optional[IngestKey]: ...

    @abstractmethod
    async def find_by_token_hash(self, token_hash: str) -> Optional[IngestKey]:
        """Resolve a presented token to its key, across tenants.

        Deliberately not tenant-scoped, like `EventStore.read_for_dispatch` and
        `ConnectorBindingRepository.list_enabled_for_health`: the token *is* the
        tenant selector, because an unauthenticated HTTP request has no other
        way to say who it is. The key it returns carries its own tenant_id, and
        every write that follows goes through the scoped methods below, so the
        isolation boundary moves to the caller's writes rather than disappearing.
        Lookup is by sha256 of the token, so the stored material is not usable.
        """

    @abstractmethod
    async def list(
        self,
        tenant_id: str,
        *,
        app_id: Optional[str] = None,
        stream: Optional[IngestStream] = None,
        include_revoked: bool = False,
    ) -> Sequence[IngestKey]: ...

    @abstractmethod
    async def list_for_binding(
        self, tenant_id: str, binding_id: str
    ) -> Sequence[IngestKey]: ...

    @abstractmethod
    async def update(self, key: IngestKey) -> None: ...


class IngestReceiptStore(ABC):
    @abstractmethod
    async def seen(self, tenant_id: str, event_ids: Sequence[str]) -> set[str]:
        """Which of these producer event ids this tenant has already recorded.

        Asked once per batch rather than once per item: a 500-item retry must
        not be 500 round trips.
        """

    @abstractmethod
    async def add(self, receipt: IngestReceipt) -> None: ...

    @abstractmethod
    async def get(self, tenant_id: str, event_id: str) -> Optional[IngestReceipt]: ...

    @abstractmethod
    async def count_for_key(self, tenant_id: str, key_id: str) -> int: ...
