"""The consume side of the bus (REQ-021, REQ-022).

Guarantees, and how each one is obtained:

* **Ordered per partition.** Every event is assigned a lane from its partition
  key (incident, else app, else tenant). Each (group, lane) pair has its own
  stored offset, and a lane's events are delivered in strict seq order. Two
  incidents never wait for one another.
* **At least once.** The handler runs, the processed-ledger row is written and
  the offset is advanced in one transaction. A crash before that commit rolls
  the whole thing back, so the event is delivered again after restart; a crash
  after it means the offset moved and there is nothing to redo.
* **Duplicates are harmless.** Before invoking a handler the dispatcher checks
  the ledger for (group, event_id). Redelivery caused by a rewound offset, a
  restore or an operator requeue therefore cannot apply a handler twice.
* **Poison never blocks.** A handler that keeps failing exhausts a bounded
  retry policy, the event lands in the dead-letter store with its error, and
  the lane advances. Requeue puts it back in front of the same group.
* **Catch-up is the normal path.** A sweep always starts from the stored
  offsets, so a dispatcher that has been down simply has more to do.

Lanes are drained sequentially in this single-machine deployment; the per-lane
offsets are the seam at which lanes become concurrent workers without any
change to the guarantees above.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from typing import Callable, Optional, Sequence

from signalforge.application.events.uow import EventUnitOfWork
from signalforge.contracts.envelope import EventEnvelope
from signalforge.domain.events.dispatch import (
    DeliveryOutcome,
    RetryPolicy,
    topic_matches,
)
from signalforge.domain.events.entities import DeadLetter, DeadLetterState
from signalforge.domain.events.lanes import LANE_COUNT, lane_for
from signalforge.domain.identity.ports import Clock

DEAD_LETTER_TOPIC = "system.bus.dead_letter"


@dataclass
class DispatchReport:
    delivered: int = 0
    skipped_duplicates: int = 0
    retried: int = 0
    dead_lettered: int = 0
    redelivered: int = 0
    redelivery_failed: int = 0


@dataclass(frozen=True)
class Subscription:
    group: str
    handler: object
    topics: Optional[tuple[str, ...]] = None


class Dispatcher:
    def __init__(
        self,
        uow_factory: Callable[[], EventUnitOfWork],
        clock: Clock,
        *,
        retry_policy: Optional[RetryPolicy] = None,
        batch_size: int = 200,
        lane_count: int = LANE_COUNT,
        metrics: Optional[object] = None,
    ):
        self._uow_factory = uow_factory
        self._clock = clock
        self._retry = retry_policy or RetryPolicy()
        self._batch_size = batch_size
        self._lane_count = lane_count
        self._metrics = metrics
        self._subscriptions: list[Subscription] = []

    # -- registration ------------------------------------------------------

    def register(
        self, group: str, handler, topics: Optional[Sequence[str]] = None
    ) -> None:
        if any(s.group == group for s in self._subscriptions):
            raise ValueError(f"consumer group {group!r} is already registered")
        self._subscriptions.append(
            Subscription(group=group, handler=handler, topics=tuple(topics) if topics else None)
        )

    @property
    def groups(self) -> tuple[str, ...]:
        return tuple(s.group for s in self._subscriptions)

    # -- the sweep ---------------------------------------------------------

    async def run_once(self) -> DispatchReport:
        report = DispatchReport()
        for subscription in self._subscriptions:
            await self._drain_requeued(subscription, report)
            await self._deliver_new(subscription, report)
        return report

    async def run_forever(
        self, interval_s: float = 0.5, stop_event: Optional[asyncio.Event] = None
    ) -> None:
        while stop_event is None or not stop_event.is_set():
            await self.run_once()
            if stop_event is None:
                await asyncio.sleep(interval_s)
            else:
                try:
                    await asyncio.wait_for(stop_event.wait(), timeout=interval_s)
                except asyncio.TimeoutError:
                    pass

    async def _deliver_new(self, subscription: Subscription, report: DispatchReport) -> None:
        while True:
            async with self._uow_factory() as uow:
                offsets = await uow.offsets.get_all(subscription.group)
            floor = min(offsets.get(lane, 0) for lane in range(self._lane_count))

            async with self._uow_factory() as uow:
                batch = list(
                    await uow.events.read_for_dispatch(
                        after_seq=floor, limit=self._batch_size
                    )
                )
            if not batch:
                return

            high_water = batch[-1].seq
            for envelope in batch:
                lane = lane_for(envelope.partition_key, self._lane_count)
                if envelope.seq <= offsets.get(lane, 0):
                    continue
                await self._deliver_one(subscription, envelope, lane, report)
                offsets[lane] = envelope.seq

            # Lanes with nothing in this batch are provably caught up to the
            # high-water mark, so advancing them keeps the floor moving and the
            # next sweep from rescanning history.
            idle = [
                lane
                for lane in range(self._lane_count)
                if offsets.get(lane, 0) < high_water
            ]
            if idle:
                async with self._uow_factory() as uow:
                    for lane in idle:
                        await uow.offsets.set(subscription.group, lane, high_water)
                        offsets[lane] = high_water
                    await uow.commit()

            if len(batch) < self._batch_size:
                return

    async def _deliver_one(
        self,
        subscription: Subscription,
        envelope: EventEnvelope,
        lane: int,
        report: DispatchReport,
    ) -> None:
        if not topic_matches(envelope.topic, subscription.topics):
            await self._advance(subscription.group, lane, envelope.seq)
            return

        attempts = 0
        while True:
            try:
                async with self._uow_factory() as uow:
                    if await uow.processed.seen(subscription.group, str(envelope.event_id)):
                        await uow.offsets.set(subscription.group, lane, envelope.seq)
                        await uow.commit()
                        report.skipped_duplicates += 1
                        self._count("signalforge_events_duplicate_total", subscription.group)
                        return
                    await subscription.handler.handle(envelope, uow)
                    await uow.processed.mark(
                        subscription.group, str(envelope.event_id), self._clock.now()
                    )
                    await uow.offsets.set(subscription.group, lane, envelope.seq)
                    await uow.commit()
                report.delivered += 1
                self._count("signalforge_events_dispatched_total", subscription.group)
                return
            except Exception as exc:  # handler failure: retry, then dead letter
                attempts += 1
                self._count("signalforge_event_handler_failures_total", subscription.group)
                if self._retry.decide(attempts) is DeliveryOutcome.DEAD_LETTER:
                    await self._dead_letter(subscription, envelope, lane, attempts, exc)
                    report.dead_lettered += 1
                    return
                report.retried += 1
                await asyncio.sleep(self._retry.backoff_s(attempts))

    async def _advance(self, group: str, lane: int, seq: int) -> None:
        async with self._uow_factory() as uow:
            await uow.offsets.set(group, lane, seq)
            await uow.commit()

    async def _dead_letter(
        self,
        subscription: Subscription,
        envelope: EventEnvelope,
        lane: int,
        attempts: int,
        exc: BaseException,
    ) -> None:
        now = self._clock.now()
        async with self._uow_factory() as uow:
            await uow.dead_letters.add(
                DeadLetter(
                    id=str(uuid.uuid4()),
                    group=subscription.group,
                    event_seq=envelope.seq or 0,
                    event_id=str(envelope.event_id),
                    tenant_id=envelope.tenant_id,
                    topic=envelope.topic,
                    partition_key=envelope.partition_key,
                    lane=lane,
                    attempts=attempts,
                    error=f"{type(exc).__name__}: {exc}"[:2000],
                    state=DeadLetterState.DEAD,
                    first_failed_at=now,
                    last_failed_at=now,
                )
            )
            await uow.audit.append(
                tenant_id=envelope.tenant_id,
                topic=DEAD_LETTER_TOPIC,
                actor_type="system",
                actor_id="dispatcher",
                payload={
                    "group": subscription.group,
                    "event_id": str(envelope.event_id),
                    "event_seq": envelope.seq,
                    "event_topic": envelope.topic,
                    "attempts": attempts,
                    "error": f"{type(exc).__name__}: {exc}"[:500],
                },
                app_id=envelope.app_id,
            )
            await uow.offsets.set(subscription.group, lane, envelope.seq)
            await uow.commit()
        self._count("signalforge_dead_letters_total", subscription.group)

    # -- redelivery --------------------------------------------------------

    async def _drain_requeued(
        self, subscription: Subscription, report: DispatchReport
    ) -> None:
        async with self._uow_factory() as uow:
            pending = list(
                await uow.dead_letters.take_requeued([subscription.group], self._batch_size)
            )
        for dead_letter in pending:
            async with self._uow_factory() as uow:
                envelope = await uow.events.get(dead_letter.tenant_id, dead_letter.event_id)
            if envelope is None:
                await self._fail_redelivery(
                    dead_letter, "event no longer present in the store"
                )
                report.redelivery_failed += 1
                continue
            try:
                async with self._uow_factory() as uow:
                    already = await uow.processed.seen(
                        subscription.group, dead_letter.event_id
                    )
                    if not already:
                        await subscription.handler.handle(envelope, uow)
                        await uow.processed.mark(
                            subscription.group, dead_letter.event_id, self._clock.now()
                        )
                    await uow.dead_letters.update(
                        dead_letter.resolved(at=self._clock.now())
                    )
                    await uow.commit()
                report.redelivered += 1
                self._count("signalforge_dead_letters_requeued_total", subscription.group)
            except Exception as exc:
                await self._fail_redelivery(dead_letter, f"{type(exc).__name__}: {exc}")
                report.redelivery_failed += 1

    async def _fail_redelivery(self, dead_letter: DeadLetter, error: str) -> None:
        async with self._uow_factory() as uow:
            await uow.dead_letters.update(
                dead_letter.failed_again(error=error[:2000], at=self._clock.now())
            )
            await uow.commit()

    # -- metrics -----------------------------------------------------------

    def _count(self, name: str, group: str) -> None:
        if self._metrics is not None:
            self._metrics.increment(name, {"group": group})
