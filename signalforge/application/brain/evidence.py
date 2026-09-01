"""Executing the progressive evidence plan (REQ-054).

`domain/brain/evidence.py` decides *what* may be gathered and how much; this
executes it against the stores. The split is what makes the budget testable
without a database and the retrieval testable without a model.

The order is broad, aggregate, narrow, and each pass is bounded before the next
begins:

- **broad** costs four cheap structured reads and produces counts. It is what
  tells the rest of the pass whether there is anything to look at.
- **aggregate** collapses those into rows a person would write - the worst
  signal per family, alert *clusters* rather than alerts, the statements that
  changed what is believed. Bounded labels, no free-text dumps.
- **narrow** runs only per hypothesis, only under the query budget, and is the
  only pass that carries excerpts - each capped, each redacted, each carrying a
  `result_ref` so the full thing is one click away rather than in the prompt.

Two floors are applied here rather than left to the caller, because "the caller
remembers" is not a control:

- **Redaction.** Every excerpt taken from free text goes through
  `domain/ingest/redaction.mask` - the platform's only masking code, so this
  path and the ingest path cannot drift.
- **Untrusted marking.** Anything this platform did not write is flagged at the
  moment it is retrieved. By the time it reaches a prompt builder its origin is
  no longer visible, so the flag has to be set where the origin still is.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Callable, Mapping, Optional, Sequence

from signalforge.application.identity.uow import IdentityUnitOfWork
from signalforge.contracts.canonical import TopologySnapshot
from signalforge.domain.brain.evidence import (
    BudgetLedger,
    EvidenceBundle,
    EvidenceItem,
    EvidenceQuery,
    QueryBudget,
    SourceKind,
    Stage,
    aggregate_plan,
    broad_plan,
    narrow_plan,
)
from signalforge.domain.brain.similarity import IncidentFingerprint
from signalforge.domain.detection.engine import DetectionVerdict
from signalforge.domain.detection.anomaly import SignalStatus
from signalforge.domain.identity.ports import Clock
from signalforge.domain.incidents.changes import ChangeWindow
from signalforge.domain.incidents.entities import Incident
from signalforge.domain.incidents.lifecycle import LifecycleState
from signalforge.domain.ingest.redaction import mask
from signalforge.domain.onboarding.ports import TopologyVersion
from signalforge.domain.warroom.entities import IncidentTask, WarRoom

logger = logging.getLogger("signalforge.brain")

BRIDGE_LOOKBACK_H = 6
MAX_BRIDGE_MESSAGES = 60
MAX_PRIOR_INCIDENTS = 40
ONSET_WINDOW_S = 90 * 60.0
"""How close to the declaration a change has to be to count as temporally
proximate. Ninety minutes: wide enough for a slow rollout, narrow enough that
"something was deployed today" does not become evidence."""


@dataclass
class IncidentContext:
    """Everything one governor pass reads, gathered once.

    A single object rather than each agent doing its own retrieval, because the
    agents have to reason about a *consistent* view: a topology agent that read
    version 7 while the change agent read version 8 would produce a blast radius
    and a correlation that disagree, and nothing in the output would say why.
    """

    tenant_id: str
    incident_id: str
    app_id: str
    incident: Incident
    now: datetime
    room: Optional[WarRoom] = None
    verdict: Optional[DetectionVerdict] = None
    verdict_error: str = ""
    changes: tuple[ChangeWindow, ...] = ()
    topology: Optional[TopologyVersion] = None
    previous_topology: Optional[TopologyVersion] = None
    topology_versions: tuple[str, ...] = ()
    messages: tuple[Mapping[str, Any], ...] = ()
    tasks: tuple[IncidentTask, ...] = ()
    priors: tuple[IncidentFingerprint, ...] = ()
    prior_relevance: Mapping[str, float] = field(default_factory=dict)
    """What each similar incident's relevance was at the last pass, read from the
    graph. Without it "relevance movement" - a prior incident becoming *more*
    relevant as evidence arrives, which is the interesting event on S14 - would
    always read as zero, because the agent has no memory of its own."""
    bundle: Optional[EvidenceBundle] = None
    ledger: BudgetLedger = field(default_factory=BudgetLedger)

    @property
    def snapshot(self) -> Optional[TopologySnapshot]:
        return self.topology.snapshot if self.topology else None

    @property
    def services(self) -> tuple[str, ...]:
        """Names an agent may match a statement's subject against. The
        application and everything its map names - never inferred from text."""
        names = {self.app_id}
        if self.room is not None:
            names.update(self.room.radius.services)
        if self.snapshot is not None:
            names.update(
                node.name or node.node_id for node in self.snapshot.nodes
            )
        return tuple(sorted(name for name in names if name))


class ProgressiveEvidenceService:
    """Broad, then aggregate, then narrow - under a query budget throughout."""

    def __init__(
        self,
        uow_factory: Callable[[], IdentityUnitOfWork],
        clock: Clock,
        *,
        detection=None,
        bridge=None,
        metrics=None,
    ):
        self._uow_factory = uow_factory
        self._clock = clock
        self._detection = detection
        self._bridge = bridge
        self._metrics = metrics

    # -- pass one and two -----------------------------------------------------

    async def gather(
        self,
        tenant_id: str,
        incident: Incident,
        *,
        budget: Optional[QueryBudget] = None,
    ) -> IncidentContext:
        """The broad and aggregate passes, as one consistent view.

        Every retrieval inside is individually survivable: a detector that is
        down, a bridge with no connector and an application with no map each
        produce an empty result and a recorded reason, not an exception. The
        brain of an incident whose detector is broken is exactly the brain
        somebody needs most.
        """
        now = self._clock.now()
        ledger = BudgetLedger(budget)
        context = IncidentContext(
            tenant_id=tenant_id,
            incident_id=incident.id,
            app_id=incident.app_id,
            incident=incident,
            now=now,
            ledger=ledger,
        )

        async with self._uow_factory() as uow:
            context.room = await uow.war_rooms.get(tenant_id, incident.id)
            context.tasks = tuple(await uow.incident_tasks.list(tenant_id, incident.id))
            if ledger.take():
                context.changes = tuple(
                    await uow.changes.list(tenant_id, incident.app_id, limit=20)
                )
            if ledger.take():
                versions = list(
                    await uow.topologies.list(tenant_id, incident.app_id, limit=10)
                )
                context.topology = versions[0] if versions else None
                context.previous_topology = versions[1] if len(versions) > 1 else None
                context.topology_versions = tuple(v.version for v in versions)
            if ledger.take():
                context.priors = await self._priors(uow, tenant_id, incident)
                context.prior_relevance = await self._prior_relevance(
                    uow, tenant_id, incident.id
                )

        if ledger.take():
            context.verdict, context.verdict_error = await self._verdict(
                tenant_id, incident.app_id
            )
        if ledger.take():
            context.messages = await self._messages(context)

        broad = EvidenceBundle(
            incident_id=incident.id,
            stage=Stage.BROAD,
            gathered_at=now,
            queries=broad_plan(),
            counts=self._counts(context),
            summaries=(),
        )
        context.bundle = broad.merged(self._aggregate(context))
        if self._metrics is not None:
            self._metrics.increment(
                "signalforge_brain_evidence_queries_total",
                {"stage": Stage.AGGREGATE.value},
                ledger.spent,
            )
        return context

    def _counts(self, context: IncidentContext) -> dict[str, int]:
        verdict = context.verdict
        return {
            "changes": len(context.changes),
            "changes_open": sum(1 for change in context.changes if change.open),
            "topology_versions": len(context.topology_versions),
            "bridge_messages": len(context.messages),
            "prior_incidents": len(context.priors),
            "anomalous_signals": len(verdict.anomalous) if verdict else 0,
            "slo_burns": (
                sum(1 for burn in verdict.burns if burn.opens_watch) if verdict else 0
            ),
            "alert_clusters": len(verdict.storm.clusters) if verdict else 0,
            "tasks_open": sum(
                1 for task in context.tasks if task.status.value == "open"
            ),
        }

    def _aggregate(self, context: IncidentContext) -> EvidenceBundle:
        """Pass two: bounded rows, still no free-text dumps.

        The one place free text enters is a bridge excerpt, and it arrives
        masked and flagged untrusted.
        """
        items: list[EvidenceItem] = []
        summaries: list[str] = []
        verdict = context.verdict

        if verdict is not None:
            # The worst signal per family, not every signal: fifty rows saying
            # the same thing is one fact with a large font.
            worst: dict[str, Any] = {}
            for state in verdict.anomalous:
                held = worst.get(state.family.value)
                if held is None or (state.deviation or 0) > (held.deviation or 0):
                    worst[state.family.value] = state
            for family, state in sorted(worst.items()):
                items.append(
                    EvidenceItem(
                        ref=f"signal:{state.signal_key}:{state.region or 'all'}",
                        source=SourceKind.TELEMETRY,
                        label=(
                            f"{family}: {state.signal_key}"
                            + (f" [{state.region}]" if state.region else "")
                        ),
                        excerpt=(
                            f"value {state.value} against threshold "
                            f"{state.threshold} ({state.deviation:.1f} deviation), "
                            f"anomalous for {state.streak} evaluation(s)"
                        ),
                        result_ref=f"rollup:{state.signal_key}",
                        at=state.evaluated_at,
                        authority="observed",
                        detail={"family": family, "region": state.region},
                    )
                )
            for burn in verdict.burns:
                if not burn.opens_watch:
                    continue
                items.append(
                    EvidenceItem(
                        ref=f"slo:{burn.slo_id}",
                        source=SourceKind.TELEMETRY,
                        label=f"SLO {burn.name} burning ({burn.verdict.value})",
                        excerpt=burn.detail,
                        result_ref=f"slo:{burn.slo_id}",
                        authority="observed",
                    )
                )
            for cluster in verdict.storm.clusters[:6]:
                items.append(
                    EvidenceItem(
                        ref=f"alertcluster:{cluster.cluster_id}",
                        source=SourceKind.ALERT,
                        label=(
                            f"{cluster.size} alert(s) clustered on "
                            f"{cluster.root_node_id or cluster.root_alert_id}"
                        ),
                        excerpt=cluster.detail,
                        result_ref=f"alert:{cluster.root_alert_id}",
                        at=cluster.latest_at,
                        authority="observed",
                    )
                )
            summaries.append(
                f"Detection stance {verdict.stance.value}; health "
                f"{verdict.health.score if verdict.health.score is not None else '--'} "
                f"({verdict.health.band.value}); corroborated="
                f"{verdict.corroboration.corroborated}"
            )
        elif context.verdict_error:
            summaries.append(
                f"No detection verdict is available ({context.verdict_error}). "
                "Everything below is drawn from the declaration and from what "
                "people have said."
            )

        for change in context.changes[:6]:
            items.append(
                EvidenceItem(
                    ref=f"change:{change.change_id}",
                    source=SourceKind.CHANGE,
                    label=(
                        f"{change.change_type.value} on {change.service}"
                        + (f" {change.version}" if change.version else "")
                    ),
                    excerpt=change.detail(context.now),
                    result_ref=change.diff_ref or f"change:{change.change_id}",
                    at=change.started_at,
                    authority="authoritative",
                    detail={"open": change.open, "status": change.status.value},
                )
            )

        for message in context.messages[-12:]:
            text = str(message.get("text") or "")
            masked = mask(text)
            items.append(
                EvidenceItem(
                    ref=f"bridge:{message.get('message_id')}",
                    source=SourceKind.BRIDGE,
                    label=f"{message.get('author_display') or message.get('author') or 'bridge'}",
                    excerpt=masked,
                    result_ref=f"bridge:{message.get('message_id')}",
                    at=message.get("at"),
                    # Somebody else's words. Flagged here, where the origin is
                    # still visible, because a prompt builder cannot tell.
                    untrusted=True,
                    redacted=masked != text,
                    authority="observed",
                )
            )

        for prior in context.priors[:5]:
            items.append(
                EvidenceItem(
                    ref=f"incident:{prior.incident_id}",
                    source=SourceKind.HISTORY,
                    label=f"{prior.reference}: {prior.title}",
                    excerpt=prior.closure_reason or "no closure reason recorded",
                    result_ref=f"incident:{prior.incident_id}",
                    at=prior.declared_at,
                    authority="observed",
                    detail={"severity": prior.severity},
                )
            )

        return EvidenceBundle(
            incident_id=context.incident_id,
            stage=Stage.AGGREGATE,
            gathered_at=context.now,
            queries=aggregate_plan(),
            items=tuple(items),
            summaries=tuple(summaries),
        )

    # -- pass three -----------------------------------------------------------

    def narrow(
        self,
        context: IncidentContext,
        *,
        hypothesis_key: str,
        subject: str,
        budget: Optional[QueryBudget] = None,
    ) -> EvidenceBundle:
        """Only what would discriminate one hypothesis, under its own budget.

        Runs against the material already gathered rather than re-reading the
        stores: a narrow pass that went back to the database per hypothesis
        would multiply the cost of the thing the budget exists to bound, and
        every source it needs is already in the context.

        A refused query is *recorded*, not silently skipped: `budget_exhausted`
        travels onto the bundle and onto the screen, so an operator can tell
        "nothing was found" from "we stopped looking".
        """
        ledger = context.ledger
        wanted = narrow_plan(hypothesis_key, subject=subject, budget=budget)
        items: list[EvidenceItem] = []
        ran: list[EvidenceQuery] = []
        exhausted = False
        needle = subject.strip().lower()

        for query in wanted:
            if not ledger.take(hypothesis_key):
                exhausted = True
                break
            ran.append(query)
            if query.source is SourceKind.TELEMETRY and context.verdict is not None:
                for state in context.verdict.anomalous:
                    if needle and needle not in state.signal_key.lower():
                        continue
                    items.append(
                        EvidenceItem(
                            ref=f"signal:{state.signal_key}:{state.region or 'all'}",
                            source=SourceKind.TELEMETRY,
                            label=f"{state.family.value}: {state.signal_key}",
                            excerpt=(
                                f"{state.status.value}, value {state.value}, "
                                f"threshold {state.threshold}"
                            ),
                            result_ref=f"rollup:{state.signal_key}",
                            at=state.evaluated_at,
                            authority="observed",
                        )
                    )
            elif query.source is SourceKind.CHANGE:
                for change in context.changes:
                    if needle and needle not in change.service.lower():
                        continue
                    items.append(
                        EvidenceItem(
                            ref=f"change:{change.change_id}",
                            source=SourceKind.CHANGE,
                            label=f"{change.change_type.value} on {change.service}",
                            excerpt=change.detail(context.now),
                            result_ref=change.diff_ref or f"change:{change.change_id}",
                            at=change.started_at,
                            authority="authoritative",
                        )
                    )
            elif query.source is SourceKind.BRIDGE:
                for message in context.messages:
                    text = str(message.get("text") or "")
                    if needle and needle not in text.lower():
                        continue
                    masked = mask(text)
                    items.append(
                        EvidenceItem(
                            ref=f"bridge:{message.get('message_id')}",
                            source=SourceKind.BRIDGE,
                            label=str(
                                message.get("author_display")
                                or message.get("author")
                                or "bridge"
                            ),
                            excerpt=masked,
                            result_ref=f"bridge:{message.get('message_id')}",
                            at=message.get("at"),
                            untrusted=True,
                            redacted=masked != text,
                        )
                    )
            elif query.source is SourceKind.PROBE and context.verdict is not None:
                for state in context.verdict.states:
                    if state.status is not SignalStatus.ANOMALOUS:
                        continue
                    if state.basis.value != "probe":
                        continue
                    items.append(
                        EvidenceItem(
                            ref=f"probe:{state.signal_key}:{state.region or 'all'}",
                            source=SourceKind.PROBE,
                            label=f"probe {state.signal_key}",
                            excerpt=f"{state.status.value} in {state.region or 'all regions'}",
                            result_ref=f"probe:{state.signal_key}",
                            authority="observed",
                        )
                    )
            items = items[: query.limit * len(ran)]

        return EvidenceBundle(
            incident_id=context.incident_id,
            stage=Stage.NARROW,
            gathered_at=context.now,
            queries=tuple(ran),
            items=tuple(items),
            budget_exhausted=exhausted,
            detail={"hypothesis": hypothesis_key, "subject": subject},
        )

    # -- retrieval ------------------------------------------------------------

    async def _verdict(
        self, tenant_id: str, app_id: str
    ) -> tuple[Optional[DetectionVerdict], str]:
        if self._detection is None:
            return None, "no detector is wired on this node"
        try:
            return await self._detection.evaluate(tenant_id, app_id), ""
        except Exception as exc:  # pragma: no cover - defensive by design
            logger.info(
                "the detector could not be read for the incident brain",
                extra={"app_id": app_id, "error": f"{type(exc).__name__}: {exc}"},
            )
            return None, f"{type(exc).__name__}"

    async def _messages(self, context: IncidentContext) -> tuple[Mapping[str, Any], ...]:
        room = context.room
        if self._bridge is None or room is None or not room.has_bridge:
            return ()
        since = max(
            room.opened_at - timedelta(seconds=1),
            context.now - timedelta(hours=BRIDGE_LOOKBACK_H),
        )
        try:
            messages = await self._bridge.transcript(
                room, since=since, limit=MAX_BRIDGE_MESSAGES
            )
        except Exception as exc:  # pragma: no cover - defensive by design
            logger.info(
                "the bridge could not be read for the incident brain",
                extra={
                    "incident_id": context.incident_id,
                    "error": f"{type(exc).__name__}: {exc}",
                },
            )
            return ()
        return tuple(
            {
                "message_id": message.message_id,
                "author": message.author,
                "author_display": message.author_display,
                "text": message.text,
                "at": message.at,
                "simulated": message.simulated,
            }
            for message in messages
        )

    async def _prior_relevance(
        self, uow: IdentityUnitOfWork, tenant_id: str, incident_id: str
    ) -> dict[str, float]:
        """The relevance each similar incident was last given, from the graph.

        The graph is the agent's memory. Reading it back here rather than keeping
        a cache means a restart, a redelivery and a second node all see the same
        history - and that the movement shown on the screen is the difference
        between two things that were actually written down.
        """
        from signalforge.domain.brain.nodes import NodeType

        held = await uow.incident_graph.list(
            tenant_id, incident_id, types=[NodeType.EVIDENCE], limit=200
        )
        found: dict[str, float] = {}
        for node in held:
            source = str((node.detail or {}).get("source_incident") or "")
            relevance = (node.detail or {}).get("relevance")
            if source and isinstance(relevance, (int, float)):
                found[source] = float(relevance)
        return found

    async def _priors(
        self, uow: IdentityUnitOfWork, tenant_id: str, incident: Incident
    ) -> tuple[IncidentFingerprint, ...]:
        """Closed incidents in this tenant, reduced to fingerprints.

        Tenant-scoped by the repository, like every other read: history is one
        of the easiest places to leak one customer's incident into another's
        reasoning, and the boundary is the same one everywhere else.
        """
        closed = list(
            await uow.incidents.list(
                tenant_id,
                states=[LifecycleState.CLOSED],
                limit=MAX_PRIOR_INCIDENTS,
            )
        )
        found: list[IncidentFingerprint] = []
        for prior in closed:
            if prior.id == incident.id:
                continue
            nodes = list(
                await uow.incident_graph.list(
                    tenant_id, prior.id, current_only=False, limit=200
                )
            )
            found.append(fingerprint_of(prior, nodes))
        return tuple(found)


def fingerprint_of(incident: Incident, nodes: Sequence[Any] = ()) -> IncidentFingerprint:
    """One incident as similarity features. Pure, and used for both sides of a
    comparison so that the current incident and a prior one are reduced the same
    way - a similarity between two differently-built fingerprints is noise."""
    from signalforge.domain.brain.nodes import NodeStatus, NodeType

    proposal = incident.proposal
    services = set()
    if proposal is not None:
        services.update(ref.ref for ref in proposal.evidence if ref.ref)
    regions = {ref.region for ref in (proposal.evidence if proposal else ()) if ref.region}
    families = set(proposal.families) if proposal else set()
    change_types = {link.change_type for link in incident.change_windows}

    confirmed_cause = ""
    ruled_out: list[str] = []
    best = 0.0
    for node in nodes:
        if node.node_type is not NodeType.HYPOTHESIS:
            continue
        if node.status is NodeStatus.RETRACTED:
            ruled_out.append(node.label)
        elif node.status is NodeStatus.CURRENT and (node.confidence or 0.0) > best:
            best = node.confidence or 0.0
            confirmed_cause = node.label
    if best < 0.5:
        # A hypothesis the room never got behind is not a conclusion. Offering
        # it as one is exactly how history becomes a bad suggestion.
        confirmed_cause = ""

    return IncidentFingerprint(
        incident_id=incident.id,
        reference=incident.reference,
        app_id=incident.app_id,
        title=incident.title,
        declared_at=incident.declared_at,
        severity=incident.severity.value,
        signal_families=frozenset(families),
        services=frozenset(services),
        regions=frozenset(regions),
        change_types=frozenset(change_types),
        customer_facing=bool(proposal.customer_impacting) if proposal else False,
        corroborated=bool(proposal.corroborated) if proposal else False,
        closed_at=incident.closed_at,
        closure_reason=incident.closure_reason,
        confirmed_cause=confirmed_cause,
        ruled_out=tuple(ruled_out[:4]),
        text=f"{incident.title}. {incident.summary} {incident.closure_reason}".strip(),
    )
