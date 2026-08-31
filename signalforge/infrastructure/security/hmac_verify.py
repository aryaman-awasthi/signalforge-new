"""Ingest HMAC verification (REQ-017), exactly per the frozen ingest contract:
HMAC-SHA256 over "<timestamp>.<raw body>" within the replay window. Built in
S1 as part of the security floor; the ingest routers adopt it in S3."""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from typing import Optional, Union

from signalforge.contracts.ingest import REPLAY_WINDOW_S
from signalforge.domain.identity.ports import Clock


@dataclass(frozen=True)
class VerificationResult:
    ok: bool
    reason: Optional[str] = None


class IngestHmacVerifier:
    def __init__(self, clock: Clock, replay_window_s: int = REPLAY_WINDOW_S):
        self._clock = clock
        # Security floor: config may only tighten the contract window.
        self._replay_window_s = min(replay_window_s, REPLAY_WINDOW_S)

    def verify(
        self,
        *,
        secret: Union[str, bytes],
        timestamp: str,
        body: bytes,
        signature: str,
    ) -> VerificationResult:
        try:
            ts = int(timestamp)
        except (TypeError, ValueError):
            return VerificationResult(False, "malformed timestamp")

        now = int(self._clock.now().timestamp())
        if abs(now - ts) > self._replay_window_s:
            return VerificationResult(False, "timestamp outside replay window")

        key = secret.encode("utf-8") if isinstance(secret, str) else secret
        expected = hmac.new(
            key, f"{ts}.".encode("utf-8") + body, hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(expected, signature.lower()):
            return VerificationResult(False, "signature mismatch")
        return VerificationResult(True)
