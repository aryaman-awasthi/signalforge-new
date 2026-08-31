"""Clock implementations (REQ-024).

Three of them, and which one a caller holds is a design decision, not a detail:

* SystemClock is wall time. Anything durable and operational - recorded_at,
  retention cutoffs, token expiry, offsets - reads this one, so a drill cannot
  make the platform expire sessions or delete real telemetry early.
* VirtualClock is scenario time. A drill compresses hours into minutes, and
  occurred_at follows it, because that is when things happened in the world the
  drill is simulating. It can be paused, stepped and re-sped while running.
* SwitchableClock is what the application layer holds. It points at wall time
  until a drill installs a virtual clock and reports which it is, so the cockpit
  can show that a timeline is simulated rather than real.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from signalforge.domain.identity.ports import Clock

MAX_VIRTUAL_SPEED = 100_000.0


class SystemClock(Clock):
    def now(self) -> datetime:
        return datetime.now(timezone.utc)


class VirtualClock(Clock):
    """Scenario time driven off a real clock, so it advances on its own when
    running and holds still when paused."""

    def __init__(self, wall: Clock, start_at: datetime, speed: float = 1.0):
        if start_at.tzinfo is None:
            raise ValueError("virtual clock start must be timezone-aware")
        if not 0 < speed <= MAX_VIRTUAL_SPEED:
            raise ValueError(f"speed must be in (0, {MAX_VIRTUAL_SPEED}]")
        self._wall = wall
        self._speed = float(speed)
        self._virtual_base = start_at
        self._wall_base = wall.now()
        self._paused = False

    @property
    def speed(self) -> float:
        return self._speed

    @property
    def paused(self) -> bool:
        return self._paused

    def now(self) -> datetime:
        if self._paused:
            return self._virtual_base
        elapsed = (self._wall.now() - self._wall_base).total_seconds()
        return self._virtual_base + timedelta(seconds=elapsed * self._speed)

    def pause(self) -> None:
        if not self._paused:
            self._rebase()
            self._paused = True

    def resume(self) -> None:
        if self._paused:
            self._wall_base = self._wall.now()
            self._paused = False

    def step(self, **delta) -> datetime:
        """Advance scenario time by hand - how a drill is stepped event by event."""
        self._rebase()
        self._virtual_base += timedelta(**delta)
        return self._virtual_base

    def set_speed(self, speed: float) -> None:
        if not 0 < speed <= MAX_VIRTUAL_SPEED:
            raise ValueError(f"speed must be in (0, {MAX_VIRTUAL_SPEED}]")
        self._rebase()
        self._speed = float(speed)

    def _rebase(self) -> None:
        """Freeze the time reached so far, so a speed or step change does not
        retroactively rewrite the scenario's history."""
        self._virtual_base = self.now()
        self._wall_base = self._wall.now()


class SwitchableClock(Clock):
    """The clock the application layer reads. Wall time until a drill takes over."""

    def __init__(self, wall: Optional[Clock] = None):
        self._wall = wall or SystemClock()
        self._active: Optional[VirtualClock] = None

    def now(self) -> datetime:
        return self._active.now() if self._active is not None else self._wall.now()

    def use(self, clock: VirtualClock) -> None:
        self._active = clock

    def reset(self) -> None:
        self._active = None

    @property
    def wall(self) -> Clock:
        return self._wall

    @property
    def is_virtual(self) -> bool:
        return self._active is not None

    @property
    def source(self) -> str:
        return "virtual" if self._active is not None else "wall"

    @property
    def speed(self) -> float:
        return self._active.speed if self._active is not None else 1.0

    @property
    def paused(self) -> bool:
        return self._active.paused if self._active is not None else False
