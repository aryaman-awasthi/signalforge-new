"""Conversation understanding: what was said, and what kind of thing it is
(REQ-052).

Two passes, and the order matters more than either of them.

**The deterministic pass runs first and always.** `domain/brain/statements.classify`
reads every message with regular expressions, no network and no clock. It is the
floor: with no key, no budget and no gateway, the bridge is still understood, a
contradiction is still raised, and the ledger still fills.

**The model pass runs second and may only promote.** It sees only the lines the
deterministic pass could not classify - the ones it called chatter or a question -
because that is exactly where the interesting misses live: *"Sam, can you check
the pool?"* is a question in form and a commitment in substance, and no honest
regular expression will ever catch it. `statements.reconcile` refuses any other
change, so a model cannot downgrade a commitment somebody explicitly made.

Three properties this agent has to hold:

- **A model classification never becomes an unaudited fact.** Every node it
  produces carries `Authority.INFERRED`, names the model, and stores the
  original text; the cockpit renders that distinction, and the audit entry has
  it.
- **No duplicate tasks.** Sprint 4's deterministic commitment harvester already
  turns promises into tasks, keyed on `(message_id, title)`. This agent does not
  create tasks at all - it records commitments as graph nodes keyed on
  `(ref, kind)`, and the task board stays the harvester's. Two writers with two
  key schemes producing two rows for one sentence is the failure this avoids,
  and it is avoided by not having two writers.
- **Untrusted in, untrusted out.** Bridge text is somebody else's writing. It is
  fenced before it reaches the model (`domain/brain/untrusted.fence`), scanned
  for injection attempts, and stored on nodes flagged `untrusted=True`.
"""

from __future__ import annotations

import logging
from typing import Optional, Sequence

from signalforge.application.brain.agents.base import (
    AgentOutcome,
    EdgeProposal,
    ModelBackedAgent,
    NodeProposal,
    observed,
)
from signalforge.application.brain.common import CONTRADICTION_TOPIC, INJECTION_TOPIC
from signalforge.application.brain.evidence import IncidentContext
from signalforge.domain.brain.nodes import (
    ConfirmationState,
    NodeType,
    Relation,
    derived_key,
)
from signalforge.domain.brain.statements import (
    MachineFact,
    STATEMENT_SCHEMA,
    Statement,
    StatementKind,
    classify,
    contradictions,
    reconcile,
)
from signalforge.domain.brain.untrusted import (
    SYSTEM_FLOOR,
    UntrustedItem,
    fence,
    scan,
)
from signalforge.domain.llm.entities import (
    CompletionRequest,
    Message,
    ModelClass,
    TokenUsage,
)

logger = logging.getLogger("signalforge.brain")

PURPOSE = "statement-classification"
MAX_MODEL_LINES = 20
"""How many unclassified lines are sent for promotion in one call. A cap rather
than a transcript: this is the bounded-input rule from REQ-054 applied at the
one call site most likely to be handed six hours of conversation."""

NODE_TYPE_FOR: dict[StatementKind, NodeType] = {
    StatementKind.FACT: NodeType.FACT,
    StatementKind.ASSUMPTION: NodeType.ASSUMPTION,
    StatementKind.DECISION: NodeType.ACTION,
    StatementKind.COMMITMENT: NodeType.ACTION,
    StatementKind.BLOCKER: NodeType.RISK,
    StatementKind.CONTRADICTION: NodeType.RISK,
}


class ConversationAgent(ModelBackedAgent):
    name = "conversation-agent"

    async def run(self, context: IncidentContext) -> AgentOutcome:
        if not context.messages:
            return AgentOutcome(skipped="this war room has no bridge transcript")

        subjects = context.services
        statements = [
            classify(
                ref=str(message.get("message_id") or ""),
                text=str(message.get("text") or ""),
                said_by=str(message.get("author_display") or message.get("author") or ""),
                said_at=message.get("at"),
                subjects=subjects,
            )
            for message in context.messages
            if message.get("message_id")
        ]

        # Injection scanning happens on the raw transcript, before anything is
        # classified or fenced: an attempt is a finding about the incident, and
        # it is recorded whether or not a model is ever called.
        findings = []
        for message in context.messages:
            findings.extend(
                scan(
                    str(message.get("text") or ""),
                    ref=f"bridge:{message.get('message_id')}",
                )
            )

        promoted, result = await self._promote(context, statements)
        merged = {statement.ref: statement for statement in statements}
        for statement in promoted:
            merged[statement.ref] = statement
        final = list(merged.values())

        nodes: list[NodeProposal] = []
        edges: list[EdgeProposal] = []
        events: list[tuple[str, dict]] = []

        for statement in final:
            if not statement.kind.enters_graph:
                continue
            nodes.append(self._node(statement))

        found = contradictions(final, _machine_facts(context))
        for contradiction in found:
            key = derived_key(NodeType.RISK, "contradiction", contradiction.key)
            nodes.append(
                NodeProposal(
                    node_type=NodeType.RISK,
                    derived_key=key,
                    label=f"Contradiction: {contradiction.detail}",
                    content=(
                        f"Said on the bridge: “{contradiction.statement.text}”. "
                        f"Measured: {contradiction.fact.detail or 'the opposite'}. "
                        "Either the belief is stale and is driving the wrong work, "
                        "or the measurement is pointed at the wrong thing. Both "
                        "are worth twenty minutes of an incident."
                    ),
                    provenance=observed(
                        self.name, result_ref=contradiction.statement.ref
                    ),
                    untrusted=True,
                    confirmation=ConfirmationState.CONFIRMED,
                    detail={
                        "statement_ref": contradiction.statement.ref,
                        "fact_ref": contradiction.fact.ref,
                        "evidence_kind": "contradiction",
                        "strength": 1.0,
                        "subject": contradiction.fact.subject,
                    },
                    tags=("contradiction",),
                )
            )
            edges.append(
                EdgeProposal(
                    source_key=key,
                    target_key=_statement_key(contradiction.statement),
                    relation=Relation.CONTRADICTS,
                    note=contradiction.detail,
                )
            )
            events.append(
                (
                    CONTRADICTION_TOPIC,
                    {
                        "incident_id": context.incident_id,
                        "subject": contradiction.fact.subject,
                        "detail": contradiction.detail,
                        "statement_ref": contradiction.statement.ref,
                        "measured_ref": contradiction.fact.ref,
                        "said_by": contradiction.statement.said_by,
                    },
                )
            )

        if findings:
            # Recorded as a risk node and an event, and the text is kept intact:
            # stripping the sentence would destroy the evidence and teach an
            # attacker what the filter looks for.
            nodes.append(
                NodeProposal(
                    node_type=NodeType.RISK,
                    derived_key=derived_key(NodeType.RISK, "injection-attempt"),
                    label=(
                        f"{len(findings)} message(s) contain text addressed to the "
                        "AI as an instruction"
                    ),
                    content=(
                        "Content in this incident tried to instruct the assistant "
                        "rather than inform it. It was treated as data throughout "
                        "and no instruction inside it was followed - the assistant "
                        "holds no tool and can change no state. Patterns seen: "
                        + ", ".join(sorted({finding.pattern for finding in findings}))
                    ),
                    provenance=observed(self.name),
                    untrusted=True,
                    detail={
                        "findings": [str(finding) for finding in findings[:10]],
                        "refs": sorted({finding.ref for finding in findings}),
                    },
                    tags=("prompt-injection",),
                )
            )
            events.append(
                (
                    INJECTION_TOPIC,
                    {
                        "incident_id": context.incident_id,
                        "count": len(findings),
                        "patterns": sorted({finding.pattern for finding in findings}),
                        "refs": sorted({finding.ref for finding in findings})[:20],
                        "action_taken": "treated as data; nothing was executed",
                    },
                )
            )

        return AgentOutcome(
            nodes=tuple(nodes),
            edges=tuple(edges),
            events=tuple(events),
            degraded=bool(result is not None and result.degraded),
            degraded_reason=(
                result.reason.value if result is not None and result.reason else ""
            ),
            model=result.model if result is not None else "",
            usage=result.usage if result is not None else TokenUsage(),
            notes=(
                f"{len(final)} statement(s) classified, "
                f"{sum(1 for s in final if s.by_model)} by the model",
            ),
        )

    # -- the model pass -------------------------------------------------------

    async def _promote(
        self, context: IncidentContext, statements: Sequence[Statement]
    ) -> tuple[list[Statement], Optional[object]]:
        """Ask the model about the lines the regular expressions could not place.

        Sends only those lines, fenced, with the refs it is allowed to name. A
        classification for a ref that was not sent is discarded rather than
        applied - the same allow-list rule the copilot uses, for the same reason.
        """
        candidates = [
            statement
            for statement in statements
            if statement.kind in (StatementKind.CHATTER, StatementKind.QUESTION)
            and statement.text
        ][:MAX_MODEL_LINES]
        if not candidates:
            return [], None

        items = [
            UntrustedItem(
                ref=statement.ref,
                kind="bridge message",
                text=(
                    f"{statement.said_by or 'unknown'}: {statement.text}"
                ),
                at=statement.said_at.isoformat() if statement.said_at else "",
            )
            for statement in candidates
        ]
        block = fence(items, salt=f"{context.incident_id}:conversation")
        prompt = (
            "These lines from an incident bridge were not classified by the "
            "deterministic pass. For each, decide which of fact, assumption, "
            "question, decision, commitment, blocker, contradiction or chatter "
            "it is.\n\n"
            "A commitment includes an indirect one: asking a named person to do "
            "something they are expected to pick up is a commitment, and name "
            "them in owner_hint exactly as the message does. Never guess a name "
            "that is not written.\n"
            "Only use the refs given. Answer about every ref or omit it; do not "
            "invent one.\n\n" + block
        )
        result = await self.llm.complete(
            CompletionRequest(
                purpose=PURPOSE,
                messages=(
                    Message(role="system", content=SYSTEM_FLOOR),
                    Message(role="user", content=prompt),
                ),
                model_class=ModelClass.GENERAL,
                schema=STATEMENT_SCHEMA,
                fallback={"statements": []},
                tenant_id=context.tenant_id,
                incident_id=context.incident_id,
                max_tokens=900,
            )
        )
        if result.degraded or not isinstance(result.data, dict):
            return [], result

        allowed = {statement.ref: statement for statement in candidates}
        promoted: list[Statement] = []
        for raw in result.data.get("statements", []) or []:
            ref = str(raw.get("ref") or "")
            base = allowed.get(ref)
            if base is None:
                # A ref the retrieval never offered. Discarded silently rather
                # than looked up: an id the model produced is not an id.
                continue
            try:
                kind = StatementKind(str(raw.get("kind")))
            except ValueError:
                continue
            promoted.append(
                reconcile(
                    base,
                    kind=kind,
                    subject=str(raw.get("subject") or ""),
                    owner_hint=str(raw.get("owner_hint") or ""),
                    why=str(raw.get("why") or ""),
                )
            )
        return promoted, result

    # -- nodes ----------------------------------------------------------------

    def _node(self, statement: Statement) -> NodeProposal:
        node_type = NODE_TYPE_FOR.get(statement.kind, NodeType.FACT)
        return NodeProposal(
            node_type=node_type,
            derived_key=_statement_key(statement),
            label=(
                f"{statement.said_by or 'somebody'} "
                f"({statement.kind.value}): {statement.text}"
            )[:300],
            content=statement.text,
            provenance=self.provenance(
                source=(
                    "bridge (model-classified)"
                    if statement.by_model
                    else "bridge (rule-classified)"
                ),
                result_ref=f"bridge:{statement.ref}",
                detail={
                    "classifier": statement.classifier,
                    "why": statement.why,
                    "owner_hint": statement.owner_hint,
                    **dict(statement.detail),
                },
            ),
            # Somebody else's words, kept verbatim and flagged all the way
            # through to the prompt builder.
            untrusted=True,
            detail={
                "kind": statement.kind.value,
                "said_by": statement.said_by,
                "subject": statement.subject,
                "classifier": statement.classifier,
                "evidence_kind": "human_statement",
                "strength": 1.0 if statement.kind is StatementKind.FACT else 0.6,
            },
            tags=(statement.kind.value, statement.classifier),
        )


def _statement_key(statement: Statement) -> str:
    node_type = NODE_TYPE_FOR.get(statement.kind, NodeType.FACT)
    return derived_key(node_type, "statement", statement.key)


def _machine_facts(context: IncidentContext) -> tuple[MachineFact, ...]:
    """What the platform measured, in the shape a human claim is checked against.

    Built only from measurements, and only about subjects the platform names -
    the signal keys and the topology's services. A looser subject match would
    cry contradiction every third message, and a war room that did that would be
    turned off by minute ten.
    """
    verdict = context.verdict
    if verdict is None:
        return ()
    facts: list[MachineFact] = []
    for state in verdict.states:
        subject = (state.signal_key or "").strip()
        if not subject:
            continue
        healthy = state.status.value not in ("anomalous", "breaching")
        facts.append(
            MachineFact(
                ref=f"signal:{state.signal_key}:{state.region or 'all'}",
                subject=subject,
                healthy=healthy,
                detail=(
                    f"{state.status.value}"
                    + (f" in {state.region}" if state.region else "")
                    + (f", value {state.value}" if state.value is not None else "")
                ),
                source="telemetry",
            )
        )
    return tuple(facts)
