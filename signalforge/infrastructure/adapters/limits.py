"""Call budgets: the manifest's `max_calls_per_minute`, enforced (REQ-030).

A limit a manifest declares and nothing checks is a comment. It is enforced here
rather than inside an adapter because the budget belongs to the *binding*, not to
an adapter object: the health loop builds a fresh adapter every poll (credentials
must not be cached), so per-instance counting would count to one forever.

Refusing is deliberately not silent and not a wait. An adapter that blocks for a
minute inside an evidence-gathering call turns a rate limit into a timeout and an
UNCERTAIN outcome; refusing immediately with a typed error lets the caller
degrade, and the connector health loop reports it as a real observation.
"""

from __future__ import annotations

from collections import deque
from typing import Callable

from signalforge.contracts.errors import AdapterError

WINDOW_S = 60.0


class RateLimitExceeded(AdapterError):
    """The binding used its declared per-minute call budget."""


class CallBudget:
    """Sliding one-minute windows keyed by binding (or by adapter for calls that
    do not have one). In-process, like the login limiter, which is right for the
    single-node deployment and swappable behind the same three methods."""

    def __init__(self, now: Callable[[], float]):
        self._now = now
        self._calls: dict[str, deque[float]] = {}

    def charge(self, key: str, limit: int) -> None:
        now = self._now()
        window = self._calls.setdefault(key, deque())
        while window and now - window[0] > WINDOW_S:
            window.popleft()
        if len(window) >= limit:
            retry_after = max(1, int(WINDOW_S - (now - window[0])))
            raise RateLimitExceeded(
                f"call budget of {limit}/minute for {key} is spent; "
                f"retry in {retry_after}s"
            )
        window.append(now)

    def used(self, key: str) -> int:
        now = self._now()
        window = self._calls.get(key, deque())
        while window and now - window[0] > WINDOW_S:
            window.popleft()
        return len(window)

    def reset(self, key: str) -> None:
        self._calls.pop(key, None)
