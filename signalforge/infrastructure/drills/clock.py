"""Scenario time for a drill (REQ-070, REQ-024).

The platform already has three clocks (infrastructure/clock.py). A drill needs
one more property from whichever it holds: the ability to be *moved*, so that an
approval TTL, a stability window and an escalation countdown are exercised in
milliseconds without any of them being weakened.

``SteppedDrillClock`` wraps any object exposing a mutable ``current`` datetime -
which is what a container's clock is in a drill - and moves it. It is the only
place a drill is allowed to touch time, and it never touches wall time, so
retention, token expiry and recorded_at stay honest.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from signalforge.application.drills.ports import DrillClock


class SteppedDrillClock(DrillClock):
    """Scenario time held still and moved by hand."""

    def __init__(self, clock):
        if not hasattr(clock, "current"):
            raise TypeError(
                "a drill clock must expose a mutable `current`; "
                "wall time is never moved by a drill"
            )
        self._clock = clock
        self._paused = False
        self._speed = 1.0

    def now(self) -> datetime:
        return self._clock.current

    def advance(self, seconds: float) -> None:
        if self._paused or seconds <= 0:
            return
        self._clock.current = self._clock.current + timedelta(
            seconds=seconds * self._speed
        )

    def pause(self) -> None:
        self._paused = True

    def resume(self) -> None:
        self._paused = False

    def set_speed(self, speed: float) -> None:
        if speed <= 0:
            raise ValueError("drill speed must be positive")
        self._speed = float(speed)

    @property
    def paused(self) -> bool:
        return self._paused

    @property
    def speed(self) -> float:
        return self._speed
