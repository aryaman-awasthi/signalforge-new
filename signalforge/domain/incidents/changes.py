"""Change windows: a deployment that started and never said it finished (REQ-044).

The signal is not "somebody deployed". It is **`deployment.started` with no
`deployment.completed`** - a change that is, right now, in flight. That is the
one fact worth putting on a chip beside a health score, because it changes what
a responder does next: the first question during an incident is "what changed",
and the answer "something is changing *now*" outranks every entry in a history.

Everything here is pure. A window is folded from the canonical `ChangeEvent`s
that arrived through the ingest API, and the fold has to survive the three things
a real deployment pipeline does:

- **duplicates.** A re-triggered pipeline announces the same change again. The
  fold is idempotent: applying the same event twice produces an equal window.
- **out-of-order delivery.** A `completed` can be recorded before its `started`,
  and a window built by ordering events would then reopen a finished deployment.
  Nothing here reads the arrival order: `started_at` takes the earliest seen and
  a completion, once seen, is never unseen.
- **silence.** A pipeline that crashes between the two calls leaves a window
  open forever. It is not closed on a guess - nobody knows whether the deploy
  landed - but past `CHANGE_WINDOW_MAX_S` it is marked `stale`, so a chip can
  say "started 3 days ago and never announced completion" instead of implying a
  deployment is in progress.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from typing import Optional, Sequence

from signalforge.contracts.canonical import ChangeEvent, ChangeStatus, ChangeType

CHANGE_WINDOW_MAX_S = 4 * 3_600.0
"""How long an unfinished change is believed to still be running. Four hours is
longer than any sane deployment and shorter than a working day, so a window that
outlives it is a pipeline that stopped talking rather than a slow release."""

RECENT_WINDOW_S = 24 * 3_600.0
"""How far back closed changes are still worth showing beside an application.
"What changed today" is the question; a month of history is a different screen."""

TERMINAL_STATUSES: frozenset[ChangeStatus] = frozenset(
    {ChangeStatus.COMPLETED, ChangeStatus.FAILED, ChangeStatus.ROLLED_BACK}
)
"""All three end the window. A failed or rolled-back deployment is no longer in
flight - and it is *more* interesting as evidence, not less, which is why the
window records which of the three it was rather than collapsing them."""


@dataclass(frozen=True)
class ChangeWindow:
    """One change, folded from every event that named it."""

    tenant_id: str
    app_id: str
    change_id: str
    change_type: ChangeType
    service: str
    status: ChangeStatus
    started_at: datetime
    first_seen_at: datetime
    last_event_at: datetime
    completed_at: Optional[datetime] = None
    version: Optional[str] = None
    actor: Optional[str] = None
    diff_ref: Optional[str] = None
    source_system: str = ""
    simulated: bool = False
    announced_started: bool = False
    announced_completed: bool = False

    @property
    def open(self) -> bool:
        """Started and never announced as finished. This is the chip."""
        return not self.announced_completed

    def age_s(self, now: datetime) -> float:
        return max((now - self.started_at).total_seconds(), 0.0)

    def duration_s(self) -> Optional[float]:
        if self.completed_at is None:
            return None
        return max((self.completed_at - self.started_at).total_seconds(), 0.0)

    def stale(self, now: datetime, *, max_age_s: float = CHANGE_WINDOW_MAX_S) -> bool:
        return self.open and self.age_s(now) > max_age_s

    def detail(self, now: datetime, *, max_age_s: float = CHANGE_WINDOW_MAX_S) -> str:
        who = f" by {self.actor}" if self.actor else ""
        what = f"{self.change_type.value} {self.version or self.change_id}"
        if not self.open:
            duration = self.duration_s()
            took = f" after {duration:.0f}s" if duration is not None else ""
            return f"{what} on {self.service} {self.status.value}{took}{who}"
        age = self.age_s(now)
        if self.stale(now, max_age_s=max_age_s):
            return (
                f"{what} on {self.service} started {age / 3600:.1f}h ago{who} and "
                "never announced completion; the window is stale rather than open"
            )
        if not self.announced_started:
            return (
                f"{what} on {self.service} was announced by a completion event "
                "whose start was never seen; treated as in flight until it is"
            )
        return f"{what} on {self.service} started {age:.0f}s ago{who} and is still running"


@dataclass(frozen=True)
class ChangeWindowView:
    """What an application's change chip and its recent-changes list read."""

    app_id: str
    at: datetime
    open: tuple[ChangeWindow, ...] = ()
    recent: tuple[ChangeWindow, ...] = ()
    stale: tuple[ChangeWindow, ...] = ()

    @property
    def change_window_open(self) -> bool:
        """The chip is shown for a window that is genuinely in flight. A stale
        one is listed and explained, but it does not light the chip - a chip
        that is on for three days is a chip people stop seeing."""
        return bool(self.open)

    @property
    def detail(self) -> str:
        if self.open:
            return (
                f"{len(self.open)} change(s) in flight: "
                + "; ".join(window.detail(self.at) for window in self.open)
            )
        if self.stale:
            return (
                f"{len(self.stale)} change(s) started and never completed: "
                + "; ".join(window.detail(self.at) for window in self.stale)
            )
        if self.recent:
            return f"no change in flight; {len(self.recent)} completed recently"
        return "no change in flight"


def merge_change(
    existing: Optional[ChangeWindow],
    event: ChangeEvent,
    *,
    tenant_id: str,
    app_id: str,
    at: datetime,
    source_system: str = "",
    simulated: bool = False,
) -> ChangeWindow:
    """Fold one canonical change event into the window it belongs to.

    Idempotent by construction: every field either takes an extreme (the
    earliest start, the first completion), sticks once set, or prefers the
    event's own non-empty value over the stored one, which is stable when the
    same event arrives twice.
    """
    terminal = event.status in TERMINAL_STATUSES
    completed_at = (event.completed_at or at) if terminal else None

    if existing is None:
        return ChangeWindow(
            tenant_id=tenant_id,
            app_id=app_id,
            change_id=event.change_id,
            change_type=event.change_type,
            service=event.service,
            status=event.status,
            started_at=event.started_at,
            first_seen_at=at,
            last_event_at=at,
            completed_at=completed_at,
            version=event.version,
            actor=event.actor,
            diff_ref=event.diff_ref,
            source_system=source_system,
            simulated=simulated,
            announced_started=event.status is ChangeStatus.STARTED,
            announced_completed=terminal,
        )

    return replace(
        existing,
        # The earliest start wins: a duplicate announcement must not move the
        # clock the window's age - and therefore its staleness - is measured from.
        started_at=min(existing.started_at, event.started_at),
        first_seen_at=min(existing.first_seen_at, at),
        last_event_at=max(existing.last_event_at, at),
        # A completion, once seen, is never unseen. A `started` arriving after a
        # `completed` is late delivery, not a second deployment.
        status=existing.status if existing.announced_completed else event.status,
        completed_at=existing.completed_at or completed_at,
        announced_started=existing.announced_started or event.status is ChangeStatus.STARTED,
        announced_completed=existing.announced_completed or terminal,
        service=event.service or existing.service,
        change_type=event.change_type or existing.change_type,
        version=event.version or existing.version,
        actor=event.actor or existing.actor,
        diff_ref=event.diff_ref or existing.diff_ref,
        source_system=source_system or existing.source_system,
        simulated=existing.simulated or simulated,
    )


def window_view(
    windows: Sequence[ChangeWindow],
    *,
    app_id: str,
    at: datetime,
    max_age_s: float = CHANGE_WINDOW_MAX_S,
    recent_s: float = RECENT_WINDOW_S,
) -> ChangeWindowView:
    """Split what is stored into in-flight, stale and recently finished."""
    live: list[ChangeWindow] = []
    stale: list[ChangeWindow] = []
    recent: list[ChangeWindow] = []
    for window in windows:
        if window.open:
            (stale if window.stale(at, max_age_s=max_age_s) else live).append(window)
        elif window.age_s(at) <= recent_s:
            recent.append(window)
    order = lambda w: (w.started_at, w.change_id)  # noqa: E731
    return ChangeWindowView(
        app_id=app_id,
        at=at,
        open=tuple(sorted(live, key=order, reverse=True)),
        recent=tuple(sorted(recent, key=order, reverse=True)),
        stale=tuple(sorted(stale, key=order, reverse=True)),
    )
