"""The shape of a replayed incident (REQ-072).

A replay entry answers six questions, and the set is not arbitrary - it is what
somebody reviewing a production change three months later actually has to ask:

    what happened?          topic + headline
    when?                   occurred_at, in the incident's own time
    who or what caused it?  actor, and whether it was a person or the platform
    why was it allowed?     justification: the policy rules hit, the approval,
                            the evidence, the person who decided
    what changed?           effect: the state on the far side
    what backs it up?       references: result_ref, execution id, node ids,
                            probe results, seal - things that can be opened

An entry that could not answer "who" or "what justified it" would be a log line.
The point of this module is that the platform's own vocabulary of topics is
mapped onto those six questions once, in one table, rather than being reinvented
by every reader.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Mapping, Optional


class Consequence(str, Enum):
    """How much a reviewer should care.

    Not a severity and not a log level: it is "did this change somebody's
    estate, or somebody's authority to change it?".
    """

    CONSEQUENTIAL = "consequential"
    """Changed production, or granted the authority to. Approvals, executions,
    policy versions, closure, issued communications."""

    DECISION = "decision"
    """A judgement the platform or a person made that others acted on."""

    OBSERVATION = "observation"
    """Something was measured or noticed."""

    ADMINISTRATIVE = "administrative"
    """Housekeeping: sessions, memberships, configuration that is not policy."""


@dataclass(frozen=True)
class AuditRecord:
    """One row of the hash-chained audit log, as the reader returns it."""

    seq: int
    event_id: str
    tenant_id: str
    topic: str
    actor_type: str
    actor_id: str
    app_id: Optional[str]
    occurred_at: datetime
    payload: Mapping[str, Any]
    payload_hash: str
    prev_hash: Optional[str]


@dataclass(frozen=True)
class ChainStatus:
    """Whether the record being replayed is the record that was written."""

    valid: bool
    checked: int
    first_broken_seq: Optional[int]
    broken_seqs: tuple[int, ...]
    reason: str

    @property
    def trustworthy(self) -> bool:
        return self.valid


@dataclass(frozen=True)
class ReplayEntry:
    seq: int
    event_id: str
    topic: str
    at: datetime
    headline: str
    actor_type: str
    actor_id: str
    actor_label: str
    consequence: Consequence
    justification: tuple[str, ...] = ()
    effect: str = ""
    references: tuple[str, ...] = ()
    payload: Mapping[str, Any] = field(default_factory=dict)

    @property
    def by_a_person(self) -> bool:
        return self.actor_type == "human"


@dataclass(frozen=True)
class ReplayDocument:
    incident_id: str
    tenant_id: str
    generated_at: datetime
    chain: ChainStatus
    entries: tuple[ReplayEntry, ...]
    from_seq: int
    to_seq: int
    seals: tuple[Mapping[str, Any], ...] = ()

    @property
    def count(self) -> int:
        return len(self.entries)

    @property
    def consequential(self) -> tuple[ReplayEntry, ...]:
        return tuple(
            entry
            for entry in self.entries
            if entry.consequence is Consequence.CONSEQUENTIAL
        )

    def at(self, seq: int) -> tuple[ReplayEntry, ...]:
        """Everything up to and including a point on the scrubber."""
        return tuple(entry for entry in self.entries if entry.seq <= seq)
