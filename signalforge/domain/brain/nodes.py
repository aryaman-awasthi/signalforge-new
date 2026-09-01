"""The incident graph's nodes and edges (REQ-050).

The Incident Brain is a *record of reasoning*, not a cache of conclusions, and
every design choice in this module follows from that one sentence.

**Nothing is overwritten.** A node carries a `derived_key` - a stable, caller
chosen identity for "the thing this is about" - and a `version`. When what the
platform believes about that thing changes, a new node is written at
`version + 1`, it names the node it `supersedes`, and the older node is marked
`SUPERSEDED` rather than updated in place. So "why did hypothesis #2 move from
65% to 32%?" is answerable six months later by reading rows, and an incident can
be replayed rather than re-derived.

**Every node says where it came from.** `Provenance` carries the actor type
(human, agent, system, external), the `authority` the frozen envelope already
defines - authoritative, observed, inferred - the source system, and, for
anything a model touched, the model and classification that produced it. A
model-produced classification is `INFERRED` by construction: there is no path in
this module by which an LLM's opinion is recorded as an observed fact.

**Confidence is optional and never invented here.** A node carries a confidence
only when something computed one (`domain/brain/confidence.py`); this module
stores it and refuses values outside [0, 1].

**Untrusted content is marked as untrusted.** `content` on an evidence node may
be an excerpt of a bridge message, a log line or a retrieved document - text
this platform did not write. `untrusted=True` travels with it to every reader,
including the prompt builder, which is what keeps "ignore your previous
instructions" a quotation rather than an instruction (`domain/brain/untrusted.py`).
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import Enum
from typing import Any, Mapping, Optional, Sequence

from signalforge.contracts.envelope import ActorType, Authority
from signalforge.domain.brain.errors import BrainValidationError

MAX_LABEL_CHARS = 300
MAX_CONTENT_CHARS = 4_000
MAX_REF_CHARS = 500
MAX_TAGS = 20

_KEY_SAFE = re.compile(r"[^a-z0-9:._\-]+")


class NodeType(str, Enum):
    """The nine node kinds the sprint names, and nothing else.

    A closed vocabulary because it is a metric label, an API filter and a UI tab
    row: an open-ended "type" string would let one agent invent a category no
    screen renders and no reviewer can find.
    """

    FACT = "fact"
    ASSUMPTION = "assumption"
    HYPOTHESIS = "hypothesis"
    EVIDENCE = "evidence"
    IMPACT = "impact"
    RISK = "risk"
    ACTION = "action"
    APPROVAL = "approval"
    COMMUNICATION = "communication"

    @property
    def is_belief(self) -> bool:
        """Types whose truth is the platform's opinion rather than a measurement.

        The cockpit renders these differently and the copilot is required to
        qualify them, because "the connection pool is exhausted" as a hypothesis
        and as a fact are different sentences with the same words.
        """
        return self in (NodeType.HYPOTHESIS, NodeType.ASSUMPTION, NodeType.RISK)


class NodeStatus(str, Enum):
    CURRENT = "current"
    SUPERSEDED = "superseded"
    RETRACTED = "retracted"
    """Withdrawn without a replacement: a hypothesis ruled out, an assumption
    that proved false. Kept and readable - a retracted belief is the most
    interesting kind at a post-incident review - and excluded from the current
    view."""

    @property
    def historical(self) -> bool:
        return self is not NodeStatus.CURRENT


class Relation(str, Enum):
    """What one node says about another.

    `SUPPORTS` and `REFUTES` are the pair the confidence policy reads, and they
    are separate relations rather than a signed weight so that "evidence
    against" is a first-class thing a screen can list.
    """

    SUPPORTS = "supports"
    REFUTES = "refutes"
    ABOUT = "about"
    """Context rather than argument: an impact node about a hypothesis, an
    action about a risk."""
    DISCRIMINATES = "discriminates"
    """The test that would tell two hypotheses apart. The single most useful
    edge on the screen at minute ten."""
    CONTRADICTS = "contradicts"
    """Two statements that cannot both be true - a human saying the database is
    healthy while telemetry says its connectivity is failing."""
    DERIVED_FROM = "derived_from"
    SUPERSEDES = "supersedes"


class ConfirmationState(str, Enum):
    """Whether something was *observed* or is *expected*.

    The single most important distinction in this file and the reason it is an
    enum rather than a boolean buried in a payload. A predicted downstream
    outage rendered like an observed one is how a war room pages four teams for
    an outage that never happened. Every impact and every blast-radius member
    carries one of these, and the API and the cockpit both name it.
    """

    CONFIRMED = "confirmed"
    PREDICTED = "predicted"

    @property
    def observed(self) -> bool:
        return self is ConfirmationState.CONFIRMED


@dataclass(frozen=True)
class Provenance:
    """Where a node came from, in the fields an auditor asks for.

    `model` and `model_class` are populated only when a model was actually
    involved, and `degraded` says the answer came from the deterministic
    fallback instead. A node written by a template and a node written by
    `genailab-maas-gpt-5.4` are not the same evidence, and a reader who cannot
    tell them apart cannot calibrate anything.
    """

    source: str
    """The agent or subsystem that wrote it: `telemetry-agent`, `bridge`,
    `detection`, `human`."""
    actor_type: ActorType = ActorType.AGENT
    actor_id: str = ""
    authority: Authority = Authority.INFERRED
    model: str = ""
    model_class: str = ""
    prompt_purpose: str = ""
    degraded: bool = False
    result_ref: str = ""
    """Where the underlying data can be read in full - an event id, a rollup
    key, a message id, a change id. The drill-down handle: a node carries an
    excerpt, and this says where the rest of it is (REQ-054)."""
    detail: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.source:
            raise BrainValidationError("a graph node must name where it came from")
        object.__setattr__(self, "result_ref", str(self.result_ref or "")[:MAX_REF_CHARS])

    @property
    def model_written(self) -> bool:
        return bool(self.model) and not self.degraded

    def as_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "actor_type": self.actor_type.value,
            "actor_id": self.actor_id,
            "authority": self.authority.value,
            "model": self.model,
            "model_class": self.model_class,
            "prompt_purpose": self.prompt_purpose,
            "degraded": self.degraded,
            "result_ref": self.result_ref,
            "detail": dict(self.detail),
        }


def derived_key(kind: NodeType, *parts: str) -> str:
    """A stable identity for "the thing this node is about".

    Two agents reasoning about the same connection pool, a redelivered event and
    a restarted worker must all land on the same key, because that is what makes
    a graph write idempotent and a version history a history rather than a pile
    of near-duplicates (REQ-021's rule, applied to reasoning state).

    Normalised aggressively - lowercased, non-key characters collapsed - so that
    "Connection pool" and "connection-pool" are one thing. Long parts are hashed
    rather than truncated: truncation makes two different sources collide, which
    is the one failure this key exists to prevent.
    """
    cleaned: list[str] = []
    for part in parts:
        text = _KEY_SAFE.sub("-", str(part or "").strip().lower()).strip("-")
        if not text:
            continue
        if len(text) > 60:
            text = hashlib.sha256(text.encode("utf-8")).hexdigest()[:24]
        cleaned.append(text)
    if not cleaned:
        raise BrainValidationError("a derived key needs at least one meaningful part")
    return f"{kind.value}:" + ":".join(cleaned)


@dataclass(frozen=True)
class GraphNode:
    """One thing the platform believes, or one thing it was told.

    Immutable. A change produces `superseded_by()` plus a new node, which is
    what makes the ledger replayable and what stops an agent from quietly
    rewriting the reason an incident was declared.
    """

    id: str
    tenant_id: str
    incident_id: str
    node_type: NodeType
    derived_key: str
    label: str
    created_at: datetime
    provenance: Provenance
    seq: int = 0
    """Creation order within the incident, assigned by the repository. The
    ledger reads in this order, so a snapshot taken at seq N is reproducible."""
    version: int = 1
    status: NodeStatus = NodeStatus.CURRENT
    content: str = ""
    untrusted: bool = False
    """True when `content` is text this platform did not write. Travels to every
    reader, including the prompt builder (`domain/brain/untrusted.py`)."""
    confidence: Optional[float] = None
    confirmation: Optional[ConfirmationState] = None
    supersedes: Optional[str] = None
    superseded_by: Optional[str] = None
    retired_at: Optional[datetime] = None
    retired_reason: str = ""
    tags: tuple[str, ...] = ()
    detail: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.tenant_id or not self.incident_id:
            raise BrainValidationError(
                "a graph node belongs to exactly one incident in one tenant"
            )
        label = str(self.label or "").strip()
        if not label:
            raise BrainValidationError("a graph node needs a label")
        object.__setattr__(self, "label", label[:MAX_LABEL_CHARS])
        object.__setattr__(self, "content", str(self.content or "")[:MAX_CONTENT_CHARS])
        if not self.derived_key:
            raise BrainValidationError("a graph node needs a derived key")
        if self.confidence is not None and not 0.0 <= self.confidence <= 1.0:
            raise BrainValidationError(
                f"confidence {self.confidence} is outside [0, 1]; a score that is "
                "not a probability is not a score"
            )
        if self.version < 1:
            raise BrainValidationError("versions start at 1")
        object.__setattr__(self, "tags", tuple(self.tags)[:MAX_TAGS])

    # -- moves ---------------------------------------------------------------

    def next_version(
        self,
        *,
        node_id: str,
        at: datetime,
        label: Optional[str] = None,
        content: Optional[str] = None,
        confidence: Optional[float] = None,
        confirmation: Optional[ConfirmationState] = None,
        provenance: Optional[Provenance] = None,
        detail: Optional[Mapping[str, Any]] = None,
        tags: Optional[Sequence[str]] = None,
    ) -> "GraphNode":
        """The replacement for this node. The old one is *not* mutated here -
        the repository marks it superseded in the same transaction."""
        return replace(
            self,
            id=node_id,
            version=self.version + 1,
            status=NodeStatus.CURRENT,
            created_at=at,
            seq=0,
            label=label if label is not None else self.label,
            content=content if content is not None else self.content,
            confidence=confidence if confidence is not None else self.confidence,
            confirmation=confirmation if confirmation is not None else self.confirmation,
            provenance=provenance or self.provenance,
            detail=dict(detail) if detail is not None else dict(self.detail),
            tags=tuple(tags) if tags is not None else self.tags,
            supersedes=self.id,
            superseded_by=None,
            retired_at=None,
            retired_reason="",
        )

    def as_superseded(self, *, by: str, at: datetime) -> "GraphNode":
        return replace(
            self,
            status=NodeStatus.SUPERSEDED,
            superseded_by=by,
            retired_at=at,
        )

    def as_retracted(self, *, at: datetime, reason: str) -> "GraphNode":
        """Withdrawn with no replacement. Kept, and excluded from the current
        view - a ruled-out hypothesis is evidence about the investigation."""
        return replace(
            self,
            status=NodeStatus.RETRACTED,
            retired_at=at,
            retired_reason=str(reason or "")[:MAX_LABEL_CHARS],
        )

    # -- reads ---------------------------------------------------------------

    @property
    def current(self) -> bool:
        return self.status is NodeStatus.CURRENT

    @property
    def citation(self) -> str:
        """How the copilot and the cockpit refer to this node. The node id, so a
        citation resolves to exactly one row at exactly one version."""
        return self.id

    def differs_from(
        self,
        *,
        label: str,
        content: str,
        confidence: Optional[float],
        confirmation: Optional[ConfirmationState],
    ) -> bool:
        """Whether re-observing this thing is worth a new version.

        The idempotency guard: an agent that runs every thirty seconds and sees
        the same world must not mint a version every time. Confidence compares
        at three decimal places because floating-point noise is not a change of
        belief.
        """
        if label.strip()[:MAX_LABEL_CHARS] != self.label:
            return True
        if str(content or "")[:MAX_CONTENT_CHARS] != self.content:
            return True
        if confirmation is not None and confirmation is not self.confirmation:
            return True
        if (confidence is None) != (self.confidence is None):
            return True
        if confidence is not None and self.confidence is not None:
            return round(confidence, 3) != round(self.confidence, 3)
        return False


@dataclass(frozen=True)
class GraphEdge:
    """A relation between two nodes, kept forever like the nodes themselves.

    Edges are written against *node ids*, so an edge points at one version of a
    belief rather than at "whatever the current one is". A hypothesis that moved
    from 65% to 32% still has the edges that argued it up, attached to the
    version they argued about.
    """

    id: str
    tenant_id: str
    incident_id: str
    source_id: str
    target_id: str
    relation: Relation
    created_at: datetime
    weight: float = 0.0
    """The confidence policy's weight for this link, at the moment it was drawn.
    Stored rather than recomputed so a history entry can be re-read exactly."""
    note: str = ""
    detail: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.source_id == self.target_id:
            raise BrainValidationError("a node cannot relate to itself")
        object.__setattr__(self, "note", str(self.note or "")[:MAX_LABEL_CHARS])


@dataclass(frozen=True)
class SituationSnapshot:
    """The whole reasoning state at one moment, addressable and comparable.

    Taken when something structural happens - the incident is declared, a
    severity changes, the governor finishes a run that moved a hypothesis - and
    kept forever. It stores the node ids that were current, not their content:
    the nodes are already immutable, so the ids are enough to reconstruct the
    view exactly, and a snapshot that copied content would double every future
    redaction problem.
    """

    id: str
    tenant_id: str
    incident_id: str
    taken_at: datetime
    reason: str
    node_ids: tuple[str, ...] = ()
    hypotheses: Mapping[str, float] = field(default_factory=dict)
    topology_version: str = ""
    max_seq: int = 0
    degraded: bool = False
    detail: Mapping[str, Any] = field(default_factory=dict)

    @property
    def fingerprint(self) -> str:
        """A hash over what was current. Two snapshots with the same fingerprint
        describe the same situation, which is how "nothing has changed since you
        last looked" is answered without diffing the whole graph."""
        payload = "|".join(sorted(self.node_ids)) + "#" + "|".join(
            f"{key}={round(value, 3)}" for key, value in sorted(self.hypotheses.items())
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


def current_nodes(nodes: Sequence[GraphNode]) -> tuple[GraphNode, ...]:
    return tuple(node for node in nodes if node.current)


def by_type(
    nodes: Sequence[GraphNode], node_type: NodeType, *, current_only: bool = True
) -> tuple[GraphNode, ...]:
    return tuple(
        node
        for node in nodes
        if node.node_type is node_type and (node.current or not current_only)
    )


def history_of(nodes: Sequence[GraphNode], key: str) -> tuple[GraphNode, ...]:
    """Every version of one thing, oldest first. What the version-history icon
    on the ledger opens."""
    return tuple(
        sorted(
            (node for node in nodes if node.derived_key == key),
            key=lambda node: (node.version, node.seq),
        )
    )
