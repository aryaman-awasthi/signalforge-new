"""Turning what was said on the bridge into work with an owner and a due time
(REQ-045).

Bridges lose commitments. Somebody says "I'll roll back region-b in ten minutes",
four other things happen, and forty minutes later nobody has rolled anything back
and everybody assumed somebody had. This module finds those sentences and makes
them tasks.

It is deliberately deterministic and deliberately conservative, and both choices
are load-bearing:

- **Deterministic**, because S5's model will do this better and this has to keep
  working when the model is down. A commitment found by a regular expression is
  found the same way every time, in a test, on a virtual clock, with no network.
- **Conservative**, because a false positive is worse than a miss. A task that
  nobody agreed to sits on the board with somebody's name against it, and the
  cost of arguing about it during a P0 exceeds the value of catching one more
  commitment. So a sentence has to contain an explicit commitment verb aimed at
  an explicit actor; "we should probably look at the pool" is not a commitment
  and is not harvested.

What it never does is *guess an owner*. "Priya will check the pool" produces a
task labelled `Priya` and owned by nobody until a person resolves it, because a
user id assigned from a first name is how the wrong Priya gets paged.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Mapping, Optional, Sequence

MAX_TITLE_CHARS = 200
MAX_LOOKAHEAD_H = 24
"""A due time further out than this is not an incident commitment. "By Friday"
during a P0 is a project, and putting it on the incident board buries the things
that matter in the next twenty minutes."""

FIRST_PERSON = re.compile(r"^(?:i|we)(?:'ll)?$", re.I)

PROMISE = re.compile(
    r"""
    (?P<actor>
        (?i:\b(?:i|we)\b)
      | \b(?P<name>[A-Z][a-z]{1,20}(?:\s+[A-Z][a-z]{1,20})?)\b
    )
    \s*
    (?:
        '?ll\s+ | \s+will\s+ | \s+(?:is|am|are)\s+going\s+to\s+
    )
    (?P<what>[^.!?\n]{4,200})
    """,
    re.VERBOSE,
)
"""Somebody saying they will do something.

Only the three verb forms that are actually a promise. `can` is deliberately
absent: "we can roll back" is an option being discussed, and a board full of
options nobody committed to is a board people stop reading."""

ASSIGN = re.compile(
    r"@(?P<handle>[A-Za-z0-9._\-]{2,40})\s+(?:to|will|'?ll)\s+(?P<what>[^.!?\n]{4,200})"
)
"""The bridge idiom for handing work over: `@leo to drain the queue`.

Bare `to` is accepted only after an `@handle`, because a handle is unambiguously
a person. Allowing it after a capitalised word would turn "Moving to the
secondary" into a task called "the secondary" owned by somebody named Moving."""

PREDICTION = re.compile(r"^(?:be|not|probably|still|already)\b", re.I)
"""What a promise never starts with. "Payments will be down for a while" is a
forecast about a system, not a commitment by a person, and the difference is
the word right after the verb."""

TAKING = re.compile(
    r"^\s*(?:@(?P<handle>[A-Za-z0-9._\-]{2,40})\s+)?"
    r"(?:i(?:'m| am)?\s+)?(?:taking|owning|picking\s+up|on)\s+"
    r"(?P<what>[^.!?\n]{3,200})",
    re.I,
)

DUE_PATTERNS: tuple[tuple[re.Pattern, str], ...] = (
    (re.compile(r"\bin\s+(?P<n>\d{1,3})\s*(?:m|min|mins|minutes?)\b", re.I), "minutes"),
    (re.compile(r"\bin\s+(?P<n>\d{1,2})\s*(?:h|hr|hrs|hours?)\b", re.I), "hours"),
    (
        re.compile(r"\bwithin\s+(?P<n>\d{1,3})\s*(?:m|min|mins|minutes?)\b", re.I),
        "minutes",
    ),
    (re.compile(r"\bwithin\s+(?P<n>\d{1,2})\s*(?:h|hr|hrs|hours?)\b", re.I), "hours"),
    (
        re.compile(r"\bby\s+(?P<h>[01]?\d|2[0-3]):(?P<m>[0-5]\d)\b"),
        "clock",
    ),
    (re.compile(r"\bnext\s+(?P<n>\d{1,3})\s*(?:m|min|mins|minutes?)\b", re.I), "minutes"),
)

SOON = re.compile(r"\b(?:right\s+away|straight\s+away|immediately|now|asap)\b", re.I)
SOON_MINUTES = 5

NOT_A_COMMITMENT = re.compile(
    r"\b(?:should\s+we|shall\s+we|could\s+we|maybe|perhaps|might|"
    r"does\s+anyone|who\s+can|any\s+idea|thoughts\?)\b",
    re.I,
)
"""Questions and suggestions. A bridge is mostly these, and turning "should we
roll back?" into a rollback task assigned to the person who asked is exactly the
sort of confident wrongness that makes people stop trusting the board."""

FILLER = re.compile(r"^(?:ok|okay|yes|no|thanks|ack|acknowledged|\+1|got\s+it)\b", re.I)


@dataclass(frozen=True)
class Commitment:
    """One promise, with everything needed to make it a task and to defend it.

    `evidence` is the message verbatim. When somebody disputes a task at the
    review, the answer is what was actually said, not this module's paraphrase
    of it.
    """

    key: str
    title: str
    said_by: str
    said_at: datetime
    evidence: str
    message_id: str = ""
    owner_hint: str = ""
    """The handle or name the sentence aimed at, unresolved. Empty when the
    speaker committed themselves, in which case `said_by` is the owner."""
    first_person: bool = False
    due_at: Optional[datetime] = None
    due_label: str = ""

    @property
    def owner_is_speaker(self) -> bool:
        return self.first_person and not self.owner_hint


def harvest(
    messages: Sequence[Mapping[str, object]],
    *,
    now: datetime,
    since: Optional[datetime] = None,
    not_people: Sequence[str] = (),
) -> tuple[Commitment, ...]:
    """Read a transcript and return the commitments in it.

    Pure: no clock of its own, no store, no notion of users. `now` is only used
    to resolve relative due times, and `since` lets a resumable harvest skip what
    it has already read - which is what stops a restart from raising every
    commitment in the transcript a second time.

    `not_people` is the caller's list of names that are systems rather than
    humans: the application id and the topology's service names. Without it,
    "Payments will recover once the pool drains" produces a task owned by
    somebody called Payments. The domain cannot know which capitalised words are
    services, and inventing a list here would be a guess; the caller genuinely
    knows, so it tells us.

    Keys are derived from the message id and the matched text, so harvesting the
    same transcript twice produces the same keys and the caller can make the
    write idempotent without keeping a cursor of its own.
    """
    excluded = {str(name).strip().lower() for name in not_people if str(name).strip()}
    found: list[Commitment] = []
    seen: set[str] = set()
    for message in messages:
        at = _at_of(message)
        if at is None or (since is not None and at <= since):
            continue
        text = str(message.get("text") or "").strip()
        if not text or FILLER.match(text) or NOT_A_COMMITMENT.search(text):
            continue
        commitment = _commitment_in(message, text, at=at, now=now, excluded=excluded)
        if commitment is None or commitment.key in seen:
            continue
        seen.add(commitment.key)
        found.append(commitment)
    return tuple(found)


def _commitment_in(
    message: Mapping[str, object],
    text: str,
    *,
    at: datetime,
    now: datetime,
    excluded: set[str],
) -> Optional[Commitment]:
    author = str(message.get("author") or message.get("author_id") or "").strip()
    message_id = str(message.get("message_id") or "")

    taking = TAKING.match(text)
    if taking is not None:
        return _build(
            text=text,
            what=taking.group("what"),
            author=author,
            message_id=message_id,
            at=at,
            now=now,
            owner_hint=taking.group("handle") or "",
            first_person=not taking.group("handle"),
            excluded=excluded,
        )

    assigned = ASSIGN.search(text)
    if assigned is not None:
        return _build(
            text=text,
            what=assigned.group("what"),
            author=author,
            message_id=message_id,
            at=at,
            now=now,
            owner_hint=assigned.group("handle"),
            first_person=False,
            excluded=excluded,
        )

    promise = PROMISE.search(text)
    if promise is None:
        return None
    name = (promise.group("name") or "").strip()
    first_person = not name and bool(FIRST_PERSON.match(promise.group("actor").strip()))
    if not first_person and not name:
        return None
    return _build(
        text=text,
        what=promise.group("what"),
        author=author,
        message_id=message_id,
        at=at,
        now=now,
        owner_hint="" if first_person else name,
        first_person=first_person,
        excluded=excluded,
    )


def _build(
    *,
    text: str,
    what: str,
    author: str,
    message_id: str,
    at: datetime,
    now: datetime,
    owner_hint: str,
    first_person: bool,
    excluded: set[str],
) -> Optional[Commitment]:
    if owner_hint and owner_hint.strip().lower() in excluded:
        # A service, not a person. The sentence is a forecast about a system.
        return None
    if PREDICTION.match(what.strip()):
        return None
    title = _title(what)
    if not title:
        return None
    due_at, due_label = due_time(text, now=now)
    return Commitment(
        key=_key(message_id, title, author),
        title=title,
        said_by=author,
        said_at=at,
        evidence=text[:500],
        message_id=message_id,
        owner_hint=owner_hint,
        first_person=first_person,
        due_at=due_at,
        due_label=due_label,
    )


def due_time(text: str, *, now: datetime) -> tuple[Optional[datetime], str]:
    """When the sentence says it will be done, if it says at all.

    A commitment with no time is still a task - "I'll roll back region-b" is
    work somebody owns - it simply has no countdown. Inventing a due time would
    make the board show overdue work nobody promised.
    """
    for pattern, unit in DUE_PATTERNS:
        match = pattern.search(text)
        if match is None:
            continue
        if unit == "clock":
            hour, minute = int(match.group("h")), int(match.group("m"))
            due = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if due <= now:
                due = due + timedelta(days=1)
            if due - now > timedelta(hours=MAX_LOOKAHEAD_H):
                return None, ""
            return due, f"by {hour:02d}:{minute:02d}"
        amount = int(match.group("n"))
        delta = (
            timedelta(minutes=amount) if unit == "minutes" else timedelta(hours=amount)
        )
        if delta > timedelta(hours=MAX_LOOKAHEAD_H):
            return None, ""
        return now + delta, f"in {amount} {unit}"
    if SOON.search(text):
        return now + timedelta(minutes=SOON_MINUTES), "right away"
    return None, ""


def _title(what: str) -> str:
    title = re.sub(r"\s+", " ", what).strip(" ,;:-")
    # Strip a trailing due phrase: it is on the task's own due field, and
    # repeating it in the title makes the board read like a transcript.
    for pattern, _unit in DUE_PATTERNS:
        title = pattern.sub("", title).strip(" ,;:-")
    title = SOON.sub("", title).strip(" ,;:-")
    if len(title) < 4:
        return ""
    return title[:MAX_TITLE_CHARS]


def _key(message_id: str, title: str, author: str) -> str:
    """Stable across restarts and re-reads: the same sentence yields the same
    key, which is what makes harvesting idempotent without a cursor.

    Deliberately *not* over the author. A message id already identifies one
    utterance uniquely - two people cannot produce the same one - so adding the
    author buys no uniqueness, and it would break the property the key exists
    for: the same message read by two paths that disagree about who said it
    (the poster knows the session; a later sweep sees only the connector's own
    identity, because the frozen collaboration SPI carries no author) would hash
    to two keys and raise the same commitment twice.
    """
    digest = hashlib.sha256(
        f"{message_id}|{title.lower()}".encode("utf-8")
    ).hexdigest()
    return f"c-{digest[:20]}"


def _at_of(message: Mapping[str, object]) -> Optional[datetime]:
    value = message.get("at") or message.get("sent_at")
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
    return None
