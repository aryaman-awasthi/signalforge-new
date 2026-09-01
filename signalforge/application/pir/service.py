"""Generating a post-incident review (REQ-072).

Every section is built from a stored record: the hash-chained audit replay, the
incident row and its transitions, the execution ledger, the validation cycles,
the communications, and the incident graph. Nothing here asks a model what
happened, and nothing here can be made to.

Three refusals, in the order they bite:

1. **A broken chain stops the review.** If the audit chain does not verify, this
   raises rather than returning a document with a warning banner, because a PIR
   is the artefact people quote six months later and a quotable document that
   was silently built on edited records is worse than no document.

2. **A lesson is PROPOSED until its citations resolve.** The gate is in the
   domain (`domain/pir/review.py`); this module's job is to hand it the set of
   refs the incident actually produced. A lesson citing an execution that never
   ran, or a validation cycle from a different incident, does not become
   institutional memory.

3. **A model may phrase, never conclude.** `narrate` is optional, off by
   default, and anything it returns lands as a PROPOSED lesson from
   `LessonSource.MODEL`. There is no argument that changes that.
"""

from __future__ import annotations

import logging
from typing import Callable, Optional, Sequence

from signalforge.application.audit.replay_service import IncidentReplayService
from signalforge.application.identity.principal import Principal
from signalforge.application.identity.uow import IdentityUnitOfWork
from signalforge.domain.audit.entries import Consequence, ReplayDocument
from signalforge.domain.identity.errors import NotFoundError
from signalforge.domain.identity.ports import Clock
from signalforge.domain.pir.review import (
    Citation,
    Lesson,
    LessonSource,
    LessonStatus,
    PostIncidentReview,
    Section,
)

logger = logging.getLogger("signalforge.pir")

MAX_TIMELINE_LINES = 200


class AuditChainBrokenError(Exception):
    """The record this review would be built from is not the record that was
    written. Raised rather than reported, because a PIR is quoted."""


class PirService:
    def __init__(
        self,
        uow_factory: Callable[[], IdentityUnitOfWork],
        *,
        replay: IncidentReplayService,
        clock: Clock,
    ):
        self._uow_factory = uow_factory
        self._replay = replay
        self._clock = clock

    async def generate(
        self, principal: Principal, incident_id: str
    ) -> PostIncidentReview:
        document = await self._replay.replay(principal, incident_id)
        if not document.chain.valid:
            raise AuditChainBrokenError(document.chain.reason)

        async with self._uow_factory() as uow:
            incident = await uow.incidents.get(principal.tenant_id, incident_id)
            if incident is None:
                raise NotFoundError(f"incident {incident_id} not found")
            executions = list(
                await uow.executions.list(principal.tenant_id, incident_id)
            )
            approvals = list(
                await uow.approvals.list(principal.tenant_id, incident_id)
            )
            cycles = list(await uow.validations.list(principal.tenant_id, incident_id))
            comms = list(
                await uow.communications.list(principal.tenant_id, incident_id)
            )
            nodes = list(
                await uow.incident_graph.list(
                    principal.tenant_id, incident_id, current_only=False, limit=500
                )
            )

        refs = _available_refs(document, executions, cycles, comms, nodes)
        sections = _sections(
            incident=incident,
            document=document,
            executions=executions,
            approvals=approvals,
            cycles=cycles,
            comms=comms,
            nodes=nodes,
        )
        lessons = tuple(
            lesson.verify(refs, at=self._clock.now())
            for lesson in _candidate_lessons(
                document=document,
                executions=executions,
                cycles=cycles,
                comms=comms,
                nodes=nodes,
            )
        )
        followups = _followups(executions, cycles, comms)
        return PostIncidentReview(
            incident_id=incident_id,
            tenant_id=principal.tenant_id,
            reference=incident.reference,
            title=incident.title,
            app_id=incident.app_id,
            generated_at=self._clock.now(),
            sections=sections,
            lessons=lessons,
            followups=followups,
            chain_valid=True,
            chain_detail=document.chain.reason,
            degraded=True,
            model="deterministic",
            metadata={
                "entries": document.count,
                "consequential": len(document.consequential),
                "executions": len(executions),
                "validation_cycles": len(cycles),
                "communications": len(comms),
                "graph_nodes": len(nodes),
                "seals": len(document.seals),
            },
        )

    def propose(
        self,
        review: PostIncidentReview,
        *,
        text: str,
        citations: Sequence[Citation],
        source: LessonSource = LessonSource.HUMAN,
        lesson_id: str,
    ) -> Lesson:
        """A lesson somebody - or something - wants to add.

        It arrives PROPOSED and stays PROPOSED until `generate` runs again and
        the gate resolves its citations against the incident's records. There is
        deliberately no `verified=True` argument anywhere on this path.
        """
        return Lesson(
            id=lesson_id,
            text=text,
            citations=tuple(citations),
            status=LessonStatus.PROPOSED,
            source=source,
        )


# --- section builders ----------------------------------------------------------


def _sections(  # noqa: C901 - one branch per required section, by design
    *, incident, document: ReplayDocument, executions, approvals, cycles, comms, nodes
) -> tuple[Section, ...]:
    return (
        Section(
            key="summary",
            title="Incident summary",
            lines=(
                f"{incident.reference} {incident.title}",
                f"application {incident.app_id}, severity {incident.severity.value}, "
                f"state {incident.state.value}",
                f"declared by {incident.declared_by_display or incident.declared_by} "
                f"at {incident.declared_at.isoformat()}",
                (
                    f"closed by {incident.closed_by} at {incident.closed_at.isoformat()}"
                    if incident.closed_at
                    else "not yet closed"
                ),
            ),
            citations=(Citation(ref=f"incident:{incident.id}", kind="incident"),),
        ),
        Section(
            key="impact",
            title="Impact",
            lines=tuple(
                f"{node.label} ({node.confirmation.value if node.confirmation else 'unconfirmed'})"
                for node in nodes
                if node.node_type.value == "impact"
            )
            or ("no impact node was recorded",),
            citations=tuple(
                Citation(ref=f"node:{node.id}", kind="graph")
                for node in nodes
                if node.node_type.value == "impact"
            ),
        ),
        Section(
            key="timeline",
            title="Timeline",
            lines=tuple(
                f"{entry.at.isoformat()} - {entry.headline} "
                f"[{entry.actor_label}]"
                + (f" - {entry.effect}" if entry.effect else "")
                for entry in document.entries[:MAX_TIMELINE_LINES]
            ),
            citations=tuple(
                Citation(ref=f"audit:{entry.seq}", kind="audit")
                for entry in document.entries[:MAX_TIMELINE_LINES]
            ),
        ),
        Section(
            key="detection",
            title="Detection",
            lines=tuple(
                f"{entry.at.isoformat()} - {entry.headline}"
                + (f" ({'; '.join(entry.justification)})" if entry.justification else "")
                for entry in document.entries
                if entry.topic.startswith("detection.")
            )
            or ("this incident was declared without a detection proposal",),
        ),
        Section(
            key="changes",
            title="Contributing changes",
            lines=tuple(
                f"{link.change_id} ({link.change_type})"
                for link in incident.change_windows
            )
            or ("no change was linked to this incident",),
        ),
        Section(
            key="hypotheses",
            title="Hypotheses considered",
            lines=tuple(
                f"{node.label} - confidence "
                f"{node.confidence if node.confidence is not None else 'n/a'}"
                f" ({node.status.value})"
                for node in nodes
                if node.node_type.value == "hypothesis"
            )
            or ("no hypothesis was recorded",),
            citations=tuple(
                Citation(ref=f"node:{node.id}", kind="graph")
                for node in nodes
                if node.node_type.value == "hypothesis"
            ),
        ),
        Section(
            key="evidence",
            title="Evidence that confirmed or rejected them",
            lines=tuple(
                f"{node.label} (from {node.provenance.source}, "
                f"{node.provenance.authority.value if node.provenance.authority else 'unstated'})"
                for node in nodes
                if node.node_type.value == "evidence"
            )
            or ("no evidence node was recorded",),
            citations=tuple(
                Citation(
                    ref=f"node:{node.id}",
                    kind="graph",
                    detail=node.provenance.result_ref or "",
                )
                for node in nodes
                if node.node_type.value == "evidence"
            ),
        ),
        Section(
            key="actions",
            title="Actions taken",
            lines=tuple(
                f"{record.action_id} ({record.risk_class.value}) requested by "
                f"{record.requested_by} - decision {record.decision.value}, "
                f"final state {record.state.value}"
                for record in executions
            )
            or ("no action was executed through the gateway",),
            citations=tuple(
                Citation(ref=f"execution:{record.id}", kind="execution")
                for record in executions
            ),
        ),
        Section(
            key="approvals",
            title="Approvals",
            lines=tuple(
                f"{approval.action_id}: requested by {approval.requested_by}, "
                f"{approval.state.value}"
                + (
                    f" by {approval.approver_user_id}"
                    if getattr(approval, "approver_user_id", None)
                    else ""
                )
                for approval in approvals
            )
            or ("no approval was required",),
        ),
        Section(
            key="uncertain",
            title="Uncertain outcomes and reconciliation",
            lines=tuple(
                f"{record.action_id}: {record.error_category.value} -> "
                f"reconciled {record.reconcile_outcome.value if record.reconcile_outcome else 'not yet'}"
                f", retry {'blocked' if record.retry_blocked else 'allowed'}"
                for record in executions
                if record.reconcile_outcome is not None
                or record.state.value in ("uncertain", "completed_late", "not_applied")
            )
            or ("no action left an uncertain outcome",),
            citations=tuple(
                Citation(ref=f"execution:{record.id}", kind="execution")
                for record in executions
                if record.reconcile_outcome is not None
            ),
        ),
        Section(
            key="validation",
            title="Validation",
            lines=tuple(
                f"cycle {cycle.cycle}: {cycle.verdict.value} - "
                + ", ".join(
                    f"{layer.layer.value} {layer.verdict.value}" for layer in cycle.layers
                )
                for cycle in cycles
            )
            or ("recovery was never independently validated",),
            citations=tuple(
                Citation(ref=f"validation:{cycle.id}", kind="validation")
                for cycle in cycles
            ),
        ),
        Section(
            key="communications",
            title="Communications and corrections",
            lines=tuple(
                f"{c.audience.value}: {c.state.value}"
                + (f" - corrects {c.corrects_id}" if c.corrects_id else "")
                + (
                    f" - contradicted: {c.contradiction_reason}"
                    if c.contradiction_reason
                    else ""
                )
                for c in comms
            )
            or ("nothing was communicated",),
            citations=tuple(
                Citation(ref=f"communication:{c.id}", kind="communication") for c in comms
            ),
        ),
        Section(
            key="worked",
            title="What worked",
            lines=_what_worked(document, executions, cycles),
        ),
        Section(
            key="failed",
            title="What failed",
            lines=_what_failed(document, executions, cycles, comms),
        ),
        Section(
            key="lessons",
            title="Lessons",
            lines=("see the lessons list, with each lesson's verification status",),
        ),
        Section(
            key="followups",
            title="Follow-up actions",
            lines=_followups(executions, cycles, comms)
            or ("no follow-up is outstanding",),
        ),
    )


def _what_worked(document: ReplayDocument, executions, cycles) -> tuple[str, ...]:
    """Controls that fired. Stated as facts about records, never as praise."""
    out: list[str] = []
    topics = {entry.topic for entry in document.entries}
    if "execution.blocked" in topics:
        out.append(
            "the platform safety floor refused an action that asked to run "
            "without a person (execution.blocked)"
        )
    if "execution.approval.granted" in topics:
        out.append(
            "a high-risk action was approved by somebody other than the person "
            "who requested it (execution.approval.granted)"
        )
    if "execution.retry.blocked" in topics:
        out.append(
            "a retry was refused until the uncertain outcome had been reconciled "
            "(execution.retry.blocked)"
        )
    if "execution.duplicate.prevented" in topics:
        out.append(
            "a duplicate submission was recognised and nothing was sent twice "
            "(execution.duplicate.prevented)"
        )
    if "incident.closure.blocked" in topics:
        out.append(
            "closure was refused while a customer-facing check was still failing "
            "(incident.closure.blocked)"
        )
    if "warroom.page.escalated" in topics:
        out.append("an unanswered page escalated on its own (warroom.page.escalated)")
    if any(cycle.verdict.value == "recovered" for cycle in cycles):
        out.append("recovery was independently validated before the incident closed")
    return tuple(out) or ("nothing notable is recorded for this section",)


def _what_failed(document, executions, cycles, comms) -> tuple[str, ...]:
    out: list[str] = []
    for record in executions:
        if record.state.value == "uncertain":
            out.append(
                f"{record.action_id} left an uncertain outcome that is still "
                "unreconciled"
            )
        if record.error_category.value == "timeout":
            out.append(f"{record.action_id} timed out against the application")
        if record.state.value == "failed":
            out.append(f"{record.action_id} failed: {record.error or 'no detail'}")
    for cycle in cycles:
        for layer in cycle.layers:
            if layer.verdict.value == "fail":
                out.append(
                    f"validation cycle {cycle.cycle}: the {layer.layer.value} layer "
                    f"failed - {layer.summary}"
                )
    for communication in comms:
        if communication.state.value == "contradicted":
            out.append(
                f"a {communication.audience.value} communication was contradicted "
                f"after it was issued: {communication.contradiction_reason}"
            )
    return tuple(dict.fromkeys(out)) or ("nothing failed that was recorded",)


def _followups(executions, cycles, comms) -> tuple[str, ...]:
    out: list[str] = []
    for record in executions:
        if record.state.value == "uncertain":
            out.append(
                f"reconcile {record.action_id} ({record.id}) - its outcome is still "
                "unknown"
            )
    for communication in comms:
        if communication.state.value == "contradicted":
            out.append(
                f"issue a correction for the {communication.audience.value} "
                f"communication {communication.id}"
            )
    if any(cycle.escalated for cycle in cycles):
        out.append("review why validation failed repeatedly enough to escalate")
    return tuple(dict.fromkeys(out))


# --- lessons -------------------------------------------------------------------


def _candidate_lessons(*, document, executions, cycles, comms, nodes) -> tuple[Lesson, ...]:
    """Lessons the incident's own records support, each carrying its citations.

    Deliberately conservative and deliberately boring. Every one of these is a
    statement about something that is *in the record*, which is the only kind of
    lesson that can pass the gate.
    """
    out: list[Lesson] = []

    for record in executions:
        if record.reconcile_outcome is not None:
            out.append(
                Lesson(
                    id=f"lesson-reconcile-{record.id}",
                    text=(
                        f"{record.action_id} timed out and was later found to have "
                        f"{record.reconcile_outcome.value.replace('_', ' ')}. A timeout "
                        "against this action is not evidence that it did not run, so "
                        "reconciliation must precede any retry."
                    ),
                    citations=(
                        Citation(ref=f"execution:{record.id}", kind="execution"),
                    ),
                )
            )

    for cycle in cycles:
        failing = [layer for layer in cycle.layers if layer.verdict.value == "fail"]
        for layer in failing:
            if layer.layer.value == "customer":
                out.append(
                    Lesson(
                        id=f"lesson-customer-{cycle.id}",
                        text=(
                            "Technical recovery preceded customer recovery in this "
                            "incident: the customer layer was still failing when the "
                            "estate looked healthy. Closing on infrastructure health "
                            "would have closed this incident while customers were "
                            "still affected."
                        ),
                        citations=(
                            Citation(ref=f"validation:{cycle.id}", kind="validation"),
                        ),
                    )
                )

    for communication in comms:
        if communication.state.value == "contradicted":
            out.append(
                Lesson(
                    id=f"lesson-comms-{communication.id}",
                    text=(
                        "A statement issued to "
                        f"{communication.audience.value} was contradicted by evidence "
                        "that arrived afterwards. Communications drawn from a moving "
                        "graph need re-checking before the incident closes."
                    ),
                    citations=(
                        Citation(
                            ref=f"communication:{communication.id}", kind="communication"
                        ),
                    ),
                )
            )

    blocked = [e for e in document.entries if e.topic == "execution.blocked"]
    for entry in blocked:
        out.append(
            Lesson(
                id=f"lesson-floor-{entry.seq}",
                text=(
                    "An action was requested with auto intent and refused by the "
                    "platform safety floor. The refusal is in the chain and is "
                    "attributable, which is what makes it reviewable."
                ),
                citations=(Citation(ref=f"audit:{entry.seq}", kind="audit"),),
            )
        )

    retracted = [
        node
        for node in nodes
        if node.node_type.value == "hypothesis" and node.status.value == "superseded"
    ]
    if retracted:
        out.append(
            Lesson(
                id="lesson-hypotheses-moved",
                text=(
                    f"{len(retracted)} hypothesis version(s) were superseded during "
                    "this incident. The ranking the room acted on early was not the "
                    "ranking it ended with."
                ),
                citations=tuple(
                    Citation(ref=f"node:{node.id}", kind="graph") for node in retracted
                ),
            )
        )
    return tuple(out)


def _available_refs(document, executions, cycles, comms, nodes) -> tuple[str, ...]:
    """Everything this incident actually produced, in citation form.

    The gate is set-membership against exactly this. A lesson citing an
    execution from another incident, or an audit seq that is not in this
    incident's replay, does not resolve - and stays proposed.
    """
    refs: list[str] = []
    refs += [f"audit:{entry.seq}" for entry in document.entries]
    refs += [f"execution:{record.id}" for record in executions]
    refs += [f"validation:{cycle.id}" for cycle in cycles]
    refs += [f"communication:{c.id}" for c in comms]
    refs += [f"node:{node.id}" for node in nodes]
    return tuple(refs)


def consequential_entries(document: ReplayDocument):
    return [e for e in document.entries if e.consequence is Consequence.CONSEQUENTIAL]


def _unused(value: Optional[str]) -> str:  # pragma: no cover - readability helper
    return value or ""
