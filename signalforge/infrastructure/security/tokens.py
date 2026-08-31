"""JWT access tokens and refresh-token material (REQ-010).

Access tokens are 15-minute HS256 JWTs referencing a session id, so the
session table doubles as the revocation store: a revoked session invalidates
the refresh token AND every outstanding access token at verification time.
Refresh tokens are 256-bit random secrets, stored only as sha256 hashes;
rotation and reuse detection are use-case logic over the session entity.
Security floor: ttl_s can only shorten the cap, never extend it.
"""

from __future__ import annotations

import hashlib
import secrets
from typing import Any

import jwt

from signalforge.domain.identity.errors import AuthenticationError
from signalforge.domain.identity.ports import AccessTokenService, Clock
from signalforge.infrastructure.config import ACCESS_TTL_CAP_S

_ALGORITHM = "HS256"


class JWTAccessTokenService(AccessTokenService):
    def __init__(self, secret: str, clock: Clock, ttl_s: int = ACCESS_TTL_CAP_S):
        if len(secret) < 32:
            raise ValueError("JWT secret must be at least 32 characters")
        self._secret = secret
        self._clock = clock
        self._ttl_s = min(ttl_s, ACCESS_TTL_CAP_S)

    def issue(self, *, user_id: str, tenant_id: str, session_id: str) -> str:
        now = int(self._clock.now().timestamp())
        claims = {
            "sub": user_id,
            "tid": tenant_id,
            "sid": session_id,
            "jti": secrets.token_hex(16),
            "iat": now,
            "exp": now + self._ttl_s,
            "typ": "access",
        }
        return jwt.encode(claims, self._secret, algorithm=_ALGORITHM)

    def verify(self, token: str) -> dict[str, Any]:
        try:
            claims = jwt.decode(
                token,
                self._secret,
                algorithms=[_ALGORITHM],
                options={
                    "require": ["sub", "tid", "sid", "iat", "exp"],
                    # expiry is checked below against the injected clock, the
                    # single time source (drills freeze it, tests control it)
                    "verify_exp": False,
                    "verify_iat": False,
                },
            )
        except jwt.PyJWTError as exc:
            raise AuthenticationError("invalid or expired token") from exc
        if claims.get("typ") != "access":
            raise AuthenticationError("invalid or expired token")
        if int(self._clock.now().timestamp()) >= claims["exp"]:
            raise AuthenticationError("invalid or expired token")
        return claims


def new_refresh_token() -> str:
    return secrets.token_urlsafe(32)


def hash_refresh_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
