"""Sliding-window login rate limiting (REQ-010).

In-process state is right for the one-machine deployment (CLAUDE.md
environment); the port keeps it swappable for a shared store later.
"""

from __future__ import annotations

from collections import deque

from signalforge.domain.identity.errors import RateLimitedError
from signalforge.domain.identity.ports import Clock, RateLimiter


class SlidingWindowRateLimiter(RateLimiter):
    def __init__(self, limit: int, window_s: int, clock: Clock):
        self._limit = limit
        self._window_s = window_s
        self._clock = clock
        self._attempts: dict[str, deque[float]] = {}

    def hit(self, key: str) -> None:
        now = self._clock.now().timestamp()
        window = self._attempts.setdefault(key, deque())
        while window and now - window[0] > self._window_s:
            window.popleft()
        if len(window) >= self._limit:
            retry_after = max(1, int(self._window_s - (now - window[0])))
            raise RateLimitedError("too many attempts", retry_after_s=retry_after)
        window.append(now)

    def reset(self, key: str) -> None:
        self._attempts.pop(key, None)
