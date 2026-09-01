"""JWT access tokens, refresh-token material and socket tickets (REQ-010, REQ-033).

Access tokens are 15-minute HS256 JWTs referencing a session id, so the
session table doubles as the revocation store: a revoked session invalidates
the refresh token AND every outstanding access token at verification time.
Refresh tokens are 256-bit random secrets, stored only as sha256 hashes;
rotation and reuse detection are use-case logic over the session entity.
Security floor: ttl_s can only shorten the cap, never extend it.

Socket tickets are the third credential and the narrowest. They are signed with
the same secret but carry `typ: "ws_ticket"`, and both verifiers check the type -
so the two are not interchangeable in either direction, which is what makes a
ticket unusable as a bearer token however it leaks.
"""

from __future__ import annotations

import secrets
from typing import Any

import jwt

from signalforge.domain.identity.errors import AuthenticationError
from signalforge.domain.identity.ports import (
    AccessTokenService,
    Clock,
    SocketTicketService,
)
from signalforge.domain.identity.tokens import (  # noqa: F401  (re-export)
    hash_refresh_token,
    new_refresh_token,
)
from signalforge.infrastructure.config import ACCESS_TTL_CAP_S, SOCKET_TICKET_TTL_S

_ALGORITHM = "HS256"

ACCESS_TYPE = "access"
SOCKET_TICKET_TYPE = "ws_ticket"


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
            "typ": ACCESS_TYPE,
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
        if claims.get("typ") != ACCESS_TYPE:
            # A socket ticket is signed with the same secret and is deliberately
            # refused here: it is a handshake credential, not a bearer token.
            raise AuthenticationError("invalid or expired token")
        if int(self._clock.now().timestamp()) >= claims["exp"]:
            raise AuthenticationError("invalid or expired token")
        return claims


class JWTSocketTicketService(SocketTicketService):
    """Short-lived, single-use handshake tickets (REQ-033).

    Two things make it a credential rather than a URL parameter with extra
    steps. It is **typed**, so `JWTAccessTokenService.verify` refuses it and it
    can reach nothing but the socket. And it is **consumed**: a redeemed `jti` is
    remembered until the ticket would have expired anyway, so a captured
    handshake replayed a second later is refused.

    The replay set is in process, which is exactly the scope of the deployment
    this platform runs in (CLAUDE.md environment: one machine, no Docker) and is
    stated rather than hidden. On a multi-node install the set becomes a shared
    store; the property being enforced does not change, and the ticket's
    thirty-second life bounds the window even without one. The set is pruned on
    every redemption, so it cannot grow without bound.
    """

    def __init__(self, secret: str, clock: Clock, ttl_s: int = SOCKET_TICKET_TTL_S):
        if len(secret) < 32:
            raise ValueError("JWT secret must be at least 32 characters")
        self._secret = secret
        self._clock = clock
        self._ttl_s = min(int(ttl_s), SOCKET_TICKET_TTL_S)
        self._consumed: dict[str, int] = {}

    @property
    def ttl_s(self) -> int:
        return self._ttl_s

    def issue(self, *, user_id: str, tenant_id: str, session_id: str) -> str:
        now = int(self._clock.now().timestamp())
        claims = {
            "sub": user_id,
            "tid": tenant_id,
            "sid": session_id,
            "jti": secrets.token_hex(16),
            "iat": now,
            "exp": now + self._ttl_s,
            "typ": SOCKET_TICKET_TYPE,
        }
        return jwt.encode(claims, self._secret, algorithm=_ALGORITHM)

    def redeem(self, ticket: str) -> dict[str, Any]:
        try:
            claims = jwt.decode(
                ticket,
                self._secret,
                algorithms=[_ALGORITHM],
                options={
                    "require": ["sub", "tid", "sid", "jti", "iat", "exp"],
                    # Expiry against the injected clock, the single time source,
                    # exactly as the access token does it.
                    "verify_exp": False,
                    "verify_iat": False,
                },
            )
        except jwt.PyJWTError as exc:
            raise AuthenticationError("invalid or expired socket ticket") from exc
        if claims.get("typ") != SOCKET_TICKET_TYPE:
            # An access token presented as a ticket. Refused for the same reason
            # in the other direction: one credential, one door.
            raise AuthenticationError("invalid or expired socket ticket")
        now = int(self._clock.now().timestamp())
        self._prune(now)
        if now >= int(claims["exp"]):
            raise AuthenticationError("invalid or expired socket ticket")
        jti = str(claims["jti"])
        if jti in self._consumed:
            raise AuthenticationError("invalid or expired socket ticket")
        self._consumed[jti] = int(claims["exp"])
        return claims

    def _prune(self, now: int) -> None:
        expired = [jti for jti, exp in self._consumed.items() if exp <= now]
        for jti in expired:
            self._consumed.pop(jti, None)


