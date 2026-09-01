"""The incident lifecycle use cases (REQ-043, REQ-044).

Four writes, and every one of them is a person: declare, change severity, move
state, close. There is no fifth entry point and no background job that can reach
any of them - the detector's own service has no import of this module, and the
state machine would refuse it if it did.

Each write is one transaction containing four things: the incident row, the
transition that records who moved it, the audit entry, and the incident-plane
event. Anything that leaves this process - raising an ITSM ticket - happens
*after* that commit and can never roll it back. That ordering is the whole
failure design of the file: an incident is declared because a person declared it,
and a ticket queue that is down, slow or absent changes nothing about that.

Reading is separate and deliberately unpermissioned within the tenant. The
frozen roles matrix gives the SME - the person who answers the page - no
permission at all, so any permission on the read path would exclude exactly the
people an incident exists to reach. Every write names one of the three incident
permissions instead.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Optional, Sequence

from signalforge.application.events.bus import EventBus
from signalforge.application.identity.principal import Principal
from signalforge.application.identity.uow import IdentityUnitOfWork
from signalforge.application.incidents.changes import ChangeWindowService
from signalforge.application.incidents.common import (
    CLOSED_TOPIC,
    DECLARED_TOPIC,
    SEVERITY_TOPIC,
    STATE_TOPIC,
    record,
)
from signalforge.application.incidents.itsm import IncidentTicketSync
from signalforge.application.onboarding.common import require_application
from signalforge.contracts.envelope import ActorType
from signalforge.domain.identity.ports import Clock
from signalforge.domain.incidents.changes import ChangeWindowView
from signalforge.domain.incidents.entities import (
    ChangeWindowLink,
    DeclaredProposal,
    EvidenceLink,
    Incident,
    IncidentTransition,
    SeveritySource,
)
from signalforge.domain.incidents.errors import (
    IncidentAlreadyOpenError,
    IncidentNotFoundError,
    IncidentValidationError,
)
from signalforge.domain.incidents.lifecycle import (
    TRANSITIONS,
    LifecycleState,
    assert_transition,
    is_human_only,
    parse_state,
    state_for_stance,
)
from signalforge.domain.onboarding.applications import Application
from signalforge.domain.onboarding.profile import Severity

logger = logging.getLogger("signalforge.lifecycle")

SETTABLE_STATES: tuple[LifecycleState, ...] = (
    LifecycleState.ACTIVE,
    LifecycleState.RECOVERING,
)
"""What `POST /incidents/{id}/state` may set. Closing has its own route because
it has its own permission, and a stance is not something a person sets on an
incident - detection owns those and recomputes them from measurements."""

REFERENCE_PREFIX = "INC"
ORDINAL_RETRIES = 2


@dataclass(frozen=True)
class IncidentView:
    """An incident and everything a screen reads beside it."""

    incident: Incident
    transitions: tuple[IncidentTransition, ...] = ()


@dataclass(frozen=True)
class LifecycleView:
    """One application's position in the lifecycle, from either source.

    `source` is the honest part. With no incident the state is a *stance* the
    detector recomputed five seconds ago; with one it is a decision a person made
    and nothing recomputes it.
    """

    app_id: str
    state: LifecycleState
    source: str
    at: datetime
    stance: str = "steady"
    incident: Optional[Incident] = None
    severity: Optional[Severity] = None
    change_window: Optional[ChangeWindowView] = None
    detection_error: str = ""
    health_score: Optional[float] = None
    health_band: str = ""
    corroborated: bool = False
    proposed_severity: Optional[Severity] = None
    reasons: tuple[str, ...] = ()

    @property
    def declared(self) -> bool:
        return self.incident is not None

    @property
    def next_states(self) -> tuple[str, ...]:
        """Every move the lifecycle allows from here, whoever makes it.

        Read by a screen to decide which buttons exist at all. Which of them a
        *person* may press is the `human_moves` half; the two are published
        separately so a client cannot render a machine transition as a button.
        """
        return tuple(sorted(state.value for state in TRANSITIONS[self.state]))

    @property
    def human_moves(self) -> tuple[str, ...]:
        return tuple(
            sorted(
                state.value
                for state in TRANSITIONS[self.state]
                if is_human_only(self.state, state)
            )
        )


class IncidentService:
    def __init__(
        self,
        uow_factory: Callable[[], IdentityUnitOfWork],
        bus: EventBus,
        clock: Clock,
        changes: ChangeWindowService,
        tickets: IncidentTicketSync,
        detection=None,
        metrics=None,
    ):
        self._uow_factory = uow_factory
        self._bus = bus
        self._clock = clock
        self._changes = changes
        self._tickets = tickets
        # Optional so a node with detection disabled can still run incidents: a
        # person declaring when the detector is down is the case that matters
        # most, not the one to fail on.
        self._detection = detection
        self._metrics = metrics
        self._on_declared: list = []
        self._on_closed: list = []
        self._closure_guards: list = []

    @property
    def closure_guards(self) -> list:
        """Checks that may refuse a closure (S6-T05, REQ-065).

        A list of `async (incident) -> None` callables the composition root
        fills, exactly like `on_declared` - so the lifecycle has no import of
        layered validation and a node without it runs Sprint 4's closure
        unchanged. Unlike the hooks, a guard's exception is *not* swallowed:
        that is the whole point of it. A guard that raises stops the close, and
        the incident does not move.
        """
        return self._closure_guards

    @property
    def on_declared(self) -> list:
        """Hooks the composition root fills - the war room (S4-T03).

        Hooks rather than a call, so the arrow keeps pointing one way: the
        lifecycle has no import of the war room, and a node could run incidents
        with the room switched off without changing a line here. Each runs after
        the declaration commits and each survives its own failure, on exactly the
        rule the ITSM sync follows: a room that could not be assembled must never
        be able to un-declare an incident a person declared.
        """
        return self._on_declared

    @property
    def on_closed(self) -> list:
        """Hooks run after an incident closes - standing the war room down."""
        return self._on_closed

    # -- reads ---------------------------------------------------------------

    async def get(self, principal: Principal, incident_id: str) -> IncidentView:
        async with self._uow_factory() as uow:
            incident = await self._require(uow, principal.tenant_id, incident_id)
            transitions = tuple(
                await uow.incident_transitions.list(principal.tenant_id, incident_id)
            )
        return IncidentView(incident=incident, transitions=transitions)

    async def list(
        self,
        principal: Principal,
        *,
        app_id: Optional[str] = None,
        state: Optional[str] = None,
        limit: int = 50,
    ) -> Sequence[Incident]:
        states = None
        if state is not None:
            states = (
                tuple(LifecycleState(value) for value in ("active", "recovering"))
                if state == "open"
                else (parse_state(state),)
            )
        async with self._uow_factory() as uow:
            return list(
                await uow.incidents.list(
                    principal.tenant_id, app_id=app_id, states=states, limit=limit
                )
            )

    async def app_of(self, tenant_id: str, incident_id: str) -> str:
        """Which application an incident is about.

        The RBAC dependency for incident-scoped routes reads this, so a per-app
        role binding is evaluated against the application the incident names
        rather than against nothing.
        """
        async with self._uow_factory() as uow:
            incident = await self._require(uow, tenant_id, incident_id)
        return incident.app_id

    async def lifecycle(self, principal: Principal, app_id: str) -> LifecycleView:
        tenant_id = principal.tenant_id
        async with self._uow_factory() as uow:
            await require_application(uow, tenant_id, app_id)
            incident = await uow.incidents.open_for_app(tenant_id, app_id)
        window = await self._changes.view(tenant_id, app_id)
        stance, verdict, error = await self._stance(principal, app_id)
        state = incident.state if incident else state_for_stance(stance)
        return LifecycleView(
            app_id=app_id,
            state=state,
            source="incident" if incident else "detection",
            at=self._clock.now(),
            stance=stance,
            incident=incident,
            severity=incident.severity if incident else None,
            change_window=window,
            detection_error=error,
            health_score=verdict.health.score if verdict else None,
            health_band=verdict.health.band.value if verdict else "",
            corroborated=bool(verdict and verdict.corroboration.corroborated),
            proposed_severity=(
                verdict.severity.severity if verdict and verdict.severity else None
            ),
            reasons=tuple(verdict.reasons) if verdict else (),
        )

    # -- declare -------------------------------------------------------------

    async def declare(
        self,
        principal: Principal,
        app_id: str,
        *,
        title: Optional[str] = None,
        summary: str = "",
        severity: Optional[str] = None,
        reason: str = "",
    ) -> IncidentView:
        tenant_id = principal.tenant_id
        async with self._uow_factory() as uow:
            application = await require_application(uow, tenant_id, app_id)
            open_already = await uow.incidents.open_for_app(tenant_id, app_id)
        self._refuse_second(open_already, app_id)

        stance, verdict, error = await self._stance(principal, app_id)
        proposal = _proposal_of(stance, verdict, error)
        chosen, source = _severity_of(severity, proposal)
        windows = await self._changes.links(tenant_id, app_id)

        # The floor, stated at the point of use as well as inside the entity:
        # this is the move only a person makes.
        assert_transition(
            state_for_stance(stance),
            LifecycleState.ACTIVE,
            by=ActorType.HUMAN,
            what=f"application {app_id}",
        )

        now = self._clock.now()
        incident = await self._insert(
            principal,
            application,
            title=title or _default_title(application, chosen, stance),
            summary=summary,
            severity=chosen,
            source=source,
            stance=stance,
            proposal=proposal,
            windows=windows,
            reason=reason,
            now=now,
        )
        self._transition_metric(
            state_for_stance(stance), LifecycleState.ACTIVE, ActorType.HUMAN
        )
        # After the commit, never inside it: a ticket queue that is down must not
        # be able to un-declare an incident a person declared.
        incident = await self._tickets.sync(incident)
        await self._fire(self._on_declared, incident, principal)
        return await self.get(principal, incident.id)

    async def _fire(self, hooks: Sequence, incident: Incident, *args) -> None:
        """Run the composition root's hooks, each surviving its own failure.

        The declaration is already committed by the time this runs, and nothing
        here may undo it. A war room that could not be assembled is a war room
        that could not be assembled; it is not a reason for a P0 to have failed
        to be declared.
        """
        for hook in hooks:
            try:
                await hook(incident, *args)
            except Exception as exc:  # pragma: no cover - defensive by design
                logger.warning(
                    "a lifecycle hook failed; the incident stands",
                    extra={
                        "incident_id": incident.id,
                        "hook": getattr(hook, "__qualname__", repr(hook)),
                        "error": f"{type(exc).__name__}: {exc}",
                    },
                )

    async def _insert(
        self,
        principal: Principal,
        application: Application,
        *,
        title: str,
        summary: str,
        severity: Severity,
        source: SeveritySource,
        stance: str,
        proposal: DeclaredProposal,
        windows: tuple[ChangeWindowLink, ...],
        reason: str,
        now: datetime,
    ) -> Incident:
        tenant_id = principal.tenant_id
        app_id = application.app_id
        last: Optional[Exception] = None
        for _attempt in range(ORDINAL_RETRIES + 1):
            async with self._uow_factory() as uow:
                # Re-read inside the transaction: two commanders pressing Declare
                # at the same moment is a normal thing for a pager to cause.
                open_already = await uow.incidents.open_for_app(tenant_id, app_id)
                self._refuse_second(open_already, app_id)
                ordinal = await uow.incidents.next_ordinal(tenant_id)
                incident = Incident(
                    id=str(uuid.uuid4()),
                    tenant_id=tenant_id,
                    app_id=app_id,
                    reference=f"{REFERENCE_PREFIX}-{ordinal:06d}",
                    ordinal=ordinal,
                    title=title,
                    summary=summary,
                    severity=severity,
                    severity_source=source,
                    state=LifecycleState.ACTIVE,
                    stance_at_declare=stance,
                    declared_at=now,
                    declared_by=principal.user_id,
                    declared_by_display=principal.display_name or "",
                    updated_at=now,
                    proposal=proposal,
                    change_windows=windows,
                )
                try:
                    await uow.incidents.add(incident)
                except Exception as exc:  # a reference collision, nothing else
                    last = exc
                    continue
                await uow.incident_transitions.append(
                    IncidentTransition(
                        tenant_id=tenant_id,
                        incident_id=incident.id,
                        app_id=app_id,
                        from_state=state_for_stance(stance),
                        to_state=LifecycleState.ACTIVE,
                        at=now,
                        actor_type=ActorType.HUMAN,
                        actor_id=principal.user_id,
                        reason=reason,
                        severity_after=severity,
                        detail={"severity_source": source.value},
                    )
                )
                await record(
                    uow,
                    self._bus,
                    self._clock,
                    principal=principal,
                    app_id=app_id,
                    incident_id=incident.id,
                    topic=DECLARED_TOPIC,
                    payload=_declared_payload(incident, reason),
                )
                await uow.commit()
                return incident
        raise IncidentValidationError(
            "could not allocate an incident reference; another declaration took "
            f"the number twice in a row ({last})"
        )

    # -- moves ---------------------------------------------------------------

    async def change_severity(
        self, principal: Principal, incident_id: str, severity: str, *, reason: str = ""
    ) -> IncidentView:
        target = _parse_severity(severity)
        async with self._uow_factory() as uow:
            incident = await self._require(uow, principal.tenant_id, incident_id)
        now = self._clock.now()
        updated = incident.with_severity(target, at=now, by=ActorType.HUMAN)
        if updated is incident:
            # Already this severity. Not a decision, so not a transition and not
            # an event - a screen re-posting what is true must not fill a
            # timeline people are reading during an incident.
            return await self.get(principal, incident_id)

        async with self._uow_factory() as uow:
            await uow.incidents.update(updated)
            await uow.incident_transitions.append(
                IncidentTransition(
                    tenant_id=updated.tenant_id,
                    incident_id=updated.id,
                    app_id=updated.app_id,
                    from_state=updated.state,
                    to_state=updated.state,
                    at=now,
                    actor_type=ActorType.HUMAN,
                    actor_id=principal.user_id,
                    reason=reason,
                    severity_before=incident.severity,
                    severity_after=updated.severity,
                    detail={"kind": "severity"},
                )
            )
            await record(
                uow,
                self._bus,
                self._clock,
                principal=principal,
                app_id=updated.app_id,
                incident_id=updated.id,
                topic=SEVERITY_TOPIC,
                payload={
                    "incident_id": updated.id,
                    "reference": updated.reference,
                    "app_id": updated.app_id,
                    "severity": updated.severity.value,
                    "previous_severity": incident.severity.value,
                    "proposed_severity": (
                        updated.proposal.severity.value
                        if updated.proposal and updated.proposal.severity
                        else None
                    ),
                    "reason": reason,
                    "changed_by_human": True,
                },
            )
            await uow.commit()
        await self._tickets.sync(updated)
        return await self.get(principal, incident_id)

    async def set_state(
        self, principal: Principal, incident_id: str, state: str, *, reason: str = ""
    ) -> IncidentView:
        target = parse_state(state, allowed=SETTABLE_STATES)
        return await self._move(
            principal, incident_id, target, reason=reason, topic=STATE_TOPIC
        )

    async def close(
        self, principal: Principal, incident_id: str, *, reason: str = ""
    ) -> IncidentView:
        """A person signs it off - if nothing is standing in the way.

        The guards run *before* the transition and their refusal propagates.
        Closing an incident while the platform's own customer validation says
        people are still affected is the one thing this lifecycle will not do,
        and there is no override parameter: the way past a failing gate is to
        fix the thing or to re-validate, not to tell the platform to look away.
        """
        async with self._uow_factory() as uow:
            incident = await self._require(uow, principal.tenant_id, incident_id)
        for guard in self._closure_guards:
            await guard(incident)
        return await self._move(
            principal,
            incident_id,
            LifecycleState.CLOSED,
            reason=reason,
            topic=CLOSED_TOPIC,
        )

    async def _move(
        self,
        principal: Principal,
        incident_id: str,
        target: LifecycleState,
        *,
        reason: str,
        topic: str,
    ) -> IncidentView:
        async with self._uow_factory() as uow:
            incident = await self._require(uow, principal.tenant_id, incident_id)
        now = self._clock.now()
        updated = incident.moved_to(
            target,
            at=now,
            by=ActorType.HUMAN,
            actor_id=principal.user_id,
            reason=reason,
        )
        async with self._uow_factory() as uow:
            await uow.incidents.update(updated)
            await uow.incident_transitions.append(
                IncidentTransition(
                    tenant_id=updated.tenant_id,
                    incident_id=updated.id,
                    app_id=updated.app_id,
                    from_state=incident.state,
                    to_state=updated.state,
                    at=now,
                    actor_type=ActorType.HUMAN,
                    actor_id=principal.user_id,
                    reason=reason,
                    severity_before=incident.severity,
                    severity_after=updated.severity,
                )
            )
            await record(
                uow,
                self._bus,
                self._clock,
                principal=principal,
                app_id=updated.app_id,
                incident_id=updated.id,
                topic=topic,
                payload={
                    "incident_id": updated.id,
                    "reference": updated.reference,
                    "app_id": updated.app_id,
                    "state": updated.state.value,
                    "previous_state": incident.state.value,
                    "severity": updated.severity.value,
                    "reason": reason,
                    "age_s": updated.age_s(now),
                    "closed_by_human": target is LifecycleState.CLOSED,
                },
            )
            await uow.commit()
        self._transition_metric(incident.state, updated.state, ActorType.HUMAN)
        await self._tickets.sync(updated)
        if target is LifecycleState.CLOSED:
            await self._fire(self._on_closed, updated)
        return await self.get(principal, incident_id)

    async def resync_ticket(
        self, principal: Principal, incident_id: str
    ) -> IncidentView:
        """A person overriding the retry cadence: try the ITSM again, now."""
        async with self._uow_factory() as uow:
            incident = await self._require(uow, principal.tenant_id, incident_id)
        await self._tickets.sync(incident)
        return await self.get(principal, incident_id)

    # -- internals -----------------------------------------------------------

    @staticmethod
    async def _require(
        uow: IdentityUnitOfWork, tenant_id: str, incident_id: str
    ) -> Incident:
        incident = await uow.incidents.get(tenant_id, incident_id)
        if incident is None:
            # Identical whether it belongs to another tenant or to nobody
            # (REQ-013): a 404 must not confirm that an id exists elsewhere.
            raise IncidentNotFoundError(f"incident {incident_id} not found")
        return incident

    @staticmethod
    def _refuse_second(open_already: Optional[Incident], app_id: str) -> None:
        if open_already is None:
            return
        raise IncidentAlreadyOpenError(
            f"application {app_id} already has an open incident "
            f"{open_already.reference} ({open_already.severity.value}, "
            f"{open_already.state.value}); join it or close it rather than "
            "declaring a second one"
        )

    async def _stance(self, principal: Principal, app_id: str):
        """What detection currently believes, and why not if it cannot say.

        A read, never an evaluation: declaring must not be able to advance a
        breach streak, and the answer has to be the one that was on the screen
        the person was looking at.
        """
        if self._detection is None:
            return "unknown", None, "detection is not enabled on this node"
        try:
            verdict = await self._detection.snapshot(principal, app_id)
        except Exception as exc:
            logger.warning(
                "detection could not be read while handling a lifecycle request",
                extra={"app_id": app_id, "error": f"{type(exc).__name__}: {exc}"},
            )
            return "unknown", None, f"{type(exc).__name__}: {exc}"[:300]
        return verdict.stance.value, verdict, ""

    def _transition_metric(
        self, before: LifecycleState, after: LifecycleState, actor: ActorType
    ) -> None:
        if self._metrics is None:
            return
        self._metrics.increment(
            "signalforge_incident_transitions_total",
            {"from_state": before.value, "to_state": after.value, "actor": actor.value},
        )

    async def refresh_gauges(self) -> None:
        """Install-wide counts for the unauthenticated scrape. Counts only."""
        if self._metrics is None:
            return
        async with self._uow_factory() as uow:
            by_severity = await uow.incidents.count_open()
        for severity in Severity:
            self._metrics.set_gauge(
                "signalforge_incidents_open",
                float(by_severity.get(severity.value, 0)),
                {"severity": severity.value},
            )
        self._metrics.set_gauge(
            "signalforge_change_windows_open", float(await self._changes.count_open())
        )


# --- pure helpers -------------------------------------------------------------


def _default_title(application: Application, severity: Severity, stance: str) -> str:
    return f"{severity.value} on {application.name or application.app_id} ({stance})"


def _parse_severity(raw: str) -> Severity:
    try:
        return Severity(str(raw).strip().upper())
    except ValueError as exc:
        raise IncidentValidationError(
            f"{raw!r} is not a severity; one of {[s.value for s in Severity]}"
        ) from exc


def _severity_of(
    requested: Optional[str], proposal: DeclaredProposal
) -> tuple[Severity, SeveritySource]:
    """The severity an incident is declared at, and where it came from.

    Both paths are a human act - somebody pressed Declare either way - but
    accepting what the application's own matrix computed and typing a different
    answer are different decisions, and the record says which.
    """
    if requested is not None:
        return _parse_severity(requested), SeveritySource.CHOSEN
    if proposal.severity is not None:
        return proposal.severity, SeveritySource.PROPOSED
    raise IncidentValidationError(
        "detection is not proposing a severity for this application"
        + (f" ({proposal.unavailable})" if proposal.unavailable else "")
        + ", so the declaration has to name one: severity is P0, P1, P2 or P3"
    )


def _proposal_of(stance: str, verdict, error: str) -> DeclaredProposal:
    if verdict is None:
        return DeclaredProposal(stance=stance, unavailable=error or "no verdict")
    severity = verdict.severity
    impact = severity.impact if severity is not None else None
    return DeclaredProposal(
        stance=stance,
        severity=severity.severity if severity else None,
        rule=severity.rule if severity else "",
        corroborated=verdict.corroboration.corroborated,
        families=tuple(verdict.corroboration.families),
        health_score=verdict.health.score,
        health_band=verdict.health.band.value,
        regions_affected=impact.regions_affected if impact else 0,
        regions_total=impact.regions_total if impact else 0,
        success_rate_pct=impact.success_rate_pct if impact else None,
        journey_tier=impact.journey_tier if impact else None,
        customer_impacting=bool(impact.customer_impacting) if impact else False,
        reasons=tuple(verdict.reasons),
        detail=severity.detail if severity else verdict.corroboration.reason,
        evidence=tuple(
            EvidenceLink(
                kind=ref.kind,
                ref=ref.ref,
                detail=ref.detail,
                family=ref.family,
                region=ref.region,
                customer_facing=ref.customer_facing,
                at=ref.at,
            )
            for ref in (
                severity.evidence if severity else verdict.corroboration.evidence
            )
        ),
    )


def _declared_payload(incident: Incident, reason: str) -> dict[str, Any]:
    proposal = incident.proposal
    return {
        "incident_id": incident.id,
        "reference": incident.reference,
        "app_id": incident.app_id,
        "title": incident.title,
        "severity": incident.severity.value,
        "severity_source": incident.severity_source.value,
        "state": incident.state.value,
        "stance_at_declare": incident.stance_at_declare,
        "declared": True,
        "declared_by_human": True,
        "overrode_detection": incident.overrode_detection,
        "reason": reason,
        "proposed_severity": (
            proposal.severity.value if proposal and proposal.severity else None
        ),
        "corroborated": bool(proposal and proposal.corroborated),
        "health_score": proposal.health_score if proposal else None,
        "evidence": [
            {"kind": ref.kind, "ref": ref.ref, "detail": ref.detail}
            for ref in (proposal.evidence if proposal else ())
        ],
        "change_windows_open": [
            {
                "change_id": link.change_id,
                "change_type": link.change_type,
                "service": link.service,
                "version": link.version,
            }
            for link in incident.change_windows
        ],
    }
