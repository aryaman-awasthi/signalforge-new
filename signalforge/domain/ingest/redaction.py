"""The ingest redaction floor (REQ-032, REQ-014).

S3-T02 established the rule that free text coming out of a customer's estate is
masked *at the boundary, at write time*, so that a later reader of the row
cannot bypass it. Push ingestion is the same boundary in the other direction and
needs the same rule, but it cannot borrow the adapter's: the frozen SPI defines
`normalize` only on alerts, changes and topology, so a collaboration or itsm
adapter can never be push-mode and there is no manifest to read a policy from.

So the floor lives here. It is deliberately small and deterministic - email
addresses and long digit runs, the two shapes that turn an incident timeline
into a data-protection incident - and it is applied to the fields that carry
free text a human typed:

| Stream | Fields |
|---|---|
| messages | `text` |
| tickets | `title`, `description` |
| alerts | `annotations` values (summary and description, by convention) |

Changes and topology carry identifiers, not prose, and masking a commit range or
a node label would break correlation for no benefit.

A masked payload is recorded on the envelope's `Redaction` value object naming
this policy, so evidence can cite it.
"""

from __future__ import annotations

import re
from typing import Any, Mapping

from pydantic import BaseModel

from signalforge.domain.ingest.streams import IngestStream

POLICY_ID = "ingest-default"

PATTERNS: tuple[str, ...] = (
    r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}",
    r"\b(?:\d[ -]?){13,19}\b",
)

REDACTED = "***"

TEXT_FIELDS: dict[IngestStream, tuple[str, ...]] = {
    IngestStream.MESSAGES: ("text",),
    IngestStream.TICKETS: ("title", "description"),
    IngestStream.ALERTS: ("annotations",),
}

_COMPILED = tuple(re.compile(pattern) for pattern in PATTERNS)


def mask(text: str) -> str:
    masked = text
    for pattern in _COMPILED:
        masked = pattern.sub(REDACTED, masked)
    return masked


def apply(stream: IngestStream, payload: BaseModel) -> tuple[BaseModel, tuple[str, ...]]:
    """Return the payload with the floor applied and the fields it changed.

    Only fields that actually changed are reported, so `redaction.applied` on
    the envelope means "something was removed from this event" rather than
    "a policy exists", which is the difference between a useful flag and noise.
    """
    fields = TEXT_FIELDS.get(stream, ())
    if not fields:
        return payload, ()
    updates: dict[str, Any] = {}
    changed: list[str] = []
    for name in fields:
        value = getattr(payload, name, None)
        if isinstance(value, str):
            masked = mask(value)
            if masked != value:
                updates[name] = masked
                changed.append(name)
        elif isinstance(value, Mapping):
            masked_map = {
                key: mask(item) if isinstance(item, str) else item
                for key, item in value.items()
            }
            if masked_map != dict(value):
                updates[name] = masked_map
                changed.append(name)
    if not updates:
        return payload, ()
    return payload.model_copy(update=updates), tuple(changed)
