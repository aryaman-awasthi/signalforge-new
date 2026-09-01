"""Wire shapes for ingest keys (REQ-032).

One rule holds for everything in this module: the credential material appears in
exactly one model, `IssuedKeyOut`, which is only ever the body of a 201. Every
listing shape carries the display prefix and nothing else, because a platform
that can show you a token can show it to whoever takes over your session.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field

from signalforge.domain.ingest.keys import IngestKey
from signalforge.domain.ingest.streams import IngestStream


class IngestKeyIn(BaseModel):
    app_id: str = Field(min_length=1, max_length=100)
    stream: IngestStream
    name: str = Field(min_length=1, max_length=80)
    binding_id: Optional[str] = Field(
        default=None,
        description=(
            "Push connector binding whose adapter normalises vendor-shaped "
            "payloads for this stream. Omit when the sender posts the canonical "
            "contract shape."
        ),
    )


class IngestKeyOut(BaseModel):
    id: str
    app_id: str
    stream: str
    name: str
    path: str = Field(description="Where this key posts; prefix it with your API base URL")
    binding_id: Optional[str] = None
    rotated_from: Optional[str] = None
    token_prefix: str = Field(
        description="The first characters of the token, so two keys can be told "
        "apart. The token itself is returned once, at issue, and never again."
    )
    active: bool
    created_at: Optional[datetime] = None
    created_by: str
    revoked_at: Optional[datetime] = None
    revoked_by: Optional[str] = None
    last_used_at: Optional[datetime] = None
    last_event_at: Optional[datetime] = None
    freshness_s: Optional[float] = Field(
        default=None,
        description="Seconds since anything actually arrived on this key; null "
        "when nothing ever has. A push binding cannot measure this by pinging.",
    )
    last_error: Optional[str] = Field(
        default=None,
        description="Why the most recent request was refused. The sender only "
        "ever sees a bare 401; this is where the reason is readable.",
    )
    accepted_count: int
    duplicate_count: int
    rejected_count: int


class IssuedKeyOut(IngestKeyOut):
    """The 201 body, and the only place the credential material exists."""

    token: str = Field(description="Bearer token. Shown once; store it now.")
    secret: str = Field(
        description="HMAC-SHA256 shared secret. Sign '<unix seconds>.<raw body>' "
        "with it and send the hex digest in X-SignalForge-Signature. Shown once."
    )


def ingest_key_out(key: IngestKey, now: datetime) -> IngestKeyOut:
    return IngestKeyOut(**_fields(key, now))


def issued_key_out(issued, now: datetime) -> IssuedKeyOut:
    return IssuedKeyOut(
        **_fields(issued.key, now), token=issued.token, secret=issued.secret
    )


def _fields(key: IngestKey, now: datetime) -> dict:
    return {
        "id": key.id,
        "app_id": key.app_id,
        "stream": key.stream.value,
        "name": key.name,
        "path": key.path,
        "binding_id": key.binding_id,
        "rotated_from": key.rotated_from,
        "token_prefix": key.token_prefix,
        "active": key.active,
        "created_at": key.created_at,
        "created_by": key.created_by,
        "revoked_at": key.revoked_at,
        "revoked_by": key.revoked_by,
        "last_used_at": key.last_used_at,
        "last_event_at": key.last_event_at,
        "freshness_s": key.freshness_s(now),
        "last_error": key.last_error,
        "accepted_count": key.accepted_count,
        "duplicate_count": key.duplicate_count,
        "rejected_count": key.rejected_count,
    }
