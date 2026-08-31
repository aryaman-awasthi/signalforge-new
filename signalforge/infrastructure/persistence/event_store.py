"""SQL implementations of the event-backbone ports.

Tenant isolation (REQ-013) lives here, as it does for identity: every read that
serves a request carries a tenant_id predicate inside the repository. The single
exception is read_for_dispatch, the platform worker path, which is documented on
the port and covered by a security test asserting the HTTP surface stays scoped.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional, Sequence

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from signalforge.contracts.envelope import (
    EventEnvelope,
    Plane,
    canonical_payload_hash,
)
from signalforge.domain.events.chain import ChainLink
from signalforge.domain.events.entities import (
    DeadLetter,
    DeadLetterState,
    EventSeal,
    SealScope,
)
from signalforge.domain.events.lanes import lane_for
from signalforge.domain.events.ports import (
    ConsumerOffsetStore,
    DeadLetterStore,
    EventStore,
    ProcessedLedger,
    SealStore,
)
from signalforge.domain.identity.ports import Clock
from signalforge.infrastructure.persistence.models import (
    ConsumerOffsetRow,
    ConsumerProcessedRow,
    DeadLetterRow,
    EventRow,
    EventSealRow,
)

MAX_CAUSAL_CHAIN_DEPTH = 500


class SqlEventStore(EventStore):
    def __init__(self, session: AsyncSession, clock: Clock):
        self._s = session
        self._clock = clock

    async def append(self, envelope: EventEnvelope) -> EventEnvelope:
        existing = (
            await self._s.execute(
                select(EventRow).where(EventRow.event_id == str(envelope.event_id))
            )
        ).scalars().first()
        if existing is not None:
            # A producer retry, not a new fact. History stays as it was recorded.
            return self._to_envelope(existing)

        envelope.recorded_at = envelope.recorded_at or self._clock.now()
        envelope.payload_hash = canonical_payload_hash(envelope.payload)
        envelope.prev_hash = await self._chain_tip(envelope.tenant_id, envelope.plane)

        row = EventRow(
            event_id=str(envelope.event_id),
            tenant_id=envelope.tenant_id,
            topic=envelope.topic,
            app_id=envelope.app_id,
            incident_id=envelope.incident_id,
            plane=envelope.plane.value,
            partition_key=envelope.partition_key,
            lane=lane_for(envelope.partition_key),
            occurred_at=envelope.occurred_at,
            recorded_at=envelope.recorded_at,
            actor_type=envelope.actor.type.value,
            actor_id=envelope.actor.id,
            correlation_id=envelope.correlation_id,
            causation_id=str(envelope.causation_id) if envelope.causation_id else None,
            idempotency_key=envelope.idempotency_key,
            envelope="{}",
            payload_hash=envelope.payload_hash,
            prev_hash=envelope.prev_hash,
        )
        self._s.add(row)
        await self._s.flush()  # assigns seq, the store-assigned recording order
        envelope.seq = row.seq
        row.envelope = envelope.model_dump_json()
        await self._s.flush()
        return envelope

    async def get(self, tenant_id: str, event_id: str) -> Optional[EventEnvelope]:
        row = (
            await self._s.execute(
                select(EventRow).where(
                    EventRow.tenant_id == tenant_id, EventRow.event_id == event_id
                )
            )
        ).scalars().first()
        return self._to_envelope(row) if row is not None else None

    async def read(
        self,
        *,
        tenant_id: str,
        after_seq: int = 0,
        until_seq: Optional[int] = None,
        topics: Optional[Sequence[str]] = None,
        incident_id: Optional[str] = None,
        plane: Optional[Plane] = None,
        limit: int = 200,
    ) -> Sequence[EventEnvelope]:
        stmt = select(EventRow).where(
            EventRow.tenant_id == tenant_id, EventRow.seq > after_seq
        )
        if until_seq is not None:
            stmt = stmt.where(EventRow.seq <= until_seq)
        if topics:
            stmt = stmt.where(self._topic_predicate(topics))
        if incident_id is not None:
            stmt = stmt.where(EventRow.incident_id == incident_id)
        if plane is not None:
            stmt = stmt.where(EventRow.plane == plane.value)
        stmt = stmt.order_by(EventRow.seq).limit(limit)
        rows = (await self._s.execute(stmt)).scalars().all()
        return [self._to_envelope(r) for r in rows]

    async def read_for_dispatch(
        self, *, after_seq: int, limit: int
    ) -> Sequence[EventEnvelope]:
        rows = (
            await self._s.execute(
                select(EventRow)
                .where(EventRow.seq > after_seq)
                .order_by(EventRow.seq)
                .limit(limit)
            )
        ).scalars().all()
        return [self._to_envelope(r) for r in rows]

    async def causal_chain(
        self, *, tenant_id: str, event_id: str
    ) -> Sequence[EventEnvelope]:
        root_id = await self._root_of(tenant_id, event_id)
        collected: dict[str, EventRow] = {}
        frontier = [root_id]
        while frontier and len(collected) < MAX_CAUSAL_CHAIN_DEPTH:
            rows = (
                await self._s.execute(
                    select(EventRow).where(
                        EventRow.tenant_id == tenant_id,
                        EventRow.event_id.in_(frontier),
                    )
                )
            ).scalars().all()
            for row in rows:
                collected[row.event_id] = row
            children = (
                await self._s.execute(
                    select(EventRow).where(
                        EventRow.tenant_id == tenant_id,
                        EventRow.causation_id.in_(frontier),
                    )
                )
            ).scalars().all()
            frontier = [c.event_id for c in children if c.event_id not in collected]
            for child in children:
                collected[child.event_id] = child
        ordered = sorted(collected.values(), key=lambda r: r.seq)
        return [self._to_envelope(r) for r in ordered]

    async def chain_links(
        self,
        *,
        tenant_id: str,
        plane: Plane,
        from_seq: int = 0,
        to_seq: Optional[int] = None,
        incident_id: Optional[str] = None,
    ) -> Sequence[ChainLink]:
        stmt = select(
            EventRow.seq, EventRow.event_id, EventRow.payload_hash, EventRow.prev_hash
        ).where(
            EventRow.tenant_id == tenant_id,
            EventRow.plane == plane.value,
            EventRow.seq > from_seq,
        )
        if to_seq is not None:
            stmt = stmt.where(EventRow.seq <= to_seq)
        if incident_id is not None:
            stmt = stmt.where(EventRow.incident_id == incident_id)
        rows = (await self._s.execute(stmt.order_by(EventRow.seq))).all()
        return [ChainLink(seq=r[0], event_id=r[1], payload_hash=r[2], prev_hash=r[3]) for r in rows]

    async def max_seq(self) -> int:
        return (await self._s.execute(select(func.max(EventRow.seq)))).scalar() or 0

    # -- helpers -----------------------------------------------------------

    async def _chain_tip(self, tenant_id: str, plane: Plane) -> Optional[str]:
        """Chains are per (tenant, plane): retention prunes the signal plane, and
        a single chain across both would be broken by the first prune."""
        return (
            await self._s.execute(
                select(EventRow.payload_hash)
                .where(EventRow.tenant_id == tenant_id, EventRow.plane == plane.value)
                .order_by(EventRow.seq.desc())
                .limit(1)
            )
        ).scalars().first()

    async def _root_of(self, tenant_id: str, event_id: str) -> str:
        current = event_id
        for _ in range(MAX_CAUSAL_CHAIN_DEPTH):
            parent = (
                await self._s.execute(
                    select(EventRow.causation_id).where(
                        EventRow.tenant_id == tenant_id, EventRow.event_id == current
                    )
                )
            ).scalars().first()
            if not parent:
                return current
            current = parent
        return current

    @staticmethod
    def _topic_predicate(topics: Sequence[str]):
        clauses = []
        for topic in topics:
            if topic.endswith(".*"):
                clauses.append(EventRow.topic.like(f"{topic[:-1]}%"))
            else:
                clauses.append(EventRow.topic == topic)
        from sqlalchemy import or_

        return or_(*clauses)

    @staticmethod
    def _to_envelope(row: EventRow) -> EventEnvelope:
        return EventEnvelope.model_validate_json(row.envelope)


class SqlConsumerOffsetStore(ConsumerOffsetStore):
    def __init__(self, session: AsyncSession, clock: Clock):
        self._s = session
        self._clock = clock

    async def get_all(self, group: str) -> dict[int, int]:
        rows = (
            await self._s.execute(
                select(ConsumerOffsetRow.lane, ConsumerOffsetRow.last_seq).where(
                    ConsumerOffsetRow.group_name == group
                )
            )
        ).all()
        return {lane: last_seq for lane, last_seq in rows}

    async def set(self, group: str, lane: int, seq: int) -> None:
        row = await self._s.get(ConsumerOffsetRow, (group, lane))
        if row is None:
            self._s.add(
                ConsumerOffsetRow(
                    group_name=group, lane=lane, last_seq=seq, updated_at=self._clock.now()
                )
            )
        else:
            row.last_seq = seq
            row.updated_at = self._clock.now()
        await self._s.flush()

    async def list_all(self) -> Sequence[tuple[str, int, int]]:
        rows = (
            await self._s.execute(
                select(
                    ConsumerOffsetRow.group_name,
                    ConsumerOffsetRow.lane,
                    ConsumerOffsetRow.last_seq,
                ).order_by(ConsumerOffsetRow.group_name, ConsumerOffsetRow.lane)
            )
        ).all()
        return [(g, lane, seq) for g, lane, seq in rows]


class SqlProcessedLedger(ProcessedLedger):
    def __init__(self, session: AsyncSession):
        self._s = session

    async def seen(self, group: str, event_id: str) -> bool:
        return await self._s.get(ConsumerProcessedRow, (group, event_id)) is not None

    async def mark(self, group: str, event_id: str, at: datetime) -> None:
        self._s.add(
            ConsumerProcessedRow(group_name=group, event_id=event_id, processed_at=at)
        )
        await self._s.flush()


class SqlDeadLetterStore(DeadLetterStore):
    def __init__(self, session: AsyncSession):
        self._s = session

    async def add(self, dead_letter: DeadLetter) -> DeadLetter:
        self._s.add(
            DeadLetterRow(
                id=dead_letter.id,
                group_name=dead_letter.group,
                event_seq=dead_letter.event_seq,
                event_id=dead_letter.event_id,
                tenant_id=dead_letter.tenant_id,
                topic=dead_letter.topic,
                partition_key=dead_letter.partition_key,
                lane=dead_letter.lane,
                attempts=dead_letter.attempts,
                error=dead_letter.error,
                state=dead_letter.state.value,
                first_failed_at=dead_letter.first_failed_at,
                last_failed_at=dead_letter.last_failed_at,
            )
        )
        await self._s.flush()
        return dead_letter

    async def get(
        self, dead_letter_id: str, tenant_id: Optional[str] = None
    ) -> Optional[DeadLetter]:
        stmt = select(DeadLetterRow).where(DeadLetterRow.id == dead_letter_id)
        if tenant_id is not None:
            stmt = stmt.where(DeadLetterRow.tenant_id == tenant_id)
        row = (await self._s.execute(stmt)).scalars().first()
        return self._to_entity(row) if row is not None else None

    async def list(
        self,
        *,
        tenant_id: Optional[str] = None,
        group: Optional[str] = None,
        state: Optional[DeadLetterState] = None,
        limit: int = 100,
    ) -> Sequence[DeadLetter]:
        stmt = select(DeadLetterRow)
        if tenant_id is not None:
            stmt = stmt.where(DeadLetterRow.tenant_id == tenant_id)
        if group is not None:
            stmt = stmt.where(DeadLetterRow.group_name == group)
        if state is not None:
            stmt = stmt.where(DeadLetterRow.state == state.value)
        rows = (
            await self._s.execute(stmt.order_by(DeadLetterRow.last_failed_at.desc()).limit(limit))
        ).scalars().all()
        return [self._to_entity(r) for r in rows]

    async def update(self, dead_letter: DeadLetter) -> None:
        await self._s.execute(
            update(DeadLetterRow)
            .where(DeadLetterRow.id == dead_letter.id)
            .values(
                state=dead_letter.state.value,
                attempts=dead_letter.attempts,
                error=dead_letter.error,
                last_failed_at=dead_letter.last_failed_at,
                requeued_at=dead_letter.requeued_at,
                requeued_by=dead_letter.requeued_by,
                resolved_at=dead_letter.resolved_at,
            )
        )
        await self._s.flush()

    async def take_requeued(
        self, groups: Sequence[str], limit: int
    ) -> Sequence[DeadLetter]:
        if not groups:
            return []
        rows = (
            await self._s.execute(
                select(DeadLetterRow)
                .where(
                    DeadLetterRow.group_name.in_(list(groups)),
                    DeadLetterRow.state == DeadLetterState.REQUEUED.value,
                )
                .order_by(DeadLetterRow.event_seq)
                .limit(limit)
            )
        ).scalars().all()
        return [self._to_entity(r) for r in rows]

    async def count_by_state(self, tenant_id: Optional[str] = None) -> dict[str, int]:
        stmt = select(DeadLetterRow.state, func.count()).group_by(DeadLetterRow.state)
        if tenant_id is not None:
            stmt = stmt.where(DeadLetterRow.tenant_id == tenant_id)
        rows = (await self._s.execute(stmt)).all()
        counts = {state.value: 0 for state in DeadLetterState}
        counts.update({state: count for state, count in rows})
        return counts

    @staticmethod
    def _to_entity(row: DeadLetterRow) -> DeadLetter:
        return DeadLetter(
            id=row.id,
            group=row.group_name,
            event_seq=row.event_seq,
            event_id=row.event_id,
            tenant_id=row.tenant_id,
            topic=row.topic,
            partition_key=row.partition_key,
            lane=row.lane,
            attempts=row.attempts,
            error=row.error,
            state=DeadLetterState(row.state),
            first_failed_at=row.first_failed_at,
            last_failed_at=row.last_failed_at,
            requeued_at=row.requeued_at,
            requeued_by=row.requeued_by,
            resolved_at=row.resolved_at,
        )


class SqlSealStore(SealStore):
    def __init__(self, session: AsyncSession):
        self._s = session

    async def add(self, seal: EventSeal) -> EventSeal:
        self._s.add(
            EventSealRow(
                id=seal.id,
                tenant_id=seal.tenant_id,
                scope=seal.scope.value,
                plane=seal.plane.value,
                incident_id=seal.incident_id,
                from_seq=seal.from_seq,
                to_seq=seal.to_seq,
                event_count=seal.event_count,
                seal_hash=seal.seal_hash,
                prev_seal_hash=seal.prev_seal_hash,
                sealed_at=seal.sealed_at,
                sealed_by=seal.sealed_by,
            )
        )
        await self._s.flush()
        return seal

    async def get(self, tenant_id: str, seal_id: str) -> Optional[EventSeal]:
        row = (
            await self._s.execute(
                select(EventSealRow).where(
                    EventSealRow.tenant_id == tenant_id, EventSealRow.id == seal_id
                )
            )
        ).scalars().first()
        return self._to_entity(row) if row is not None else None

    async def list(
        self, *, tenant_id: str, incident_id: Optional[str] = None
    ) -> Sequence[EventSeal]:
        stmt = select(EventSealRow).where(EventSealRow.tenant_id == tenant_id)
        if incident_id is not None:
            stmt = stmt.where(EventSealRow.incident_id == incident_id)
        rows = (await self._s.execute(stmt.order_by(EventSealRow.to_seq))).scalars().all()
        return [self._to_entity(r) for r in rows]

    async def latest(self, tenant_id: str) -> Optional[EventSeal]:
        row = (
            await self._s.execute(
                select(EventSealRow)
                .where(EventSealRow.tenant_id == tenant_id)
                .order_by(EventSealRow.sealed_at.desc(), EventSealRow.to_seq.desc())
                .limit(1)
            )
        ).scalars().first()
        return self._to_entity(row) if row is not None else None

    async def latest_for_scope(
        self, tenant_id: str, incident_id: Optional[str]
    ) -> Optional[EventSeal]:
        stmt = select(EventSealRow).where(EventSealRow.tenant_id == tenant_id)
        stmt = stmt.where(
            EventSealRow.incident_id == incident_id
            if incident_id is not None
            else EventSealRow.incident_id.is_(None)
        )
        row = (
            await self._s.execute(stmt.order_by(EventSealRow.to_seq.desc()).limit(1))
        ).scalars().first()
        return self._to_entity(row) if row is not None else None

    @staticmethod
    def _to_entity(row: EventSealRow) -> EventSeal:
        return EventSeal(
            id=row.id,
            tenant_id=row.tenant_id,
            scope=SealScope(row.scope),
            plane=Plane(row.plane),
            incident_id=row.incident_id,
            from_seq=row.from_seq,
            to_seq=row.to_seq,
            event_count=row.event_count,
            seal_hash=row.seal_hash,
            prev_seal_hash=row.prev_seal_hash,
            sealed_at=row.sealed_at,
            sealed_by=row.sealed_by,
        )
