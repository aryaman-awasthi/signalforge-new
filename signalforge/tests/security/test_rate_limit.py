"""S1-T02: login rate limiting (REQ-010)."""

import pytest

from signalforge.domain.identity.errors import RateLimitedError
from signalforge.infrastructure.security.ratelimit import SlidingWindowRateLimiter


@pytest.mark.req("REQ-010")
def test_sixth_attempt_in_window_is_blocked(clock):
    limiter = SlidingWindowRateLimiter(limit=5, window_s=300, clock=clock)
    for _ in range(5):
        limiter.hit("login:t1:a@b.co")
    with pytest.raises(RateLimitedError) as excinfo:
        limiter.hit("login:t1:a@b.co")
    assert 0 < excinfo.value.retry_after_s <= 300


@pytest.mark.req("REQ-010")
def test_window_slides_and_attempts_expire(clock):
    limiter = SlidingWindowRateLimiter(limit=5, window_s=300, clock=clock)
    for _ in range(5):
        limiter.hit("k")
    clock.advance(seconds=301)
    limiter.hit("k")  # old attempts aged out


@pytest.mark.req("REQ-010")
def test_keys_are_independent_and_reset_clears(clock):
    limiter = SlidingWindowRateLimiter(limit=5, window_s=300, clock=clock)
    for _ in range(5):
        limiter.hit("a")
    limiter.hit("b")  # different key unaffected
    limiter.reset("a")
    limiter.hit("a")  # reset (successful login) clears the window
