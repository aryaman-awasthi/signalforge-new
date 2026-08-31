"""Wall-clock implementation of the Clock port. Drills swap this for a
virtual clock (S2/S7) without touching the code that reads time."""

from __future__ import annotations

from datetime import datetime, timezone

from signalforge.domain.identity.ports import Clock


class SystemClock(Clock):
    def now(self) -> datetime:
        return datetime.now(timezone.utc)
