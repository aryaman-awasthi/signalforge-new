"""The collaboration script player (REQ-033).

teams-sim is a simulated bridge, and a simulated bridge has to do two things at
once: replay a written conversation so a drill has something to react to, and
carry whatever a real person types into it now. Both end up as messages in the
same channel, distinguishable by one flag, because a war room that separated
"scripted" and "live" into two panes would rehearse nothing useful.

The player is pure and materialising rather than streaming. When a channel with
a script is first touched, every scripted line is written at
`epoch + after_s` - future timestamps included - and reads then simply window by
time. That has three consequences worth having:

- **It follows whatever clock the platform holds.** On a drill's virtual clock
  at 100x, the conversation arrives at 100x with no code that knows about drills.
- **It is idempotent.** Message ids are derived from the channel and the line's
  position, so replaying a script, restarting the process or two workers racing
  each other produce one conversation, not three.
- **It survives a restart**, because the schedule is rows rather than timers.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta
from typing import Any, Mapping, Optional, Sequence

from signalforge.contracts.envelope import ActorType
from signalforge.domain.integrations.entities import ScriptedMessage, StoredMessage
from signalforge.domain.shared.errors import DomainValidationError

MAX_SCRIPT_ENTRIES = 500


def parse_script(raw: Any) -> tuple[ScriptedMessage, ...]:
    """Read a script out of a connector's config.

    Config is operator input validated against the manifest's JSON schema, so
    the shape is already checked; what is checked here is what a schema cannot
    say - that an entry names an author, carries text and is not scheduled
    before the channel exists.
    """
    if raw in (None, "", [], {}):
        return ()
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        raise DomainValidationError("a collaboration script must be a list of entries")
    if len(raw) > MAX_SCRIPT_ENTRIES:
        raise DomainValidationError(
            f"a collaboration script may hold at most {MAX_SCRIPT_ENTRIES} entries"
        )
    entries: list[ScriptedMessage] = []
    for index, item in enumerate(raw):
        if not isinstance(item, Mapping):
            raise DomainValidationError(f"script entry {index} is not an object")
        try:
            author_type = ActorType(item.get("author_type", ActorType.HUMAN.value))
        except ValueError as exc:
            raise DomainValidationError(
                f"script entry {index}: author_type {item.get('author_type')!r} is not "
                f"one of {[a.value for a in ActorType]}"
            ) from exc
        entries.append(
            ScriptedMessage(
                after_s=float(item.get("after_s", 0)),
                author_id=str(item.get("author_id", "")),
                text=str(item.get("text", "")),
                author_type=author_type,
                author_display=item.get("author_display"),
                thread_id=item.get("thread_id"),
            )
        )
    return tuple(entries)


def scripted_message_id(channel: str, index: int) -> str:
    """Deterministic, so materialising the same script twice is one message."""
    digest = hashlib.sha256(f"{channel}\x00{index}".encode("utf-8")).hexdigest()
    return f"script-{digest[:24]}"


def materialize(
    *,
    tenant_id: str,
    app_id: Optional[str],
    channel: str,
    script: Sequence[ScriptedMessage],
    epoch: datetime,
) -> tuple[StoredMessage, ...]:
    """Turn a script into messages scheduled from `epoch`. Pure."""
    return tuple(
        StoredMessage(
            tenant_id=tenant_id,
            app_id=app_id,
            channel=channel,
            message_id=scripted_message_id(channel, index),
            author_type=entry.author_type,
            author_id=entry.author_id,
            author_display=entry.author_display,
            text=entry.text,
            sent_at=epoch + timedelta(seconds=entry.after_s),
            thread_id=entry.thread_id,
            scripted=True,
        )
        for index, entry in enumerate(script)
    )
