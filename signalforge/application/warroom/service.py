"""Opening a war room and filling it (REQ-045).

The room is opened by the act of declaring, not by a separate button. `open_for`
is called from `IncidentService.declare` after the incident commits, and it is
written so that **it can never un-declare an incident**: every failure inside it
is caught, recorded and survived, exactly as the ITSM sync is. A P0 with a room
that failed to assemble is bad; a P0 that could not be declared because the room
failed to assemble is unforgivable.

Membership is derived, in three separate steps that stay separate on purpose:

1. `BlastRadius` - what is affected, from the incident's own declaration
   snapshot and the application's topology.
2. `required_roles` - which roles that calls for. Pure, a table, arguable.
3. Here - who holds those roles, from role bindings scoped to the application.

A required role nobody holds is a `MembershipGap`. It is written onto the room,
published on the timeline and rendered on the screen, because a P0 whose comms
lead seat is empty is something to find out at minute two rather than at the
post-incident review.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Optional, Sequence

from signalforge.application.events.bus import EventBus
from signalforge.application.identity.principal import Principal
from signalforge.application.identity.uow import IdentityUnitOfWork
from signalforge.application.warroom.common import (
    MEMBER_JOINED_TOPIC,
    ROOM_GAP_TOPIC,
    ROOM_OPENED_TOPIC,
    publish_system,
    record,
)
from signalforge.contracts.canonical import TopologySnapshot
from signalforge.contracts.envelope import AdapterKind
from signalforge.contracts.roles import Role
from signalforge.domain.adapters.bindings import ConnectorBinding
from signalforge.domain.adapters.ports import AdapterRegistry
from signalforge.domain.identity.entities import RoleBinding
from signalforge.domain.identity.ports import Clock
from signalforge.domain.incidents.entities import Incident
from signalforge.domain.onboarding.profile import Severity
from signalforge.domain.warroom.blast import (
    BlastRadius,
    MembershipGap,
    RoleRequirement,
    downstream_services,
    required_roles,
)
from signalforge.domain.warroom.brief import build_brief
from signalforge.domain.warroom.entities import (
    IncidentTask,
    JoinBrief,
    MemberSource,
    RoomMember,
    WarRoom,
)
from signalforge.domain.warroom.errors import WarRoomNotFoundError
from signalforge.domain.warroom.paging import Page

logger = logging.getLogger("signalforge.warroom")

BRIDGE_CHANNEL_PREFIX = "incident"


@dataclass(frozen=True)
class WarRoomView:
    """The room and everything the S9 shell renders beside it."""

    room: WarRoom
    incident: Incident
    members: tuple[RoomMember, ...] = ()
    pages: tuple[Page, ...] = ()
    tasks: tuple[IncidentTask, ...] = ()
    requirements: tuple[RoleRequirement, ...] = ()

    @property
    def present(self) -> tuple[RoomMember, ...]:
        """Who is actually in the room. "Who is in the room" is a panel title in
        the S9 shell, and it means people who turned up - not people who were
        invited, which is a different and less useful number."""
        return tuple(member for member in self.members if member.present)


class WarRoomService:
    def __init__(
        self,
        uow_factory: Callable[[], IdentityUnitOfWork],
        bus: EventBus,
        clock: Clock,
        registry: AdapterRegistry,
        metrics=None,
        brief_writer=None,
    ):
        self._uow_factory = uow_factory
        self._bus = bus
        self._clock = clock
        self._registry = registry
        self._metrics = metrics
        # S5-T03's model-backed brief writer, injected rather than imported: the
        # war room must not depend on the incident brain, so the composition
        # root passes one in and a node without one keeps Sprint 4's behaviour
        # exactly (REQ-045, REQ-052).
        self._brief_writer = brief_writer
        self._on_opened: list = []

    @property
    def on_opened(self) -> list:
        """Hooks the composition root fills - the paging service, which must not
        be imported here. The room decides who should be in it; paging decides
        how to reach them, and keeping the arrow pointing one way is what lets a
        node run a room with paging disabled."""
        return self._on_opened

    # -- opening -------------------------------------------------------------

    async def open_for(self, incident: Incident, principal: Principal) -> Optional[WarRoom]:
        """Open the room for a freshly declared incident. Never raises.

        Idempotent on the incident: a second call returns the room that already
        exists rather than assembling a second one. That matters because declare
        is retried by clients and because the escalation sweep re-reads rooms.
        """
        try:
            return await self._open(incident, principal)
        except Exception as exc:  # pragma: no cover - defensive by design
            logger.warning(
                "the war room could not be opened; the incident stands",
                extra={
                    "incident_id": incident.id,
                    "app_id": incident.app_id,
                    "error": f"{type(exc).__name__}: {exc}",
                },
            )
            return None

    async def _open(self, incident: Incident, principal: Principal) -> WarRoom:
        async with self._uow_factory() as uow:
            existing = await uow.war_rooms.get(incident.tenant_id, incident.id)
        if existing is not None:
            return existing

        radius = await self.blast_radius(incident)
        requirements = required_roles(radius)
        bindings, users = await self._directory(incident.tenant_id)
        members, gaps = self._assemble(
            incident, radius, requirements, bindings, users, at=self._clock.now()
        )
        bridge = await self._bridge_binding(incident.tenant_id, incident.app_id)

        now = self._clock.now()
        room = WarRoom(
            id=str(uuid.uuid4()),
            tenant_id=incident.tenant_id,
            incident_id=incident.id,
            app_id=incident.app_id,
            opened_at=now,
            opened_by=incident.declared_by,
            severity=incident.severity,
            radius=radius,
            gaps=gaps,
            bridge_channel=(
                f"{BRIDGE_CHANNEL_PREFIX}-{incident.reference.lower()}" if bridge else ""
            ),
            bridge_adapter_id=bridge.adapter_id if bridge else "",
            bridge_binding_id=bridge.id if bridge else "",
        )

        async with self._uow_factory() as uow:
            await uow.war_rooms.add(room)
            for member in members:
                await uow.room_members.upsert(member)
            await publish_system(
                uow,
                self._bus,
                self._clock,
                tenant_id=room.tenant_id,
                app_id=room.app_id,
                incident_id=room.incident_id,
                topic=ROOM_OPENED_TOPIC,
                payload=_opened_payload(room, members, requirements),
            )
            for gap in gaps:
                # One event per gap rather than a list on the room event: a gap
                # is something somebody has to act on, and a timeline entry is
                # how it reaches a person who is not looking at the room.
                await publish_system(
                    uow,
                    self._bus,
                    self._clock,
                    tenant_id=room.tenant_id,
                    app_id=room.app_id,
                    incident_id=room.incident_id,
                    topic=ROOM_GAP_TOPIC,
                    payload={
                        "incident_id": room.incident_id,
                        "reference": incident.reference,
                        "role": gap.role.value,
                        "reason": gap.reason,
                        "detail": str(gap),
                    },
                )
            await uow.commit()

        self._count_opened(room, members, gaps)
        for hook in self._on_opened:
            # After the room commits, and each hook survives its own failure:
            # paging that cannot reach anybody must not remove the room that
            # records who it was trying to reach.
            try:
                await hook(room, incident, members)
            except Exception as exc:  # pragma: no cover - defensive by design
                logger.warning(
                    "a war-room hook failed; the room stands",
                    extra={
                        "incident_id": incident.id,
                        "hook": getattr(hook, "__qualname__", repr(hook)),
                        "error": f"{type(exc).__name__}: {exc}",
                    },
                )
        return room

    # -- the radius ----------------------------------------------------------

    async def blast_radius(self, incident: Incident) -> BlastRadius:
        """What this incident reaches, from the declaration and the topology.

        Read from the incident's own proposal snapshot rather than from live
        detection, deliberately. The room is assembled from what was true when
        the person declared; re-reading telemetry would assemble a different room
        each time somebody opened the screen.
        """
        proposal = incident.proposal
        snapshot = await self._topology(incident.tenant_id, incident.app_id)
        regions: tuple[str, ...] = ()
        journeys: tuple[str, ...] = ()
        customer_facing = False
        tier: Optional[int] = None
        regions_total = 0
        source = "declaration"

        if proposal is not None and not proposal.unavailable:
            source = "proposal"
            customer_facing = proposal.customer_impacting
            tier = proposal.journey_tier
            regions_total = proposal.regions_total
            regions, journeys = _scope_from_evidence(proposal)

        services, truncated = downstream_services(
            snapshot, app_id=incident.app_id, regions=regions
        )
        return BlastRadius(
            app_id=incident.app_id,
            severity=incident.severity,
            regions=regions,
            journeys=journeys,
            services=services,
            customer_facing=customer_facing,
            journey_tier=tier,
            regions_total=regions_total or len(regions),
            truncated=truncated,
            source=source,
        )

    async def _topology(
        self, tenant_id: str, app_id: str
    ) -> Optional[TopologySnapshot]:
        async with self._uow_factory() as uow:
            version = await uow.topologies.latest(tenant_id, app_id)
        if version is None:
            return None
        try:
            # The repository already hands back a parsed snapshot; re-validating
            # it guards the row a migration or a hand edit could have left behind.
            return TopologySnapshot.model_validate(version.snapshot.model_dump())
        except Exception:
            # A stored topology that will not parse is a mapped estate this
            # platform cannot read. The room opens without downstream services
            # and the brief says no topology is mapped, which is true from the
            # reader's point of view.
            logger.warning(
                "the stored topology could not be read; the room opens without it",
                extra={"app_id": app_id},
            )
            return None

    # -- membership ----------------------------------------------------------

    def _assemble(
        self,
        incident: Incident,
        radius: BlastRadius,
        requirements: Sequence[RoleRequirement],
        bindings: Sequence[RoleBinding],
        users: dict[str, str],
        *,
        at: datetime,
    ) -> tuple[tuple[RoomMember, ...], tuple[MembershipGap, ...]]:
        """Roles to people, and the roles nobody holds.

        A binding covers this room when it names this application or names none
        at all - a tenant-wide incident commander commands every application's
        incidents, which is what a binding with no `app_id` means everywhere else
        in this platform, and the war room does not get to reinterpret it.
        """
        members: dict[str, RoomMember] = {}
        gaps: list[MembershipGap] = []
        for requirement in requirements:
            holders = [
                binding.user_id
                for binding in bindings
                if binding.role is requirement.role
                and (binding.app_id in (None, "", incident.app_id))
            ]
            if not holders:
                gaps.append(
                    MembershipGap(
                        role=requirement.role,
                        reason=requirement.reason,
                        detail=(
                            f"{radius.severity.value} on {incident.app_id} calls for a "
                            f"{requirement.role.value} and nobody is bound to one"
                        ),
                    )
                )
                continue
            for user_id in sorted(set(holders)):
                if user_id in members:
                    # Somebody holding two required roles is one person in the
                    # room. The first requirement wins the reason, because the
                    # requirements are in declaration order and the earlier one
                    # is the more senior reason to be there.
                    continue
                members[user_id] = RoomMember(
                    tenant_id=incident.tenant_id,
                    incident_id=incident.id,
                    user_id=user_id,
                    role=requirement.role,
                    reason=requirement.reason,
                    source=MemberSource.BLAST_RADIUS,
                    added_at=at,
                    display_name=users.get(user_id, ""),
                    required=True,
                )
        # The person who declared it is in the room whatever their bindings say.
        # They pressed the button; a room that could exclude its own declarer
        # would be a bug with an obvious 03:00 failure mode.
        if incident.declared_by not in members:
            members[incident.declared_by] = RoomMember(
                tenant_id=incident.tenant_id,
                incident_id=incident.id,
                user_id=incident.declared_by,
                role=None,
                reason="declared this incident",
                source=MemberSource.JOINED,
                added_at=at,
                display_name=incident.declared_by_display
                or users.get(incident.declared_by, ""),
                required=True,
                present=True,
                joined_at=at,
                last_seen_at=at,
            )
        else:
            members[incident.declared_by] = members[incident.declared_by].arrived(at=at)
        return tuple(members.values()), tuple(gaps)

    async def _directory(
        self, tenant_id: str
    ) -> tuple[Sequence[RoleBinding], dict[str, str]]:
        async with self._uow_factory() as uow:
            bindings = list(await uow.bindings.list_for_tenant(tenant_id))
            users = {
                user.id: user.display_name or user.email
                for user in await uow.users.list(tenant_id)
            }
        return bindings, users

    # -- reads ---------------------------------------------------------------

    async def get(self, principal: Principal, incident_id: str) -> WarRoomView:
        tenant_id = principal.tenant_id
        async with self._uow_factory() as uow:
            incident = await uow.incidents.get(tenant_id, incident_id)
            if incident is None:
                raise WarRoomNotFoundError(f"incident {incident_id} not found")
            room = await uow.war_rooms.get(tenant_id, incident_id)
            if room is None:
                raise WarRoomNotFoundError(
                    f"incident {incident.reference} has no war room; it was "
                    "declared before the room could be assembled"
                )
            members = tuple(await uow.room_members.list(tenant_id, incident_id))
            pages = tuple(await uow.incident_pages.list(tenant_id, incident_id))
            tasks = tuple(await uow.incident_tasks.list(tenant_id, incident_id))
        return WarRoomView(
            room=room,
            incident=incident,
            members=members,
            pages=pages,
            tasks=tasks,
            requirements=required_roles(room.radius),
        )

    async def brief(
        self, principal: Principal, incident_id: str, *, for_self: bool = True
    ) -> JoinBrief:
        """The join brief, addressed to the caller when they are in the room.

        The deterministic brief is built first and always: it is complete on its
        own, and it is what the reader gets when there is no model, when the
        budget is spent and when a model answers off-schema. When a writer is
        wired (S5-T03) it rewrites *that material*, grounded in the incident
        graph, keeping every caveat - and stamps `source` so the reader can tell
        which kind they have.
        """
        view = await self.get(principal, incident_id)
        mine = next(
            (
                member
                for member in view.members
                if member.user_id == principal.user_id
            ),
            None,
        )
        async with self._uow_factory() as uow:
            transitions = tuple(
                await uow.incident_transitions.list(principal.tenant_id, incident_id)
            )
            nodes = (
                tuple(
                    await uow.incident_graph.list(
                        principal.tenant_id, incident_id, limit=200
                    )
                )
                if self._brief_writer is not None
                else ()
            )
        reason = mine.reason if mine and for_self else ""
        template = build_brief(
            view.incident,
            radius=view.room.radius,
            now=self._clock.now(),
            members=view.members,
            transitions=transitions,
            tasks=view.tasks,
            for_user=principal.user_id if for_self else None,
            for_role_reason=reason,
        )
        if self._brief_writer is None:
            return template
        try:
            return await self._brief_writer.write(
                template,
                tenant_id=principal.tenant_id,
                nodes=nodes,
                for_role_reason=reason,
            )
        except Exception as exc:  # pragma: no cover - defensive by design
            # A brief is the first thing a paged responder reads at 03:00. It is
            # never allowed to fail because a writer did.
            logger.warning(
                "the join-brief writer failed; serving the deterministic brief",
                extra={
                    "incident_id": incident_id,
                    "error": f"{type(exc).__name__}: {exc}",
                },
            )
            return template

    # -- joining -------------------------------------------------------------

    async def join(
        self, principal: Principal, incident_id: str, *, note: str = ""
    ) -> WarRoomView:
        """A person turns up.

        Carries no permission and needs none: you join yourself, into a room in
        your own tenant, and the session is the only thing that says who you are.
        A permission here would exclude the SME - the frozen matrix gives that
        role nothing - which is exactly the person a war room exists to reach.
        """
        tenant_id = principal.tenant_id
        now = self._clock.now()
        async with self._uow_factory() as uow:
            incident = await uow.incidents.get(tenant_id, incident_id)
            if incident is None:
                raise WarRoomNotFoundError(f"incident {incident_id} not found")
            room = await uow.war_rooms.get(tenant_id, incident_id)
            if room is None:
                raise WarRoomNotFoundError(
                    f"incident {incident.reference} has no war room"
                )
            existing = await uow.room_members.get(
                tenant_id, incident_id, principal.user_id
            )
            member = (
                existing.arrived(at=now)
                if existing is not None
                else RoomMember(
                    tenant_id=tenant_id,
                    incident_id=incident_id,
                    user_id=principal.user_id,
                    role=None,
                    reason=note or "joined the room",
                    source=MemberSource.JOINED,
                    added_at=now,
                    display_name=principal.display_name or "",
                    present=True,
                    joined_at=now,
                    last_seen_at=now,
                )
            )
            await uow.room_members.upsert(member)
            if existing is None or not existing.present:
                # Only the first arrival is an event. A screen polling every ten
                # seconds must not fill the timeline people are reading.
                await record(
                    uow,
                    self._bus,
                    self._clock,
                    principal=principal,
                    app_id=incident.app_id,
                    incident_id=incident_id,
                    topic=MEMBER_JOINED_TOPIC,
                    payload={
                        "incident_id": incident_id,
                        "reference": incident.reference,
                        "user_id": principal.user_id,
                        "display_name": principal.display_name or "",
                        "role": member.role.value if member.role else None,
                        "reason": member.reason,
                        "was_required": member.required,
                        "was_paged": member.paged,
                    },
                )
            await uow.commit()
        return await self.get(principal, incident_id)

    async def mark_present(
        self, tenant_id: str, incident_id: str, user_id: str, *, display_name: str = ""
    ) -> None:
        """Somebody was seen - they spoke on the bridge, or acknowledged a page.

        No event and no audit entry: presence inferred from another act is a
        detail of that act, and the act itself is already on the timeline.
        """
        now = self._clock.now()
        async with self._uow_factory() as uow:
            existing = await uow.room_members.get(tenant_id, incident_id, user_id)
            member = (
                existing.arrived(at=now)
                if existing is not None
                else RoomMember(
                    tenant_id=tenant_id,
                    incident_id=incident_id,
                    user_id=user_id,
                    role=None,
                    reason="took part",
                    source=MemberSource.JOINED,
                    added_at=now,
                    display_name=display_name,
                    present=True,
                    joined_at=now,
                    last_seen_at=now,
                )
            )
            await uow.room_members.upsert(member)
            await uow.commit()

    async def add_member(
        self,
        tenant_id: str,
        incident_id: str,
        user_id: str,
        *,
        role: Optional[Role],
        reason: str,
        source: MemberSource,
        display_name: str = "",
        paged: bool = False,
    ) -> None:
        """Put somebody in the room without them acting - an escalation target.

        Upsert rather than insert, and the existing row's presence is preserved:
        escalating to somebody who is already in the room must not make them look
        as though they left.
        """
        now = self._clock.now()
        async with self._uow_factory() as uow:
            existing = await uow.room_members.get(tenant_id, incident_id, user_id)
            if existing is not None:
                member = existing.was_paged() if paged else existing
            else:
                member = RoomMember(
                    tenant_id=tenant_id,
                    incident_id=incident_id,
                    user_id=user_id,
                    role=role,
                    reason=reason,
                    source=source,
                    added_at=now,
                    display_name=display_name,
                    paged=paged,
                )
            await uow.room_members.upsert(member)
            await uow.commit()

    # -- closing -------------------------------------------------------------

    async def close_for(self, incident: Incident) -> None:
        """The incident closed. The room closes with it and stays readable."""
        async with self._uow_factory() as uow:
            room = await uow.war_rooms.get(incident.tenant_id, incident.id)
            if room is None or not room.open:
                return
            await uow.war_rooms.update(room.closed(at=self._clock.now()))
            await uow.commit()

    # -- wiring --------------------------------------------------------------

    async def _bridge_binding(
        self, tenant_id: str, app_id: str
    ) -> Optional[ConnectorBinding]:
        async with self._uow_factory() as uow:
            bindings = await uow.connectors.list(
                tenant_id, app_id=app_id, enabled_only=True
            )
        for binding in bindings:
            manifest = self._registry.find(binding.adapter_id)
            if (
                manifest is not None
                and manifest.kind is AdapterKind.COLLABORATION
                and manifest.supports("post")
            ):
                return binding
        return None

    def _count_opened(
        self,
        room: WarRoom,
        members: Sequence[RoomMember],
        gaps: Sequence[MembershipGap],
    ) -> None:
        if self._metrics is None:
            return
        self._metrics.increment(
            "signalforge_war_rooms_total", {"severity": room.severity.value}
        )
        for gap in gaps:
            self._metrics.increment(
                "signalforge_war_room_membership_gaps_total", {"role": gap.role.value}
            )


# --- pure helpers -------------------------------------------------------------


def _scope_from_evidence(proposal) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Regions and journeys the declaration's evidence actually named.

    Read off the evidence refs rather than off a summary count, because
    `regions_affected: 2` says how many and the room needs to know *which*.
    """
    regions = sorted(
        {ref.region for ref in proposal.evidence if ref.region}
    )
    journeys = sorted(
        {
            ref.ref.split(":", 1)[1]
            for ref in proposal.evidence
            if ref.kind == "journey" and ":" in ref.ref
        }
    )
    return tuple(regions), tuple(journeys)


def _opened_payload(
    room: WarRoom,
    members: Sequence[RoomMember],
    requirements: Sequence[RoleRequirement],
) -> dict:
    return {
        "incident_id": room.incident_id,
        "war_room_id": room.id,
        "app_id": room.app_id,
        "severity": room.severity.value,
        "blast_radius": {
            "summary": room.radius.summary(),
            "regions": list(room.radius.regions),
            "journeys": list(room.radius.journeys),
            "services": list(room.radius.services),
            "customer_facing": room.radius.customer_facing,
            "source": room.radius.source,
        },
        "required_roles": [
            {
                "role": requirement.role.value,
                "paged": requirement.paged,
                "reason": requirement.reason,
            }
            for requirement in requirements
        ],
        "members": [
            {
                "user_id": member.user_id,
                "role": member.role.value if member.role else None,
                "reason": member.reason,
            }
            for member in members
        ],
        "gaps": [gap.role.value for gap in room.gaps],
        "bridge_channel": room.bridge_channel or None,
    }


def _severity_of(incident: Incident) -> Severity:
    return incident.severity
