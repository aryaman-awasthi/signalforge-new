"""S1-T02/T05: ingest HMAC verification per the frozen ingest contract (REQ-017)."""

import hashlib
import hmac as hmac_mod

import pytest

from signalforge.contracts.ingest import REPLAY_WINDOW_S
from signalforge.infrastructure.security.hmac_verify import IngestHmacVerifier

SECRET = "shared-secret"
BODY = b'{"source_system":"ref-harness","items":[]}'


def sign(secret: str, timestamp: int, body: bytes) -> str:
    message = f"{timestamp}.".encode() + body
    return hmac_mod.new(secret.encode(), message, hashlib.sha256).hexdigest()


def ts(clock) -> int:
    return int(clock.now().timestamp())


@pytest.mark.req("REQ-017")
def test_valid_signature_within_window_accepted(clock):
    verifier = IngestHmacVerifier(clock=clock)
    result = verifier.verify(
        secret=SECRET, timestamp=str(ts(clock)), body=BODY,
        signature=sign(SECRET, ts(clock), BODY),
    )
    assert result.ok, result.reason


@pytest.mark.req("REQ-017")
def test_tampered_body_rejected(clock):
    verifier = IngestHmacVerifier(clock=clock)
    result = verifier.verify(
        secret=SECRET, timestamp=str(ts(clock)), body=BODY + b" ",
        signature=sign(SECRET, ts(clock), BODY),
    )
    assert not result.ok and "signature" in result.reason


@pytest.mark.req("REQ-017")
def test_forged_signature_rejected(clock):
    verifier = IngestHmacVerifier(clock=clock)
    result = verifier.verify(
        secret=SECRET, timestamp=str(ts(clock)), body=BODY,
        signature=sign("guessed-secret", ts(clock), BODY),
    )
    assert not result.ok


@pytest.mark.req("REQ-017")
def test_stale_and_future_timestamps_rejected(clock):
    verifier = IngestHmacVerifier(clock=clock)
    for skew in (-(REPLAY_WINDOW_S + 1), REPLAY_WINDOW_S + 1):
        stale = ts(clock) + skew
        result = verifier.verify(
            secret=SECRET, timestamp=str(stale), body=BODY,
            signature=sign(SECRET, stale, BODY),
        )
        assert not result.ok and "window" in result.reason
    edge = ts(clock) - REPLAY_WINDOW_S
    result = verifier.verify(
        secret=SECRET, timestamp=str(edge), body=BODY,
        signature=sign(SECRET, edge, BODY),
    )
    assert result.ok, "the window boundary itself is inside the window"


@pytest.mark.req("REQ-017")
def test_malformed_timestamp_rejected(clock):
    verifier = IngestHmacVerifier(clock=clock)
    result = verifier.verify(
        secret=SECRET, timestamp="not-a-number", body=BODY,
        signature=sign(SECRET, ts(clock), BODY),
    )
    assert not result.ok
