"""S1-T02: access-token expiry, hashing, refresh material (REQ-010).

Rotation and reuse-detection flow tests live in test_auth_flows.py once the
use cases exist (S1-T03); this file pins the token primitives.
"""

import pytest

from signalforge.domain.identity.errors import AuthenticationError
from signalforge.infrastructure.security.passwords import Argon2PasswordHasher
from signalforge.infrastructure.security.tokens import (
    ACCESS_TTL_CAP_S,
    JWTAccessTokenService,
    hash_refresh_token,
    new_refresh_token,
)


@pytest.mark.req("REQ-010")
def test_argon2id_hashing_roundtrip():
    hasher = Argon2PasswordHasher()
    digest = hasher.hash("correct horse battery staple")
    assert digest.startswith("$argon2id$"), "argon2id variant is required"
    assert hasher.verify("correct horse battery staple", digest)
    assert not hasher.verify("wrong password", digest)
    assert not hasher.verify("wrong password", "not-a-hash")


@pytest.mark.req("REQ-010")
def test_access_token_carries_session_and_expires_in_15_minutes(clock):
    service = JWTAccessTokenService(secret="s" * 32, clock=clock)
    token = service.issue(user_id="u1", tenant_id="t1", session_id="s1")
    claims = service.verify(token)
    assert claims["sub"] == "u1"
    assert claims["tid"] == "t1"
    assert claims["sid"] == "s1"
    assert claims["exp"] - claims["iat"] == 900

    clock.advance(minutes=14)
    service.verify(token)  # still valid
    clock.advance(minutes=2)
    with pytest.raises(AuthenticationError):
        service.verify(token)


@pytest.mark.req("REQ-010")
def test_access_ttl_cannot_be_raised_above_the_cap(clock):
    service = JWTAccessTokenService(secret="s" * 32, clock=clock, ttl_s=86_400)
    token = service.issue(user_id="u1", tenant_id="t1", session_id="s1")
    claims = service.verify(token)
    assert claims["exp"] - claims["iat"] <= ACCESS_TTL_CAP_S, (
        "security never weakens: config cannot extend the access TTL"
    )


@pytest.mark.req("REQ-010")
def test_tampered_or_foreign_tokens_rejected(clock):
    service = JWTAccessTokenService(secret="s" * 32, clock=clock)
    other = JWTAccessTokenService(secret="x" * 32, clock=clock)
    token = service.issue(user_id="u1", tenant_id="t1", session_id="s1")
    with pytest.raises(AuthenticationError):
        service.verify(token[:-2] + "zz")
    with pytest.raises(AuthenticationError):
        service.verify(other.issue(user_id="u1", tenant_id="t1", session_id="s1"))
    with pytest.raises(AuthenticationError):
        service.verify("not.a.jwt")


@pytest.mark.req("REQ-010")
def test_refresh_tokens_are_random_and_stored_only_hashed():
    a, b = new_refresh_token(), new_refresh_token()
    assert a != b and len(a) >= 43, "at least 256 bits of randomness"
    digest = hash_refresh_token(a)
    assert digest != a and len(digest) == 64, "sha256 hex digest, never the raw token"
    assert hash_refresh_token(a) == digest, "deterministic so lookup by hash works"
