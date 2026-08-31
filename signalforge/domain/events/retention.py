"""Retention policy for the two planes (REQ-023).

The signal plane carries telemetry: metric samples, health polls, log lines. It
is high volume and only interesting while it is recent, so it ages out on a
configured window. The incident plane carries what an incident was: declarations,
evidence, decisions, actions, communications. It is kept, and sealed.

Two rules are not configurable, which is why they live here rather than in
config: the incident plane is never pruned, and the window cannot be shortened
below one day or stretched past ninety. A deployment can tune retention inside
those bounds; it cannot turn the platform into one that forgets an incident.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from signalforge.contracts.envelope import Plane

SIGNAL_RETENTION_DEFAULT_DAYS = 7
SIGNAL_RETENTION_MIN_DAYS = 1
SIGNAL_RETENTION_MAX_DAYS = 90

PRUNED_TOPIC = "system.retention.pruned"


@dataclass(frozen=True)
class RetentionPolicy:
    signal_retention_days: int = SIGNAL_RETENTION_DEFAULT_DAYS

    def __post_init__(self) -> None:
        if not (
            SIGNAL_RETENTION_MIN_DAYS
            <= self.signal_retention_days
            <= SIGNAL_RETENTION_MAX_DAYS
        ):
            raise ValueError(
                "signal retention must be between "
                f"{SIGNAL_RETENTION_MIN_DAYS} and {SIGNAL_RETENTION_MAX_DAYS} days"
            )

    def applies_to(self, plane: Plane) -> bool:
        """Only the signal plane ages out. There is deliberately no branch that
        could ever return True for the incident plane."""
        return plane is Plane.SIGNAL

    def cutoff(self, now: datetime) -> datetime:
        """Events recorded before this wall-clock instant may be pruned. Wall
        clock, always: a drill's virtual clock must not age out real data."""
        return now - timedelta(days=self.signal_retention_days)
