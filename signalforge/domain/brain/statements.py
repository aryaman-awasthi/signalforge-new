"""What people say on a bridge, and what kind of thing it is (REQ-052).

A war room's transcript is the richest source of state in an incident and the
worst-structured. Somebody says "pool is at 100%" (a fact), "probably the deploy"
(an assumption), "should we roll back?" (a question), "rolling back region-b" (a
decision), "I'll drain the queue in ten" (a commitment), "I can't get into the
console" (a blocker), and "db is healthy" while telemetry says it is not (a
contradiction). Six of those change what the incident believes, and the seventh
is the one worth waking somebody for.

Two classifiers live here and they are not alternatives:

- `classify` is **deterministic**. Regular expressions and a decision order,
  running with no network, on a virtual clock, in a test. It is what runs when
  there is no model, when the budget is spent, and as the floor under the model
  the rest of the time.
- The model classifier (application layer) uses `STATEMENT_SCHEMA` and only ever
  *upgrades* the deterministic answer where the semantics justify it - the
  indirect commitment ("Sam, can you check the pool?") that no honest regular
  expression will ever catch. Its output is validated back through
  `reconcile`, which refuses a classification the evidence does not support.

**A model classification never becomes an unaudited fact.** Everything here
produces a `Statement` carrying the original text, the classifier that decided,
and the confidence; the caller writes it to the graph with `Authority.INFERRED`
when a model decided, and the cockpit renders that distinction.

Contradiction is separate and deliberately so: it is not a property of one
sentence but a relation between a sentence and something the platform measured.
`contradictions` takes human claims and machine facts and pairs them, which is
why it can be deterministic - it is comparing two structured things, not reading
a mood.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Mapping, Optional, Sequence

MAX_TEXT_CHARS = 1_000
MAX_SUBJECT_CHARS = 80


class StatementKind(str, Enum):
    FACT = "fact"
    ASSUMPTION = "assumption"
    QUESTION = "question"
    DECISION = "decision"
    COMMITMENT = "commitment"
    BLOCKER = "blocker"
    CONTRADICTION = "contradiction"
    CHATTER = "chatter"
    """Everything else. A named category rather than a null, because "we decided
    this line means nothing" is itself a decision worth being able to audit and
    to disagree with."""

    @property
    def enters_graph(self) -> bool:
        """Whether this kind becomes a node. Questions and chatter do not - a
        graph that recorded every question asked would bury the answers."""
        return self in (
            StatementKind.FACT,
            StatementKind.ASSUMPTION,
            StatementKind.DECISION,
            StatementKind.COMMITMENT,
            StatementKind.BLOCKER,
            StatementKind.CONTRADICTION,
        )


STATEMENT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["statements"],
    "properties": {
        "statements": {
            "type": "array",
            "maxItems": 40,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["ref", "kind"],
                "properties": {
                    "ref": {
                        "type": "string",
                        "description": "The exact ref given for this message.",
                    },
                    "kind": {
                        "type": "string",
                        "enum": [kind.value for kind in StatementKind],
                    },
                    "subject": {
                        "type": "string",
                        "maxLength": MAX_SUBJECT_CHARS,
                        "description": (
                            "The service, component or thing the statement is "
                            "about, in two or three words."
                        ),
                    },
                    "owner_hint": {
                        "type": "string",
                        "maxLength": 60,
                        "description": (
                            "For a commitment: who was asked or who volunteered, "
                            "exactly as the message names them. Never a guess."
                        ),
                    },
                    "why": {"type": "string", "maxLength": 200},
                },
            },
        }
    },
}


@dataclass(frozen=True)
class Statement:
    """One classified utterance, with everything needed to defend the call."""

    ref: str
    """The message id. Also the citation and the idempotency handle."""
    kind: StatementKind
    text: str
    said_by: str = ""
    said_at: Optional[datetime] = None
    subject: str = ""
    owner_hint: str = ""
    classifier: str = "deterministic"
    confidence: float = 0.0
    why: str = ""
    detail: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "text", str(self.text or "")[:MAX_TEXT_CHARS])
        object.__setattr__(
            self, "subject", str(self.subject or "").strip()[:MAX_SUBJECT_CHARS]
        )

    @property
    def by_model(self) -> bool:
        return self.classifier == "model"

    @property
    def key(self) -> str:
        """Stable across classifiers and reruns.

        Deliberately over the *ref and kind* rather than over the text: the
        deterministic pass and the model pass reading the same message must
        collide when they agree, so the graph gets one node, and must not
        collide when they disagree, so a disagreement is visible.
        """
        digest = hashlib.sha256(
            f"{self.ref}|{self.kind.value}".encode("utf-8")
        ).hexdigest()[:20]
        return f"stmt-{digest}"


# --- the deterministic classifier ---------------------------------------------

QUESTION = re.compile(r"\?\s*$|^\s*(?:should|shall|could|can|do|does|did|is|are|who|what|why|when|where|how)\b", re.I)
BLOCKER = re.compile(
    r"\b(?:blocked|blocker|can'?t\s+(?:get|access|reach|log|connect)|cannot\s+"
    r"(?:get|access|reach|log|connect)|no\s+access|locked\s+out|permission\s+denied|"
    r"waiting\s+on|need\s+access|don'?t\s+have\s+(?:access|permission|the\s+creds?))\b",
    re.I,
)
DECISION = re.compile(
    r"\b(?:we(?:'| a)?re\s+(?:rolling|going\s+with|failing\s+over|switching)|"
    r"decision\s*:|decided\s+to|let'?s\s+(?:roll|fail|switch|go\s+with)|"
    r"(?:rolling|failing)\s+(?:back|over)\s+now|call\s+is\s+to)\b",
    re.I,
)
COMMITMENT = re.compile(
    r"\b(?:i'?ll|we'?ll|i\s+will|we\s+will|i\s+am\s+going\s+to|we\s+are\s+going\s+to|"
    r"picking\s+(?:this\s+)?up|on\s+it)\b",
    re.I,
)
TAKING = re.compile(
    r"^\s*(?:i(?:'m|\s+am)\s+)?(?:taking|owning|picking\s+up)\s+\S", re.I
)
"""Somebody claiming a piece of work, anchored at the start of the message.

Anchored deliberately, and mirroring `domain/warroom/commitments.TAKING` for the
same reason it is anchored there: "taking" mid-sentence is usually a description
("taking down the region is risky"), while a message that *opens* with it is
somebody putting their hand up."""
HEDGE = re.compile(
    r"\b(?:probably|likely|maybe|might|could\s+be|i\s+think|i\s+suspect|"
    r"my\s+guess|seems?\s+like|looks?\s+like|possibly|presumably|assume|assuming)\b",
    re.I,
)
MEASURED = re.compile(
    # No trailing \b after the unit: `%` is a non-word character, so "12% in
    # eu-west-1" would not match one - which is exactly the sentence this
    # pattern exists to catch.
    r"\b\d+(?:\.\d+)?\s*(?:%|percent\b|ms\b|s\b|sec\b|seconds?\b|rps\b|qps\b|"
    r"errors?\b|connections?\b|gb\b|mb\b)"
    r"|\b(?:is|are|was|were|looks?|seems?)\s+(?:at|showing|returning|failing|"
    r"down|up|green|red|fine|ok|okay|good|healthy|normal|clean|stable|broken|"
    r"degraded|bad|unhealthy|struggling)\b"
    r"|\bno\s+(?:issues?|errors?|problems?)\b"
    r"|\bnothing\s+wrong\b",
    re.I,
)
"""What makes a sentence an assertion about the world rather than conversation.

A number with a unit, or a state verb naming a state. **The health adjectives are
in here deliberately**: "payments-db is fine" is a factual claim about a
measurable thing, and it is exactly the claim that contradicts telemetry saying
otherwise. Without them the most valuable statement a bridge produces would be
classified as chatter and would never reach the contradiction check."""
FILLER = re.compile(r"^(?:ok|okay|yes|no|thanks|ack|acknowledged|\+1|got\s+it|hi|hey)\b[.! ]*$", re.I)


def classify(
    ref: str,
    text: str,
    *,
    said_by: str = "",
    said_at: Optional[datetime] = None,
    subjects: Sequence[str] = (),
) -> Statement:
    """One message, classified without a model. Total and order-independent.

    The decision order is the interesting part and it is not arbitrary. A
    question is checked first because "should we roll back?" contains a decision
    verb and is not a decision. A blocker outranks a commitment because "I'll do
    it as soon as I can get access" is a blocker with a promise attached, and the
    blocker is the actionable half. A hedge outranks a measurement because
    "probably 100% pool usage" is an assumption about a number, not a fact.
    """
    body = str(text or "").strip()
    if not body or FILLER.match(body):
        kind, why, confidence = StatementKind.CHATTER, "no content", 0.9
    elif QUESTION.search(body):
        kind, why, confidence = StatementKind.QUESTION, "asked, not asserted", 0.8
    elif BLOCKER.search(body):
        kind, why, confidence = StatementKind.BLOCKER, "names something that is in the way", 0.75
    elif DECISION.search(body):
        kind, why, confidence = StatementKind.DECISION, "announces a course of action taken", 0.75
    elif TAKING.match(body) or COMMITMENT.search(body):
        kind, why, confidence = StatementKind.COMMITMENT, "somebody takes work on", 0.75
    elif HEDGE.search(body):
        kind, why, confidence = StatementKind.ASSUMPTION, "hedged, so not asserted as fact", 0.7
    elif MEASURED.search(body):
        kind, why, confidence = StatementKind.FACT, "states a measured value or state", 0.65
    else:
        kind, why, confidence = StatementKind.CHATTER, "no classifiable claim", 0.5
    return Statement(
        ref=ref,
        kind=kind,
        text=body,
        said_by=said_by,
        said_at=said_at,
        subject=_subject_in(body, subjects),
        classifier="deterministic",
        confidence=confidence,
        why=why,
    )


def _subject_in(text: str, subjects: Sequence[str]) -> str:
    """The known service a sentence is about, if it names one.

    Only ever matched against a list the caller supplies - the application and
    its topology - because guessing a subject out of free text is how "the pool"
    becomes a service called Pool.
    """
    lowered = text.lower()
    for subject in sorted(subjects, key=len, reverse=True):
        name = str(subject or "").strip()
        if name and name.lower() in lowered:
            return name[:MAX_SUBJECT_CHARS]
    return ""


MODEL_UPGRADABLE: frozenset[StatementKind] = frozenset(
    {StatementKind.CHATTER, StatementKind.QUESTION}
)
"""The only classifications a model is allowed to overturn.

This is the whole safety property of the hybrid classifier. The deterministic
pass is conservative: when it says "commitment" it has matched an explicit
promise, and a model second-guessing that would lose a task somebody made. When
it says "chatter" or "question" it has merely failed to match a pattern, and
that is exactly where "Sam, can you check the pool?" lives - a question in form,
a commitment request in substance. So the model may promote from those two and
from nothing else.
"""


def reconcile(
    deterministic: Statement, *, kind: StatementKind, subject: str = "",
    owner_hint: str = "", why: str = "", confidence: float = 0.6,
) -> Statement:
    """Merge a model classification onto the deterministic one.

    Refuses to downgrade or contradict a confident deterministic call, keeps the
    original text and ref, and stamps `classifier="model"` so that every reader -
    the ledger, the audit entry, the copilot - can see a model decided this.
    """
    if kind is deterministic.kind:
        # Agreement is worth recording: it is why the subject and owner hint the
        # model extracted are trusted enough to keep.
        return Statement(
            ref=deterministic.ref,
            kind=deterministic.kind,
            text=deterministic.text,
            said_by=deterministic.said_by,
            said_at=deterministic.said_at,
            subject=subject or deterministic.subject,
            owner_hint=owner_hint or deterministic.owner_hint,
            classifier="model",
            confidence=max(confidence, deterministic.confidence),
            why=why or deterministic.why,
            detail={"agreed_with": "deterministic"},
        )
    if deterministic.kind not in MODEL_UPGRADABLE:
        # The deterministic pass matched something explicit. Keep it, and record
        # that the model disagreed - a disagreement rate is a useful thing to be
        # able to measure later, and silently discarding it would hide it.
        return Statement(
            ref=deterministic.ref,
            kind=deterministic.kind,
            text=deterministic.text,
            said_by=deterministic.said_by,
            said_at=deterministic.said_at,
            subject=deterministic.subject,
            owner_hint=deterministic.owner_hint,
            classifier="deterministic",
            confidence=deterministic.confidence,
            why=deterministic.why,
            detail={"model_proposed": kind.value, "model_overruled": True},
        )
    return Statement(
        ref=deterministic.ref,
        kind=kind,
        text=deterministic.text,
        said_by=deterministic.said_by,
        said_at=deterministic.said_at,
        subject=subject or deterministic.subject,
        owner_hint=owner_hint,
        classifier="model",
        confidence=confidence,
        why=why or "promoted by the model from an unmatched line",
        detail={"deterministic_was": deterministic.kind.value},
    )


# --- contradiction -------------------------------------------------------------


@dataclass(frozen=True)
class MachineFact:
    """Something the platform measured, in the shape a claim can be checked
    against. Deliberately tiny: subject, whether it is healthy, and where it came
    from."""

    ref: str
    subject: str
    healthy: bool
    detail: str = ""
    source: str = "detection"


HEALTHY_CLAIM = re.compile(
    r"\b(?:is|are|looks?|seems?|all)\s+(?:fine|ok|okay|good|green|healthy|up|"
    r"normal|clean|stable)\b|\bno\s+(?:issues?|problems?|errors?)\b|"
    r"\bnothing\s+wrong\b",
    re.I,
)
UNHEALTHY_CLAIM = re.compile(
    r"\b(?:is|are|looks?|seems?)\s+(?:down|broken|failing|red|degraded|bad|"
    r"unhealthy|struggling)\b|\b(?:errors?|timeouts?|failures?)\b",
    re.I,
)


@dataclass(frozen=True)
class Contradiction:
    """A human claim and a measurement that cannot both be true.

    The single most valuable thing conversation understanding produces. Somebody
    saying "db is healthy" while the health check for that database is failing is
    either a stale belief driving the wrong work, or a measurement pointed at the
    wrong thing - and both are worth twenty minutes of an incident.
    """

    statement: Statement
    fact: MachineFact
    detail: str

    @property
    def key(self) -> str:
        digest = hashlib.sha256(
            f"{self.statement.ref}|{self.fact.ref}".encode("utf-8")
        ).hexdigest()[:20]
        return f"contra-{digest}"


def contradictions(
    statements: Sequence[Statement], facts: Sequence[MachineFact]
) -> tuple[Contradiction, ...]:
    """Pair human claims against measurements about the same subject.

    Only claims that *name* a subject the platform measures are checked, and the
    subject match is exact against the caller's own list. A looser match would
    generate contradictions between "the pool" in one sentence and a service
    called `pool-manager` in another, and a war room that cried contradiction
    every third message would be turned off by minute ten.
    """
    by_subject: dict[str, list[MachineFact]] = {}
    for fact in facts:
        by_subject.setdefault(fact.subject.strip().lower(), []).append(fact)
    found: list[Contradiction] = []
    for statement in statements:
        if statement.kind not in (StatementKind.FACT, StatementKind.ASSUMPTION):
            continue
        subject = statement.subject.strip().lower()
        if not subject:
            continue
        claims_healthy = bool(HEALTHY_CLAIM.search(statement.text))
        claims_unhealthy = bool(UNHEALTHY_CLAIM.search(statement.text))
        if claims_healthy == claims_unhealthy:
            # Either no health claim at all, or a sentence claiming both - which
            # is somebody thinking out loud, not a contradiction with telemetry.
            continue
        for fact in by_subject.get(subject, ()):
            if claims_healthy and not fact.healthy:
                found.append(
                    Contradiction(
                        statement=statement,
                        fact=fact,
                        detail=(
                            f"{statement.said_by or 'somebody'} said {fact.subject} "
                            f"is healthy; {fact.source} reports it is not"
                            + (f" ({fact.detail})" if fact.detail else "")
                        ),
                    )
                )
            elif claims_unhealthy and fact.healthy:
                found.append(
                    Contradiction(
                        statement=statement,
                        fact=fact,
                        detail=(
                            f"{statement.said_by or 'somebody'} said {fact.subject} "
                            f"is failing; {fact.source} reports it is healthy"
                            + (f" ({fact.detail})" if fact.detail else "")
                        ),
                    )
                )
    return tuple(found)
