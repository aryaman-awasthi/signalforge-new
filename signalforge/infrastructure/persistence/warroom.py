"""War-room repositories (REQ-045, REQ-013).

Tenant isolation lives here, not in the handlers: every read and write carries
the tenant predicate inside the class, and a cross-tenant get returns None so the
service above answers the same 404 it would for an id that exists nowhere.

Two methods deliberately span tenants, both of them work lists for the one job
that serves the install - `overdue_across_tenants` (the escalation sweep) and
`needing_harvest` (the commitment harvest). Every row either returns carries its
own `tenant_id`, and everything the jobs then write goes back through the
tenant-scoped methods. That is the same shape the connector health loop and the
ITSM retry already have, and it is documented at the port as well as here.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping, Optional, Sequence

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from signalforge.contracts.canonical import NotificationChannel
from signalforge.contracts.roles import Role
from signalforge.domain.onboarding.profile import Severity
from signalforge.domain.warroom.blast import BlastRadius, MembershipGap
from signalforge.domain.warroom.entities import (
    IncidentTask,
    MemberSource,
    RoomMember,
    TaskSource,
    TaskStatus,
    WarRoom,
)
from signalforge.domain.warroom.paging import Page, PageDelivery, PageState
from signalforge.domain.warroom.ports import (
    IncidentTaskRepository,
    PageRepository,
    RoomMemberRepository,
    WarRoomRepository,
)
from signalforge.infrastructure.persistence.models import (
    IncidentPageRow,
    IncidentTaskRow,
    RoomMemberRow,
    WarRoomRow,
)


class SqlWarRoomRepository(WarRoomRepository):
    def __init__(self, session: AsyncSession):
        self._s = session

    async def add(self, room: WarRoom) -> None:
        self._s.add(WarRoomRow(id=room.id, **_room_values(room)))
        await self._s.flush()

    async def get(self, tenant_id: str, incident_id: str) -> Optional[WarRoom]:
        row = (
            await self._s.execute(
                select(WarRoomRow).where(
                    WarRoomRow.tenant_id == tenant_id,
                    WarRoomRow.incident_id == incident_id,
                )
            )
        ).scalar_one_or_none()
        return _room_of(row) if row is not None else None

    async def update(self, room: WarRoom) -> None:
        row = (
            await self._s.execute(
                select(WarRoomRow).where(
                    WarRoomRow.tenant_id == room.tenant_id,
                    WarRoomRow.incident_id == room.incident_id,
                )
            )
        ).scalar_one_or_none()
        if row is None:
            return
        for key, value in _room_values(room).items():
            setattr(row, key, value)
        await self._s.flush()

    async def needing_harvest(self, limit: int = 25) -> Sequence[WarRoom]:
        rows = (
            (
                await self._s.execute(
                    select(WarRoomRow)
                    .where(
                        WarRoomRow.closed_at.is_(None),
                        WarRoomRow.bridge_binding_id != "",
                    )
                    .order_by(
                        WarRoomRow.last_harvest_at.asc().nullsfirst(),
                        WarRoomRow.opened_at.asc(),
                    )
                    .limit(limit)
                )
            )
            .scalars()
            .all()
        )
        return [_room_of(row) for row in rows]


class SqlRoomMemberRepository(RoomMemberRepository):
    def __init__(self, session: AsyncSession):
        self._s = session

    async def upsert(self, member: RoomMember) -> None:
        row = await self._row(member.tenant_id, member.incident_id, member.user_id)
        values = _member_values(member)
        if row is None:
            self._s.add(
                RoomMemberRow(
                    tenant_id=member.tenant_id,
                    incident_id=member.incident_id,
                    user_id=member.user_id,
                    **values,
                )
            )
        else:
            for key, value in values.items():
                setattr(row, key, value)
        await self._s.flush()

    async def get(
        self, tenant_id: str, incident_id: str, user_id: str
    ) -> Optional[RoomMember]:
        row = await self._row(tenant_id, incident_id, user_id)
        return _member_of(row) if row is not None else None

    async def list(self, tenant_id: str, incident_id: str) -> Sequence[RoomMember]:
        rows = (
            (
                await self._s.execute(
                    select(RoomMemberRow)
                    .where(
                        RoomMemberRow.tenant_id == tenant_id,
                        RoomMemberRow.incident_id == incident_id,
                    )
                    .order_by(RoomMemberRow.added_at.asc(), RoomMemberRow.user_id.asc())
                )
            )
            .scalars()
            .all()
        )
        return [_member_of(row) for row in rows]

    async def _row(
        self, tenant_id: str, incident_id: str, user_id: str
    ) -> Optional[RoomMemberRow]:
        return (
            await self._s.execute(
                select(RoomMemberRow).where(
                    RoomMemberRow.tenant_id == tenant_id,
                    RoomMemberRow.incident_id == incident_id,
                    RoomMemberRow.user_id == user_id,
                )
            )
        ).scalar_one_or_none()


class SqlPageRepository(PageRepository):
    def __init__(self, session: AsyncSession):
        self._s = session

    async def add(self, page: Page) -> None:
        self._s.add(IncidentPageRow(id=page.id, **_page_values(page)))
        await self._s.flush()

    async def get(self, tenant_id: str, page_id: str) -> Optional[Page]:
        row = await self._row(tenant_id, page_id)
        return _page_of(row) if row is not None else None

    async def update(self, page: Page) -> None:
        row = await self._row(page.tenant_id, page.id)
        if row is None:
            return
        for key, value in _page_values(page).items():
            setattr(row, key, value)
        await self._s.flush()

    async def list(
        self, tenant_id: str, incident_id: str, *, limit: int = 200
    ) -> Sequence[Page]:
        rows = (
            (
                await self._s.execute(
                    select(IncidentPageRow)
                    .where(
                        IncidentPageRow.tenant_id == tenant_id,
                        IncidentPageRow.incident_id == incident_id,
                    )
                    .order_by(IncidentPageRow.created_at.asc())
                    .limit(limit)
                )
            )
            .scalars()
            .all()
        )
        return [_page_of(row) for row in rows]

    async def list_for_recipient(
        self, tenant_id: str, recipient: str, *, open_only: bool = True, limit: int = 50
    ) -> Sequence[Page]:
        query = select(IncidentPageRow).where(
            IncidentPageRow.tenant_id == tenant_id,
            IncidentPageRow.recipient == recipient,
        )
        if open_only:
            query = query.where(
                IncidentPageRow.state.in_(
                    (PageState.PENDING.value, PageState.SENT.value)
                )
            )
        rows = (
            (
                await self._s.execute(
                    query.order_by(IncidentPageRow.created_at.asc()).limit(limit)
                )
            )
            .scalars()
            .all()
        )
        return [_page_of(row) for row in rows]

    async def chain(self, tenant_id: str, page_id: str) -> Sequence[Page]:
        """Every page in one escalation chain, walked from the page given.

        Walked in Python rather than by a recursive CTE: a chain is bounded at
        ten hops by the domain, so the walk is ten keyed reads at worst, and
        SQLite and Postgres then need no dialect-specific SQL between them.
        """
        row = await self._row(tenant_id, page_id)
        if row is None:
            return []
        first = row
        for _hop in range(row.hop + 1):
            if not first.escalated_from_page_id:
                break
            previous = await self._row(tenant_id, first.escalated_from_page_id)
            if previous is None:
                break
            first = previous
        chain = [first]
        cursor = first
        for _hop in range(10):
            if not cursor.escalated_to_page_id:
                break
            following = await self._row(tenant_id, cursor.escalated_to_page_id)
            if following is None:
                break
            chain.append(following)
            cursor = following
        return [_page_of(item) for item in chain]

    async def overdue_across_tenants(
        self, now: datetime, limit: int = 100
    ) -> Sequence[Page]:
        rows = (
            (
                await self._s.execute(
                    select(IncidentPageRow)
                    .where(
                        IncidentPageRow.state.in_(
                            (PageState.PENDING.value, PageState.SENT.value)
                        ),
                        IncidentPageRow.ack_deadline_at.is_not(None),
                        IncidentPageRow.ack_deadline_at <= now,
                    )
                    .order_by(IncidentPageRow.ack_deadline_at.asc())
                    .limit(limit)
                )
            )
            .scalars()
            .all()
        )
        return [_page_of(row) for row in rows]

    async def _row(self, tenant_id: str, page_id: str) -> Optional[IncidentPageRow]:
        return (
            await self._s.execute(
                select(IncidentPageRow).where(
                    IncidentPageRow.tenant_id == tenant_id,
                    IncidentPageRow.id == page_id,
                )
            )
        ).scalar_one_or_none()


class SqlIncidentTaskRepository(IncidentTaskRepository):
    def __init__(self, session: AsyncSession):
        self._s = session

    async def add(self, task: IncidentTask) -> None:
        self._s.add(IncidentTaskRow(id=task.id, **_task_values(task)))
        await self._s.flush()

    async def get(self, tenant_id: str, task_id: str) -> Optional[IncidentTask]:
        row = await self._row(tenant_id, task_id)
        return _task_of(row) if row is not None else None

    async def update(self, task: IncidentTask) -> None:
        row = await self._row(task.tenant_id, task.id)
        if row is None:
            return
        for key, value in _task_values(task).items():
            setattr(row, key, value)
        await self._s.flush()

    async def list(
        self, tenant_id: str, incident_id: str, *, limit: int = 200
    ) -> Sequence[IncidentTask]:
        rows = (
            (
                await self._s.execute(
                    select(IncidentTaskRow)
                    .where(
                        IncidentTaskRow.tenant_id == tenant_id,
                        IncidentTaskRow.incident_id == incident_id,
                    )
                    .order_by(IncidentTaskRow.created_at.asc())
                    .limit(limit)
                )
            )
            .scalars()
            .all()
        )
        return [_task_of(row) for row in rows]

    async def find_by_key(
        self, tenant_id: str, incident_id: str, key: str
    ) -> Optional[IncidentTask]:
        row = (
            await self._s.execute(
                select(IncidentTaskRow).where(
                    IncidentTaskRow.tenant_id == tenant_id,
                    IncidentTaskRow.incident_id == incident_id,
                    IncidentTaskRow.harvest_key == key,
                )
            )
        ).scalar_one_or_none()
        return _task_of(row) if row is not None else None

    async def _row(self, tenant_id: str, task_id: str) -> Optional[IncidentTaskRow]:
        return (
            await self._s.execute(
                select(IncidentTaskRow).where(
                    IncidentTaskRow.tenant_id == tenant_id,
                    IncidentTaskRow.id == task_id,
                )
            )
        ).scalar_one_or_none()


# --- row <-> entity -----------------------------------------------------------


def _room_values(room: WarRoom) -> dict[str, Any]:
    return {
        "tenant_id": room.tenant_id,
        "incident_id": room.incident_id,
        "app_id": room.app_id,
        "opened_at": room.opened_at,
        "opened_by": room.opened_by,
        "severity": room.severity.value,
        "radius": _radius_json(room.radius),
        "gaps": [
            {"role": gap.role.value, "reason": gap.reason, "detail": gap.detail}
            for gap in room.gaps
        ],
        "bridge_channel": room.bridge_channel,
        "bridge_adapter_id": room.bridge_adapter_id,
        "bridge_binding_id": room.bridge_binding_id,
        "closed_at": room.closed_at,
        "last_harvest_at": room.last_harvest_at,
    }


def _room_of(row: WarRoomRow) -> WarRoom:
    return WarRoom(
        id=row.id,
        tenant_id=row.tenant_id,
        incident_id=row.incident_id,
        app_id=row.app_id,
        opened_at=row.opened_at,
        opened_by=row.opened_by,
        severity=Severity(row.severity),
        radius=_radius_of(row.radius or {}, app_id=row.app_id, severity=row.severity),
        gaps=tuple(
            MembershipGap(
                role=Role(gap["role"]),
                reason=gap.get("reason", ""),
                detail=gap.get("detail", ""),
            )
            for gap in (row.gaps or [])
            if gap.get("role") in Role._value2member_map_
        ),
        bridge_channel=row.bridge_channel or "",
        bridge_adapter_id=row.bridge_adapter_id or "",
        bridge_binding_id=row.bridge_binding_id or "",
        closed_at=row.closed_at,
        last_harvest_at=row.last_harvest_at,
    )


def _radius_json(radius: BlastRadius) -> dict[str, Any]:
    return {
        "regions": list(radius.regions),
        "journeys": list(radius.journeys),
        "services": list(radius.services),
        "customer_facing": radius.customer_facing,
        "journey_tier": radius.journey_tier,
        "regions_total": radius.regions_total,
        "truncated": radius.truncated,
        "source": radius.source,
    }


def _radius_of(
    stored: Mapping[str, Any], *, app_id: str, severity: str
) -> BlastRadius:
    return BlastRadius(
        app_id=app_id,
        severity=Severity(severity),
        regions=tuple(stored.get("regions") or ()),
        journeys=tuple(stored.get("journeys") or ()),
        services=tuple(stored.get("services") or ()),
        customer_facing=bool(stored.get("customer_facing")),
        journey_tier=stored.get("journey_tier"),
        regions_total=int(stored.get("regions_total") or 0),
        truncated=bool(stored.get("truncated")),
        source=str(stored.get("source") or "proposal"),
    )


def _member_values(member: RoomMember) -> dict[str, Any]:
    return {
        "role": member.role.value if member.role else None,
        "reason": member.reason,
        "source": member.source.value,
        "display_name": member.display_name,
        "added_at": member.added_at,
        "required": member.required,
        "paged": member.paged,
        "present": member.present,
        "joined_at": member.joined_at,
        "last_seen_at": member.last_seen_at,
    }


def _member_of(row: RoomMemberRow) -> RoomMember:
    return RoomMember(
        tenant_id=row.tenant_id,
        incident_id=row.incident_id,
        user_id=row.user_id,
        role=Role(row.role) if row.role else None,
        reason=row.reason or "",
        source=MemberSource(row.source),
        added_at=row.added_at,
        display_name=row.display_name or "",
        required=bool(row.required),
        paged=bool(row.paged),
        present=bool(row.present),
        joined_at=row.joined_at,
        last_seen_at=row.last_seen_at,
    )


def _page_values(page: Page) -> dict[str, Any]:
    return {
        "tenant_id": page.tenant_id,
        "incident_id": page.incident_id,
        "app_id": page.app_id,
        "recipient": page.recipient,
        "recipient_display": page.recipient_display,
        "role": page.role.value if page.role else None,
        "reason": page.reason,
        "severity": page.severity.value,
        "state": page.state.value,
        "created_at": page.created_at,
        "ack_timeout_s": page.ack_timeout_s,
        "paged_at": page.paged_at,
        "ack_deadline_at": page.ack_deadline_at,
        "acked_at": page.acked_at,
        "acked_by": page.acked_by,
        "ack_note": page.ack_note,
        "escalated_at": page.escalated_at,
        "escalated_to_page_id": page.escalated_to_page_id,
        "escalated_from_page_id": page.escalated_from_page_id,
        "hop": page.hop,
        "rotation_id": page.rotation_id,
        "policy_id": page.policy_id,
        "policy_step": page.policy_step,
        "detail": page.detail,
        "deliveries": [
            {
                "notification_id": link.notification_id,
                "adapter_id": link.adapter_id,
                "channel": link.channel.value,
                "status": link.status,
                "simulated": link.simulated,
                "detail": link.detail,
            }
            for link in page.deliveries
        ],
        "requested_by": page.requested_by,
        "requested_by_type": page.requested_by_type,
    }


def _page_of(row: IncidentPageRow) -> Page:
    return Page(
        id=row.id,
        tenant_id=row.tenant_id,
        incident_id=row.incident_id,
        app_id=row.app_id,
        recipient=row.recipient,
        recipient_display=row.recipient_display or "",
        role=Role(row.role) if row.role else None,
        reason=row.reason or "",
        severity=Severity(row.severity or "P2"),
        state=PageState(row.state),
        created_at=row.created_at,
        ack_timeout_s=row.ack_timeout_s,
        paged_at=row.paged_at,
        ack_deadline_at=row.ack_deadline_at,
        acked_at=row.acked_at,
        acked_by=row.acked_by,
        ack_note=row.ack_note or "",
        escalated_at=row.escalated_at,
        escalated_to_page_id=row.escalated_to_page_id,
        escalated_from_page_id=row.escalated_from_page_id,
        hop=row.hop or 0,
        rotation_id=row.rotation_id,
        policy_id=row.policy_id,
        policy_step=row.policy_step or 0,
        detail=row.detail or "",
        deliveries=tuple(
            PageDelivery(
                notification_id=link["notification_id"],
                adapter_id=link.get("adapter_id", ""),
                channel=NotificationChannel(link["channel"]),
                status=link.get("status", "queued"),
                simulated=bool(link.get("simulated")),
                detail=link.get("detail", ""),
            )
            for link in (row.deliveries or [])
            if link.get("channel") in NotificationChannel._value2member_map_
        ),
        requested_by=row.requested_by or "",
        requested_by_type=row.requested_by_type or "system",
    )


def _task_values(task: IncidentTask) -> dict[str, Any]:
    return {
        "tenant_id": task.tenant_id,
        "incident_id": task.incident_id,
        "app_id": task.app_id,
        "title": task.title,
        "status": task.status.value,
        "source": task.source.value,
        "created_at": task.created_at,
        "created_by": task.created_by,
        "owner_user_id": task.owner_user_id,
        "owner_label": task.owner_label,
        "due_at": task.due_at,
        "due_label": task.due_label,
        "note": task.note,
        "evidence_ref": task.evidence_ref,
        "harvest_key": task.detail.get("key") if task.detail else None,
        "completed_at": task.completed_at,
        "completed_by": task.completed_by,
        "updated_at": task.updated_at,
        "detail": dict(task.detail or {}),
    }


def _task_of(row: IncidentTaskRow) -> IncidentTask:
    return IncidentTask(
        id=row.id,
        tenant_id=row.tenant_id,
        incident_id=row.incident_id,
        app_id=row.app_id,
        title=row.title,
        status=TaskStatus(row.status),
        source=TaskSource(row.source),
        created_at=row.created_at,
        created_by=row.created_by or "",
        owner_user_id=row.owner_user_id,
        owner_label=row.owner_label or "",
        due_at=row.due_at,
        due_label=row.due_label or "",
        note=row.note or "",
        evidence_ref=row.evidence_ref or "",
        completed_at=row.completed_at,
        completed_by=row.completed_by,
        updated_at=row.updated_at,
        detail=dict(row.detail or {}),
    )
