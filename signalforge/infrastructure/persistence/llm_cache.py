"""The durable side of the model cache (REQ-051).

One session per operation rather than a unit of work the caller holds. A cache
write is not part of the caller's transaction and must never be able to roll one
back: an incident whose declaration failed because a cache insert conflicted
would be a spectacular way to lose a P0. So a failed write is logged and
swallowed - the answer is still returned, it just is not remembered.

The store lives in `signalforge.db` beside the audit chain because it is small,
tenant-scoped and useful to an auditor asking "was this answer generated or
replayed"; it holds no prompt text (see `LlmCacheRow`).
"""

from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import async_sessionmaker

from signalforge.domain.identity.ports import Clock
from signalforge.domain.llm.ports import ResponseCache
from signalforge.infrastructure.persistence.models import LlmCacheRow

logger = logging.getLogger("signalforge.llm")


class SqlResponseCache(ResponseCache):
    def __init__(self, session_factory: async_sessionmaker, clock: Clock):
        self._sessions = session_factory
        self._clock = clock

    async def get(self, tenant_id: str, key: str) -> Optional[dict]:
        try:
            async with self._sessions() as session:
                row = (
                    await session.execute(
                        select(LlmCacheRow).where(
                            LlmCacheRow.tenant_id == tenant_id,
                            LlmCacheRow.cache_key == key,
                        )
                    )
                ).scalar_one_or_none()
                if row is None:
                    return None
                # A hit count, not an access log: it answers "is the cache
                # working" without recording who asked what and when.
                await session.execute(
                    update(LlmCacheRow)
                    .where(
                        LlmCacheRow.tenant_id == tenant_id,
                        LlmCacheRow.cache_key == key,
                    )
                    .values(hits=LlmCacheRow.hits + 1)
                )
                await session.commit()
                return dict(row.payload or {})
        except Exception as exc:  # a cache is never load-bearing
            logger.warning(
                "llm cache read failed; treating as a miss",
                extra={"error": f"{type(exc).__name__}: {exc}"},
            )
            return None

    async def put(self, tenant_id, key, *, model, purpose, payload) -> None:
        usage = payload.get("usage") or {}
        try:
            async with self._sessions() as session:
                existing = (
                    await session.execute(
                        select(LlmCacheRow).where(
                            LlmCacheRow.tenant_id == tenant_id,
                            LlmCacheRow.cache_key == key,
                        )
                    )
                ).scalar_one_or_none()
                if existing is not None:
                    return
                session.add(
                    LlmCacheRow(
                        tenant_id=tenant_id,
                        cache_key=key,
                        provider=str(payload.get("provider", "")),
                        model=model,
                        purpose=purpose,
                        payload=payload,
                        prompt_tokens=int(usage.get("prompt", 0) or 0),
                        completion_tokens=int(usage.get("completion", 0) or 0),
                        hits=0,
                        created_at=self._clock.now(),
                    )
                )
                await session.commit()
        except Exception as exc:
            logger.warning(
                "llm cache write failed; the answer is returned but not remembered",
                extra={"error": f"{type(exc).__name__}: {exc}"},
            )
