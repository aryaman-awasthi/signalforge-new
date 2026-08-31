"""Refresh-token material (REQ-010). Stdlib-only, so it lives in the domain:
tokens are 256-bit random secrets and only their sha256 ever touches storage."""

from __future__ import annotations

import hashlib
import secrets


def new_refresh_token() -> str:
    return secrets.token_urlsafe(32)


def hash_refresh_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
