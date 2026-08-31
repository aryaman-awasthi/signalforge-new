# Sprint 2 log (2026-09-01, one day)
Goal: durable, ordered, replayable event spine everything else rides on.

## Built
- [S2-T01][REQ-020,021,022,023] Event backbone. Domain in signalforge/domain/events/:
  stable blake2b lane assignment (LANE_COUNT 16, pure function of the partition
  key so two processes and two restarts agree), the (tenant, plane) hash chain
  with prune anchors and a seal hash binding seq+event_id+payload_hash+prev_hash,
  a bounded RetryPolicy (cap 10, config may only lower it) with exponential
  backoff, topic patterns, DeadLetter/EventSeal entities and the ports.
  Application in signalforge/application/events/: EventBus.publish takes the
  caller's unit of work so an event and the state that caused it are one commit;
  Dispatcher with per-(group, lane) offsets, an idempotency ledger, retries then
  dead letters, requeue and catch-up; ReplayService (bounds, filters, paging,
  causal chain) and SealService (seals chain onto each other, verify recomputes).
  Infrastructure: events / consumer_offsets / consumer_processed / dead_letters /
  event_seals tables + Alembic 51c6a4991192, SqlEventStore and friends,
  SqlEventUnitOfWork, and .events added to the identity unit of work so the
  outbox is genuinely transactional. API: GET /events (after_seq, until, topics,
  incident_id, plane, limit), GET /events/{id}, GET /events/{id}/chain,
  POST /events/seals, GET /events/seals, POST /events/seals/{id}/verify - replay
  on read_audit, sealing on close_incident so the frozen matrix keeps the
  Auditor read-only. Ordering and idempotency written failing-test-first.
  Verified: 41 REQ-tagged tests including a real subprocess hard-killed
  mid-consumption (nothing lost, nothing applied twice, order preserved per
  partition); live smoke on :8011.

## Not built / deferred

## Deviations

## Tests
- added: 41 (S2-T01)
- suite result: 165 passed, 0 failed

## Artifacts updated
- 14 functional tests (signalforge/tests/events/)

## Security notes

## Known issues and technical debt

## Demoable checkpoint

## Coverage checklist ticks

## Entry criteria for next sprint:
