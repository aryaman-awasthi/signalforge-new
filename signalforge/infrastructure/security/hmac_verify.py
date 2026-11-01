"""Ingest HMAC verification (REQ-017), exactly per the frozen ingest contract:
HMAC-SHA256 over "<timestamp>.<raw body>" within the replay window. Built in
S1 as part of the security floor; the ingest routers adopt it in S3."""

from __future__ import annotations

import hashlib
import hmac
import re
from dataclasses import dataclass
from typing import Optional, Union

from signalforge.contracts.ingest import REPLAY_WINDOW_S
from signalforge.domain.identity.ports import Clock


_HEX_DIGEST = re.compile(r"[0-9a-f]{64}")
"""The only shape an HMAC-SHA256 hex digest has."""


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

        presented = str(signature or "").strip().lower()
        if not _HEX_DIGEST.fullmatch(presented):
            # Checked before the comparison: `compare_digest` refuses strings
            # with non-ASCII characters by raising, and a header is attacker
            # input. A signature that is not a hex digest is simply wrong.
            return VerificationResult(False, "malformed signature")

        key = secret.encode("utf-8") if isinstance(secret, str) else secret
        expected = hmac.new(
            key, f"{ts}.".encode("utf-8") + body, hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(expected, presented):
            return VerificationResult(False, "signature mismatch")
        return VerificationResult(True)
