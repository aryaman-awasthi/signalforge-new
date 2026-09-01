"""Incident lifecycle repositories (REQ-043, REQ-044, REQ-013).

Tenant isolation lives here, not in the handlers: every read and write carries
the tenant_id predicate inside the class, and a cross-tenant get returns None so
the service above answers the same 404 it would for an id that exists nowhere.

Two methods deliberately span tenants and both are counts or work lists rather
than reads of content: `needing_sync` is the ITSM retry job's queue - one job
serves the install, exactly like the connector health loop - and the two
`count_open` methods feed gauges on an unauthenticated scrape, so they return
numbers and never rows.
"""

from __future__ import annotations

from typing import Any, Mapping, Optional, Sequence

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from signalforge.contracts.canonical import ChangeStatus, ChangeType, TicketStatus
from signalforge.contracts.envelope import ActorType
from signalforge.domain.incidents.changes import ChangeWindow
from signalforge.domain.incidents.entities import (
    ChangeWindowLink,
    DeclaredProposal,
    EvidenceLink,
    Incident,
    IncidentTransition,
    SeveritySource,
    TicketSync,
    TicketSyncState,
)
from signalforge.domain.incidents.lifecycle import LifecycleState
from signalforge.domain.incidents.ports import (
    ChangeWindowStore,
    IncidentRepository,
    IncidentTransitionRepository,
)
from signalforge.domain.onboarding.profile import Severity
from signalforge.infrastructure.persistence.models import (
    ChangeWindowRow,
    IncidentRow,
    IncidentTransitionRow,
)


class SqlIncidentRepository(IncidentRepository):
    def __init__(self, session: AsyncSession):
        self._s = session

    async def add(self, incident: Incident) -> None:
        self._s.add(IncidentRow(**_incident_values(incident) | {"id": incident.id}))
        await self._s.flush()

    async def get(self, tenant_id: str, incident_id: str) -> Optional[Incident]:
        row = (
            await self._s.execute(
                select(IncidentRow).where(
                    IncidentRow.tenant_id == tenant_id,
                    IncidentRow.id == incident_id,
                )
            )
        ).scalars().first()
        return _to_incident(row)

    async def update(self, incident: Incident) -> None:
        await self._s.execute(
            update(IncidentRow)
            .where(
                IncidentRow.tenant_id == incident.tenant_id,
                IncidentRow.id == incident.id,
            )
            .values(**_incident_values(incident))
        )
        await self._s.flush()

    async def list(
        self,
        tenant_id: str,
        *,
        app_id: Optional[str] = None,
        states: Optional[Sequence[LifecycleState]] = None,
        limit: int = 50,
    ) -> Sequence[Incident]:
        stmt = select(IncidentRow).where(IncidentRow.tenant_id == tenant_id)
        if app_id is not None:
            stmt = stmt.where(IncidentRow.app_id == app_id)
        if states:
            stmt = stmt.where(IncidentRow.state.in_([s.value for s in states]))
        stmt = stmt.order_by(IncidentRow.declared_at.desc(), IncidentRow.id).limit(
            max(1, min(int(limit), 200))
        )
        rows = (await self._s.execute(stmt)).scalars().all()
        return [_to_incident(row) for row in rows]

    async def open_for_app(self, tenant_id: str, app_id: str) -> Optional[Incident]:
        row = (
            await self._s.execute(
                select(IncidentRow)
                .where(
                    IncidentRow.tenant_id == tenant_id,
                    IncidentRow.app_id == app_id,
                    IncidentRow.state.in_(
                        [LifecycleState.ACTIVE.value, LifecycleState.RECOVERING.value]
                    ),
                )
                .order_by(IncidentRow.declared_at)
                .limit(1)
            )
        ).scalars().first()
        return _to_incident(row)

    async def next_ordinal(self, tenant_id: str) -> int:
        highest = (
            await self._s.execute(
                select(func.max(IncidentRow.ordinal)).where(
                    IncidentRow.tenant_id == tenant_id
                )
            )
        ).scalar()
        return int(highest or 0) + 1

    async def needing_sync(self, limit: int = 50) -> Sequence[Incident]:
        rows = (
            await self._s.execute(
                select(IncidentRow)
                .where(
                    IncidentRow.ticket_state.in_(
                        [
                            state.value
                            for state in TicketSyncState
                            if state.needs_retry
                        ]
                    )
                )
                .order_by(IncidentRow.updated_at)
                .limit(max(1, min(int(limit), 200)))
            )
        ).scalars().all()
        return [_to_incident(row) for row in rows]

    async def count_open(self) -> dict[str, int]:
        rows = (
            await self._s.execute(
                select(IncidentRow.severity, func.count())
                .where(
                    IncidentRow.state.in_(
                        [LifecycleState.ACTIVE.value, LifecycleState.RECOVERING.value]
                    )
                )
                .group_by(IncidentRow.severity)
            )
        ).all()
        return {str(severity): int(count) for severity, count in rows}


class SqlIncidentTransitionRepository(IncidentTransitionRepository):
    def __init__(self, session: AsyncSession):
        self._s = session

    async def append(self, transition: IncidentTransition) -> None:
        self._s.add(
            IncidentTransitionRow(
                tenant_id=transition.tenant_id,
                incident_id=transition.incident_id,
                app_id=transition.app_id,
                from_state=transition.from_state.value,
                to_state=transition.to_state.value,
                at=transition.at,
                actor_type=transition.actor_type.value,
                actor_id=transition.actor_id,
                reason=transition.reason,
                severity_before=(
                    transition.severity_before.value
                    if transition.severity_before
                    else None
                ),
                severity_after=(
                    transition.severity_after.value if transition.severity_after else None
                ),
                detail=dict(transition.detail),
            )
        )
        await self._s.flush()

    async def list(
        self, tenant_id: str, incident_id: str, *, limit: int = 200
    ) -> Sequence[IncidentTransition]:
        rows = (
            await self._s.execute(
                select(IncidentTransitionRow)
                .where(
                    IncidentTransitionRow.tenant_id == tenant_id,
                    IncidentTransitionRow.incident_id == incident_id,
                )
                .order_by(IncidentTransitionRow.id)
                .limit(max(1, min(int(limit), 500)))
            )
        ).scalars().all()
        return [
            IncidentTransition(
                tenant_id=row.tenant_id,
                incident_id=row.incident_id,
                app_id=row.app_id,
                from_state=LifecycleState(row.from_state),
                to_state=LifecycleState(row.to_state),
                at=row.at,
                actor_type=ActorType(row.actor_type),
                actor_id=row.actor_id,
                reason=row.reason or "",
                severity_before=(
                    Severity(row.severity_before) if row.severity_before else None
                ),
                severity_after=(
                    Severity(row.severity_after) if row.severity_after else None
                ),
                detail=dict(row.detail or {}),
            )
            for row in rows
        ]


class SqlChangeWindowStore(ChangeWindowStore):
    def __init__(self, session: AsyncSession):
        self._s = session

    async def get(
        self, tenant_id: str, app_id: str, change_id: str
    ) -> Optional[ChangeWindow]:
        row = (
            await self._s.execute(
                select(ChangeWindowRow).where(
                    ChangeWindowRow.tenant_id == tenant_id,
                    ChangeWindowRow.app_id == app_id,
                    ChangeWindowRow.change_id == change_id,
                )
            )
        ).scalars().first()
        return _to_window(row)

    async def put(self, window: ChangeWindow) -> None:
        values = _window_values(window)
        result = await self._s.execute(
            update(ChangeWindowRow)
            .where(
                ChangeWindowRow.tenant_id == window.tenant_id,
                ChangeWindowRow.app_id == window.app_id,
                ChangeWindowRow.change_id == window.change_id,
            )
            .values(**values)
        )
        if not result.rowcount:
            self._s.add(
                ChangeWindowRow(
                    tenant_id=window.tenant_id,
                    app_id=window.app_id,
                    change_id=window.change_id,
                    **values,
                )
            )
        await self._s.flush()

    async def list(
        self, tenant_id: str, app_id: str, *, limit: int = 100
    ) -> Sequence[ChangeWindow]:
        rows = (
            await self._s.execute(
                select(ChangeWindowRow)
                .where(
                    ChangeWindowRow.tenant_id == tenant_id,
                    ChangeWindowRow.app_id == app_id,
                )
                .order_by(ChangeWindowRow.started_at.desc())
                .limit(max(1, min(int(limit), 500)))
            )
        ).scalars().all()
        return [_to_window(row) for row in rows]

    async def count_open(self) -> int:
        return int(
            (
                await self._s.execute(
                    select(func.count()).select_from(ChangeWindowRow).where(
                        ChangeWindowRow.announced_completed.is_(False)
                    )
                )
            ).scalar()
            or 0
        )


# --- mapping ------------------------------------------------------------------


def _incident_values(incident: Incident) -> dict[str, Any]:
    ticket = incident.ticket
    return {
        "tenant_id": incident.tenant_id,
        "app_id": incident.app_id,
        "reference": incident.reference,
        "ordinal": incident.ordinal,
        "title": incident.title,
        "summary": incident.summary,
        "severity": incident.severity.value,
        "severity_source": incident.severity_source.value,
        "state": incident.state.value,
        "stance_at_declare": incident.stance_at_declare,
        "declared_at": incident.declared_at,
        "declared_by": incident.declared_by,
        "declared_by_display": incident.declared_by_display,
        "updated_at": incident.updated_at,
        "recovering_at": incident.recovering_at,
        "closed_at": incident.closed_at,
        "closed_by": incident.closed_by,
        "closure_reason": incident.closure_reason,
        "proposal": _proposal_json(incident.proposal),
        "change_windows": [_link_json(link) for link in incident.change_windows],
        "ticket_state": ticket.state.value,
        "ticket_id": ticket.ticket_id,
        "ticket_reference": ticket.reference,
        "ticket_status": ticket.status.value if ticket.status else None,
        "ticket_adapter_id": ticket.adapter_id,
        "ticket_binding_id": ticket.binding_id,
        "ticket_attempts": ticket.attempts,
        "ticket_detail": ticket.detail,
        "ticket_last_attempt_at": ticket.last_attempt_at,
        "ticket_synced_at": ticket.synced_at,
    }


def _to_incident(row: Optional[IncidentRow]) -> Optional[Incident]:
    if row is None:
        return None
    return Incident(
        id=row.id,
        tenant_id=row.tenant_id,
        app_id=row.app_id,
        reference=row.reference,
        ordinal=row.ordinal,
        title=row.title,
        summary=row.summary or "",
        severity=Severity(row.severity),
        severity_source=SeveritySource(row.severity_source),
        state=LifecycleState(row.state),
        stance_at_declare=row.stance_at_declare or "",
        declared_at=row.declared_at,
        declared_by=row.declared_by,
        declared_by_display=row.declared_by_display or "",
        updated_at=row.updated_at,
        recovering_at=row.recovering_at,
        closed_at=row.closed_at,
        closed_by=row.closed_by,
        closure_reason=row.closure_reason or "",
        proposal=_proposal_from(row.proposal),
        change_windows=tuple(_link_from(item) for item in (row.change_windows or [])),
        ticket=TicketSync(
            state=TicketSyncState(row.ticket_state),
            ticket_id=row.ticket_id,
            reference=row.ticket_reference,
            status=TicketStatus(row.ticket_status) if row.ticket_status else None,
            adapter_id=row.ticket_adapter_id,
            binding_id=row.ticket_binding_id,
            attempts=row.ticket_attempts or 0,
            detail=row.ticket_detail or "",
            last_attempt_at=row.ticket_last_attempt_at,
            synced_at=row.ticket_synced_at,
        ),
    )


def _proposal_json(proposal: Optional[DeclaredProposal]) -> Optional[dict[str, Any]]:
    if proposal is None:
        return None
    return {
        "stance": proposal.stance,
        "severity": proposal.severity.value if proposal.severity else None,
        "rule": proposal.rule,
        "corroborated": proposal.corroborated,
        "families": list(proposal.families),
        "health_score": proposal.health_score,
        "health_band": proposal.health_band,
        "regions_affected": proposal.regions_affected,
        "regions_total": proposal.regions_total,
        "success_rate_pct": proposal.success_rate_pct,
        "journey_tier": proposal.journey_tier,
        "customer_impacting": proposal.customer_impacting,
        "reasons": list(proposal.reasons),
        "detail": proposal.detail,
        "unavailable": proposal.unavailable,
        "evidence": [
            {
                "kind": ref.kind,
                "ref": ref.ref,
                "detail": ref.detail,
                "family": ref.family,
                "region": ref.region,
                "customer_facing": ref.customer_facing,
                "at": ref.at.isoformat() if ref.at else None,
            }
            for ref in proposal.evidence
        ],
    }


def _proposal_from(raw: Optional[Mapping[str, Any]]) -> Optional[DeclaredProposal]:
    if not raw:
        return None
    return DeclaredProposal(
        stance=str(raw.get("stance") or ""),
        severity=Severity(raw["severity"]) if raw.get("severity") else None,
        rule=str(raw.get("rule") or ""),
        corroborated=bool(raw.get("corroborated")),
        families=tuple(raw.get("families") or ()),
        health_score=raw.get("health_score"),
        health_band=str(raw.get("health_band") or ""),
        regions_affected=int(raw.get("regions_affected") or 0),
        regions_total=int(raw.get("regions_total") or 0),
        success_rate_pct=raw.get("success_rate_pct"),
        journey_tier=raw.get("journey_tier"),
        customer_impacting=bool(raw.get("customer_impacting")),
        reasons=tuple(raw.get("reasons") or ()),
        detail=str(raw.get("detail") or ""),
        unavailable=str(raw.get("unavailable") or ""),
        evidence=tuple(
            EvidenceLink(
                kind=str(item.get("kind") or ""),
                ref=str(item.get("ref") or ""),
                detail=str(item.get("detail") or ""),
                family=item.get("family"),
                region=str(item.get("region") or ""),
                customer_facing=bool(item.get("customer_facing")),
                at=_parse_dt(item.get("at")),
            )
            for item in (raw.get("evidence") or [])
        ),
    )


def _link_json(link: ChangeWindowLink) -> dict[str, Any]:
    return {
        "change_id": link.change_id,
        "change_type": link.change_type,
        "service": link.service,
        "version": link.version,
        "started_at": link.started_at.isoformat() if link.started_at else None,
        "actor": link.actor,
    }


def _link_from(raw: Mapping[str, Any]) -> ChangeWindowLink:
    return ChangeWindowLink(
        change_id=str(raw.get("change_id") or ""),
        change_type=str(raw.get("change_type") or ""),
        service=str(raw.get("service") or ""),
        version=raw.get("version"),
        started_at=_parse_dt(raw.get("started_at")),
        actor=raw.get("actor"),
    )


def _window_values(window: ChangeWindow) -> dict[str, Any]:
    return {
        "change_type": window.change_type.value,
        "service": window.service,
        "status": window.status.value,
        "started_at": window.started_at,
        "completed_at": window.completed_at,
        "first_seen_at": window.first_seen_at,
        "last_event_at": window.last_event_at,
        "version": window.version,
        "actor": window.actor,
        "diff_ref": window.diff_ref,
        "source_system": window.source_system,
        "simulated": window.simulated,
        "announced_started": window.announced_started,
        "announced_completed": window.announced_completed,
    }


def _to_window(row: Optional[ChangeWindowRow]) -> Optional[ChangeWindow]:
    if row is None:
        return None
    return ChangeWindow(
        tenant_id=row.tenant_id,
        app_id=row.app_id,
        change_id=row.change_id,
        change_type=ChangeType(row.change_type),
        service=row.service or "",
        status=ChangeStatus(row.status),
        started_at=row.started_at,
        completed_at=row.completed_at,
        first_seen_at=row.first_seen_at,
        last_event_at=row.last_event_at,
        version=row.version,
        actor=row.actor,
        diff_ref=row.diff_ref,
        source_system=row.source_system or "",
        simulated=bool(row.simulated),
        announced_started=bool(row.announced_started),
        announced_completed=bool(row.announced_completed),
    )


def _parse_dt(raw: Any):
    from datetime import datetime

    if not raw:
        return None
    if isinstance(raw, datetime):
        return raw
    try:
        return datetime.fromisoformat(str(raw))
    except ValueError:  # pragma: no cover - stored by this module, always ISO
        return None
