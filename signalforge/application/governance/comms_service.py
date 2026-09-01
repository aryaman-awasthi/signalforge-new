"""Governed communications (REQ-064).

The three rules this service exists to hold, in the order they matter:

**Nothing sends itself.** There is no method here that transmits anything, no
scheduler that promotes a draft, and no configuration that could add one.
`issue` records that a named person, holding the audience's approval permission,
decided to publish - and what a deployment does with an ISSUED communication is
a notification concern, downstream of the decision, never upstream of it.

**Everything is grounded.** A draft cites incident-graph node ids, and
`Communication.assert_grounded` refuses a draft citing nothing or citing a node
outside this incident. The deterministic writer builds its text *from* the nodes
it cites, so the citation list is not decoration bolted on afterwards.

**Issued words are never edited.** When the evidence under an issued statement
moves - a node superseded, a node retracted - `sweep_contradictions` flags the
communication as CONTRADICTED and drafts a correction carrying a redline. The
original text stays exactly as it was published. Quietly rewriting what an
organisation told its customers is not a correction.

## The model's role

The deterministic writer runs first and always. It produces a complete, usable
communication from the graph. The model is then asked to rewrite *that material*
for the audience, and its output is accepted only if it still cites the same
nodes and says nothing the template did not. If the model is unavailable the
template is what ships, wearing the `degraded` badge so a reader can calibrate.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Optional, Sequence

from signalforge.application.events.bus import EventBus
from signalforge.application.governance.common import (
    COMMS_APPROVED_TOPIC,
    COMMS_CONTRADICTED_TOPIC,
    COMMS_CORRECTION_TOPIC,
    COMMS_DRAFTED_TOPIC,
    COMMS_ISSUED_TOPIC,
    publish_system,
    record,
)
from signalforge.application.identity.principal import Principal
from signalforge.application.identity.uow import IdentityUnitOfWork
from signalforge.contracts.roles import ROLE_PERMISSIONS, Permission
from signalforge.domain.brain.nodes import GraphNode, NodeStatus, NodeType
from signalforge.domain.brain.untrusted import SYSTEM_FLOOR, UntrustedItem, fence
from signalforge.domain.governance.comms import (
    AUDIENCE_PERMISSION,
    Audience,
    Communication,
    CommunicationState,
)
from signalforge.domain.governance.errors import (
    CommunicationNotFoundError,
    CommunicationStateError,
    UngroundedCommunicationError,
)
from signalforge.domain.identity.errors import PermissionDeniedError
from signalforge.domain.identity.ports import Clock
from signalforge.domain.incidents.errors import IncidentNotFoundError
from signalforge.domain.llm.entities import CompletionRequest, Message, ModelClass
from signalforge.domain.llm.ports import LlmProvider

logger = logging.getLogger("signalforge.governance")

PURPOSE = "communication-draft"
CORRECTION_PURPOSE = "communication-correction"

DRAFT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["subject", "body"],
    "properties": {
        "subject": {"type": "string", "maxLength": 200},
        "body": {"type": "string", "maxLength": 4000},
    },
}

AUDIENCE_GUIDANCE: Mapping[Audience, str] = {
    Audience.RESPONDERS: (
        "People already in the war room. Terse, technical, no reassurance, no "
        "restating what they can see on the screen."
    ),
    Audience.LEADERSHIP: (
        "Executives. What is affected, how many customers, what is being done, "
        "when the next update is. No component names, no jargon, no speculation "
        "about cause."
    ),
    Audience.CUSTOMER_SUPPORT: (
        "Support agents who will be asked about this by customers in the next "
        "ten minutes. What a customer would notice, what to tell them, what not "
        "to promise."
    ),
    Audience.CUSTOMER: (
        "Customers, on a public status page. Plain language, what is affected, "
        "what is not, what to do, when the next update is. Never name an "
        "internal component, never blame a vendor, never state a cause that is "
        "still a hypothesis."
    ),
}


@dataclass(frozen=True)
class CommsView:
    communications: tuple[Communication, ...]
    contradicted: tuple[Communication, ...]
    corrections: tuple[Communication, ...]


class CommunicationService:
    def __init__(
        self,
        uow_factory: Callable[[], IdentityUnitOfWork],
        bus: EventBus,
        clock: Clock,
        llm: LlmProvider,
        metrics=None,
    ):
        self._uow_factory = uow_factory
        self._bus = bus
        self._clock = clock
        self._llm = llm
        self._metrics = metrics

    # -- reads ---------------------------------------------------------------

    async def list(
        self,
        principal: Principal,
        incident_id: str,
        *,
        audience: Optional[Audience] = None,
        limit: int = 100,
    ) -> CommsView:
        """Read the board, having first checked whether anything it shows has
        stopped being true.

        The sweep runs on read as well as on the bus, deliberately: a
        contradiction that only appeared after a background job had run would let
        somebody open the screen and see a statement the platform already knew
        was wrong.
        """
        await self.sweep_contradictions(principal.tenant_id, incident_id)
        async with self._uow_factory() as uow:
            await self._require_incident(uow, principal.tenant_id, incident_id)
            rows = list(
                await uow.communications.list(
                    principal.tenant_id, incident_id, audience=audience, limit=limit
                )
            )
        return CommsView(
            communications=tuple(rows),
            contradicted=tuple(
                c for c in rows if c.state is CommunicationState.CONTRADICTED
            ),
            corrections=tuple(c for c in rows if c.corrects_id),
        )

    async def get(
        self, principal: Principal, incident_id: str, communication_id: str
    ) -> Communication:
        async with self._uow_factory() as uow:
            return await self._require_communication(
                uow, principal.tenant_id, incident_id, communication_id
            )

    # -- draft ---------------------------------------------------------------

    async def draft(
        self,
        principal: Principal,
        incident_id: str,
        *,
        audience: Audience,
        subject: str = "",
        body: str = "",
        node_ids: Sequence[str] = (),
        use_model: bool = True,
    ) -> Communication:
        """Write a draft for one audience, grounded on graph nodes.

        A caller may supply the text (a Comms Lead writing it themselves) or
        leave it empty and let the platform write it. Either way the grounding is
        checked against this incident's graph, and either way it is a DRAFT.
        """
        now = self._clock.now()
        async with self._uow_factory() as uow:
            incident = await self._require_incident(
                uow, principal.tenant_id, incident_id
            )
            nodes = list(
                await uow.incident_graph.list(
                    principal.tenant_id, incident_id, limit=200
                )
            )
        available = [node.id for node in nodes]
        chosen = tuple(node_ids) or _default_grounding(nodes, audience)
        if not chosen:
            raise UngroundedCommunicationError(
                "there is nothing on this incident's record to write from yet. "
                "A communication has to cite something."
            )
        cited = [node for node in nodes if node.id in set(chosen)]
        written_subject = subject.strip() or _subject(incident, audience)
        written_body = body.strip() or _body(incident, cited, audience)
        degraded = True
        model = ""
        model_class = ""

        if use_model and not body.strip():
            rewritten = await self._rewrite(
                principal,
                incident_id,
                audience=audience,
                subject=written_subject,
                body=written_body,
                cited=cited,
            )
            if rewritten is not None:
                written_subject, written_body, model, model_class = rewritten
                degraded = False

        communication = Communication(
            id=str(uuid.uuid4()),
            tenant_id=principal.tenant_id,
            incident_id=incident_id,
            app_id=incident.app_id,
            audience=audience,
            state=CommunicationState.DRAFT,
            subject=written_subject,
            body=written_body,
            created_at=now,
            created_by=principal.user_id,
            grounded_node_ids=chosen,
            evidence_refs=tuple(
                node.provenance.result_ref for node in cited if node.provenance.result_ref
            )[:20],
            model=model,
            model_class=model_class,
            degraded=degraded,
        )
        communication.assert_grounded(available)

        async with self._uow_factory() as uow:
            await uow.communications.add(communication)
            await record(
                uow,
                self._bus,
                self._clock,
                principal=principal,
                app_id=incident.app_id,
                incident_id=incident_id,
                topic=COMMS_DRAFTED_TOPIC,
                payload={
                    **communication.as_payload(),
                    "requires_permission": (
                        communication.required_permission.value
                        if communication.required_permission
                        else None
                    ),
                    "auto_send": False,
                },
            )
            await uow.commit()
        if self._metrics is not None:
            self._metrics.increment(
                "signalforge_communications_total",
                {"audience": audience.value, "state": "draft"},
            )
        return communication

    # -- approve and issue ---------------------------------------------------

    async def approve(
        self,
        principal: Principal,
        incident_id: str,
        communication_id: str,
        *,
        note: str = "",
    ) -> Communication:
        """A named person with the audience's permission says the text is right.

        The permission is checked here as well as at the route, because the
        *audience* decides which permission applies and the route cannot know an
        audience it has not read yet. A customer status update approved by
        somebody who holds only `approve_exec_comms` is refused.
        """
        now = self._clock.now()
        async with self._uow_factory() as uow:
            communication = await self._require_communication(
                uow, principal.tenant_id, incident_id, communication_id
            )
            self._assert_may_approve(principal, communication)
            approved = communication.approved(
                at=now,
                by=principal.user_id,
                by_label=principal.display_name or "",
                permission=communication.required_permission,
            )
            await uow.communications.update(approved)
            await record(
                uow,
                self._bus,
                self._clock,
                principal=principal,
                app_id=communication.app_id,
                incident_id=incident_id,
                topic=COMMS_APPROVED_TOPIC,
                payload={**approved.as_payload(), "note": note[:400]},
            )
            await uow.commit()
        return approved

    async def issue(
        self,
        principal: Principal,
        incident_id: str,
        communication_id: str,
        *,
        note: str = "",
    ) -> Communication:
        """Publish. A person, always.

        Approving and issuing are two acts rather than one because they answer
        two questions - "is this true?" and "do we say it now?" - and an incident
        routinely gets a correct draft approved twenty minutes before anyone
        wants it out.
        """
        now = self._clock.now()
        async with self._uow_factory() as uow:
            communication = await self._require_communication(
                uow, principal.tenant_id, incident_id, communication_id
            )
            if communication.state is not CommunicationState.APPROVED:
                raise CommunicationStateError(
                    f"this communication is {communication.state.value}; only an "
                    "approved communication can be issued"
                )
            self._assert_may_approve(principal, communication)
            issued = communication.issued_now(at=now, by=principal.user_id)
            await uow.communications.update(issued)
            if communication.corrects_id:
                original = await uow.communications.get(
                    principal.tenant_id, communication.corrects_id
                )
                if original is not None and original.state.public:
                    await uow.communications.update(
                        original.corrected_by(issued.id)
                    )
            await record(
                uow,
                self._bus,
                self._clock,
                principal=principal,
                app_id=communication.app_id,
                incident_id=incident_id,
                topic=COMMS_ISSUED_TOPIC,
                payload={
                    **issued.as_payload(),
                    "note": note[:400],
                    "issued_by_human": True,
                },
            )
            await uow.commit()
        if self._metrics is not None:
            self._metrics.increment(
                "signalforge_communications_total",
                {"audience": issued.audience.value, "state": "issued"},
            )
        return issued

    # -- contradiction and correction ---------------------------------------

    async def sweep_contradictions(self, tenant_id: str, incident_id: str) -> int:
        """Flag issued communications whose grounding has moved.

        The only automatic state change in this module, and it is a *warning*,
        never an edit. A superseded or retracted node means somebody's published
        statement may no longer be true, and saying so quickly is the whole
        point; changing what was published is never this platform's decision.
        """
        async with self._uow_factory() as uow:
            nodes = await uow.incident_graph.list(
                tenant_id, incident_id, current_only=False, limit=500
            )
            stale = {
                node.id: node
                for node in nodes
                if node.status in (NodeStatus.SUPERSEDED, NodeStatus.RETRACTED)
            }
            if not stale:
                return 0
            affected = await uow.communications.issued_grounded_on(
                tenant_id, incident_id, list(stale)
            )
            flagged = 0
            for communication in affected:
                gone = [
                    node_id
                    for node_id in communication.grounded_node_ids
                    if node_id in stale
                ]
                if not gone:
                    continue
                reason = _contradiction_reason(stale, gone)
                updated = communication.contradicted(
                    at=self._clock.now(), reason=reason, node_ids=gone
                )
                await uow.communications.update(updated)
                await publish_system(
                    uow,
                    self._bus,
                    self._clock,
                    tenant_id=tenant_id,
                    app_id=communication.app_id,
                    incident_id=incident_id,
                    topic=COMMS_CONTRADICTED_TOPIC,
                    payload={
                        **updated.as_payload(),
                        "superseded_nodes": gone,
                        "reason": reason,
                        "text_unchanged": True,
                        "next_step": "draft a correction with a redline and have "
                        "it approved",
                    },
                )
                flagged += 1
            if flagged:
                await uow.commit()
        if flagged and self._metrics is not None:
            self._metrics.increment(
                "signalforge_communications_contradicted_total", {}
            )
        return flagged

    async def draft_correction(
        self,
        principal: Principal,
        incident_id: str,
        communication_id: str,
        *,
        subject: str = "",
        body: str = "",
        node_ids: Sequence[str] = (),
        reason: str = "",
        use_model: bool = True,
    ) -> Communication:
        """A new draft that says what changed, beside what was said before.

        The redline is computed from the two bodies rather than typed, so a
        correction cannot claim to change one thing while changing another. It
        needs its own approval like any other communication - a correction to a
        customer statement is a customer statement.
        """
        now = self._clock.now()
        async with self._uow_factory() as uow:
            original = await self._require_communication(
                uow, principal.tenant_id, incident_id, communication_id
            )
            incident = await self._require_incident(
                uow, principal.tenant_id, incident_id
            )
            nodes = list(
                await uow.incident_graph.list(
                    principal.tenant_id, incident_id, limit=200
                )
            )
        available = [node.id for node in nodes]
        chosen = tuple(node_ids) or _default_grounding(nodes, original.audience)
        cited = [node for node in nodes if node.id in set(chosen)]
        written_subject = subject.strip() or f"Correction: {original.subject}"
        written_body = body.strip() or _body(incident, cited, original.audience)
        model = ""
        model_class = ""
        degraded = True
        if use_model and not body.strip():
            rewritten = await self._rewrite(
                principal,
                incident_id,
                audience=original.audience,
                subject=written_subject,
                body=written_body,
                cited=cited,
                correcting=original,
            )
            if rewritten is not None:
                written_subject, written_body, model, model_class = rewritten
                degraded = False

        correction = original.correction_draft(
            communication_id=str(uuid.uuid4()),
            at=now,
            by=principal.user_id,
            subject=written_subject,
            body=written_body,
            grounded_node_ids=chosen,
            model=model,
            model_class=model_class,
            degraded=degraded,
            reason=reason or original.contradiction_reason,
        )
        correction.assert_grounded(available)

        async with self._uow_factory() as uow:
            await uow.communications.add(correction)
            await record(
                uow,
                self._bus,
                self._clock,
                principal=principal,
                app_id=correction.app_id,
                incident_id=incident_id,
                topic=COMMS_CORRECTION_TOPIC,
                payload={
                    **correction.as_payload(),
                    "corrects": original.id,
                    "redline": [change.as_dict() for change in correction.redline],
                    "reason": reason or original.contradiction_reason,
                    "auto_send": False,
                },
            )
            await uow.commit()
        return correction

    # -- internals -----------------------------------------------------------

    def _assert_may_approve(
        self, principal: Principal, communication: Communication
    ) -> None:
        required = communication.required_permission
        if required is None:
            return
        held: set[Permission] = set()
        for binding in principal.bindings:
            if binding.app_id is None or binding.app_id == communication.app_id:
                held.update(ROLE_PERMISSIONS[binding.role])
        if required not in held:
            raise PermissionDeniedError(
                f"a {communication.audience.value} communication is approved by "
                f"somebody holding {required.value}"
            )

    async def _rewrite(
        self,
        principal: Principal,
        incident_id: str,
        *,
        audience: Audience,
        subject: str,
        body: str,
        cited: Sequence[GraphNode],
        correcting: Optional[Communication] = None,
    ) -> Optional[tuple[str, str, str, str]]:
        """The model's pass over the deterministic text. Returns None on any
        failure, which is what leaves the template in place with its badge."""
        result = await self._llm.complete(
            CompletionRequest(
                purpose=CORRECTION_PURPOSE if correcting else PURPOSE,
                messages=(
                    Message(role="system", content=SYSTEM_FLOOR),
                    Message(
                        role="user",
                        content=_prompt(audience, subject, body, cited, correcting),
                    ),
                ),
                model_class=ModelClass.GENERAL,
                schema=DRAFT_SCHEMA,
                fallback=None,
                tenant_id=principal.tenant_id,
                incident_id=incident_id,
                max_tokens=900,
            )
        )
        if result.degraded or not isinstance(result.data, dict):
            return None
        written_subject = str(result.data.get("subject") or "").strip()
        written_body = str(result.data.get("body") or "").strip()
        if not written_subject or not written_body:
            logger.info(
                "the model's communication draft was empty; serving the template",
                extra={"incident_id": incident_id, "audience": audience.value},
            )
            return None
        return (
            written_subject[:200],
            written_body[:4000],
            result.model,
            ModelClass.GENERAL.value,
        )

    @staticmethod
    async def _require_incident(
        uow: IdentityUnitOfWork, tenant_id: str, incident_id: str
    ):
        incident = await uow.incidents.get(tenant_id, incident_id)
        if incident is None:
            raise IncidentNotFoundError(f"incident {incident_id} not found")
        return incident

    @staticmethod
    async def _require_communication(
        uow: IdentityUnitOfWork,
        tenant_id: str,
        incident_id: str,
        communication_id: str,
    ) -> Communication:
        communication = await uow.communications.get(tenant_id, communication_id)
        if communication is None or communication.incident_id != incident_id:
            raise CommunicationNotFoundError(
                f"communication {communication_id} not found"
            )
        return communication


# --- the deterministic writer --------------------------------------------------


def _default_grounding(
    nodes: Sequence[GraphNode], audience: Audience
) -> tuple[str, ...]:
    """What to write from when the caller did not choose.

    Facts and impacts first, because those are what an audience needs. A
    hypothesis is included only for responders: telling customers what the
    platform currently suspects is how a status page ends up retracting a cause.
    """
    wanted = [NodeType.FACT, NodeType.IMPACT]
    if audience is Audience.RESPONDERS:
        wanted.append(NodeType.HYPOTHESIS)
    chosen = [
        node.id
        for node in nodes
        if node.current and node.node_type in wanted
    ]
    return tuple(chosen[:12])


def _subject(incident, audience: Audience) -> str:
    if audience is Audience.CUSTOMER:
        return f"Service disruption affecting {incident.app_id}"
    return f"[{incident.severity.value}] {incident.reference}: {incident.title}"


def _body(incident, cited: Sequence[GraphNode], audience: Audience) -> str:
    """The floor. Complete, usable, and made only of things on the record.

    Untrusted node content is *not* quoted into a customer-facing body: a bridge
    message somebody typed is evidence for responders and is not something to
    publish on a status page.
    """
    lines = [
        f"{incident.reference} - {incident.title}",
        f"Severity {incident.severity.value}, state {incident.state.value}.",
        "",
    ]
    external = audience in (Audience.CUSTOMER, Audience.CUSTOMER_SUPPORT)
    for node in cited:
        if external and node.untrusted:
            continue
        prefix = {
            NodeType.FACT: "Known",
            NodeType.IMPACT: "Impact",
            NodeType.HYPOTHESIS: "Under investigation",
        }.get(node.node_type, "Note")
        lines.append(f"{prefix}: {node.label}")
    lines.append("")
    if external:
        lines.append(
            "We are working on it and will post another update as soon as we "
            "know more."
        )
    else:
        lines.append("Next update when the picture changes.")
    return "\n".join(lines)


def _contradiction_reason(stale: Mapping[str, GraphNode], gone: Sequence[str]) -> str:
    node = stale[gone[0]]
    what = "retracted" if node.status is NodeStatus.RETRACTED else "superseded"
    return (
        f"{len(gone)} of the facts this was written from have been {what} - "
        f"first: \"{node.label}\""
    )[:200]


def _prompt(
    audience: Audience,
    subject: str,
    body: str,
    cited: Sequence[GraphNode],
    correcting: Optional[Communication],
) -> str:
    block = (
        fence(
            tuple(
                UntrustedItem(ref=node.id, kind=node.node_type.value, text=node.label)
                for node in cited
            ),
            salt=f"comms:{audience.value}",
        )
        if cited
        else "(nothing on the record yet)"
    )
    correction = ""
    if correcting is not None:
        correction = (
            "\nTHIS IS A CORRECTION. What was published before:\n"
            f"{correcting.body}\n\n"
            "Say plainly what has changed and why. Do not pretend the earlier "
            "statement was not made.\n"
        )
    return (
        f"Rewrite an incident communication.\n\nAudience: "
        f"{AUDIENCE_GUIDANCE[audience]}\n\n"
        "Rules:\n"
        "- Use only the material below. Add nothing, promise nothing, and give "
        "no time estimate that is not already stated.\n"
        "- State a cause only if the material below states it as a fact. A "
        "hypothesis is described as under investigation, never as the cause.\n"
        "- No apology template, no marketing language, no blame.\n"
        f"{correction}\n"
        "THE DETERMINISTIC DRAFT (ground truth):\n"
        f"Subject: {subject}\n{body}\n\n"
        "THE INCIDENT RECORD:\n"
        f"{block}"
    )
