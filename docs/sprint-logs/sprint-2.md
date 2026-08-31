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

- [S2-T02][REQ-023,024] Two planes and the virtual clock. RetentionPolicy in
  signalforge/domain/events/retention.py: the signal plane ages out on a
  configured window (default 7 days, bounded 1..90 by the domain, so config
  tunes it but cannot remove it), the incident plane has no removal path -
  applies_to() has no branch that returns True for it and SqlEventStore.prune
  raises on any plane but signal. RetentionService is the only code that deletes
  a recorded event: it works per (tenant, plane), stops short of anything a seal
  covers, writes a RetentionWatermark holding the last pruned payload_hash so
  the surviving chain still verifies against an anchor (a prune is provable, a
  deletion is not), and publishes system.retention.pruned on the kept plane plus
  an audit entry. Clocks split three ways in infrastructure/clock.py: SystemClock
  (wall) for everything durable and security-relevant, VirtualClock (pause/step/
  set_speed, rebasing so a speed change never rewrites scenario history) for
  drill time, SwitchableClock as what the application holds and reports.
  Container now wires wall_clock into JWT expiry, rate limiting, recorded_at and
  retention, so a 100000x drill clock cannot expire a session or age out real
  telemetry - both asserted by test. PlatformWorker (signalforge/workers/
  runner.py) is the second asyncio group started by the API lifespan: one loop
  per job, failures contained and reported per job, retention scheduled on it.
  Tables: retention_watermarks + Alembic 171fdf9db7db.
  Verified: 15 REQ-tagged tests (retention policy and sweep written
  failing-test-first).

- [S2-T03][REQ-025] Platform self-observability. Structured JSON logging
  (infrastructure/observability/logging.py): one line per event with a request
  id carried in a context var so a request can be followed through the API, the
  worker and the bus; known-sensitive keys redacted by the formatter rather than
  by callers; unserialisable values stringified so a log line is never lost;
  uvicorn/sqlalchemy/alembic loggers adopted and the duplicate access log
  silenced, so the process emits exactly one format. Hand-rolled Prometheus
  registry (metrics.py): counters, gauges and histograms where every metric
  declares its label names and an undeclared or identifying label is a hard
  error - which is what makes the unauthenticated /metrics scrape safe, asserted
  by both an observability test and a security test. /metrics returns text
  exposition at the frozen observe content type. /health became a real readiness
  check over the HealthSource port (database, event bus lag/DLQ, worker group),
  503 when a component is down, still carrying the S1 fields and still naming no
  tenant; a node with the worker disabled does not claim one. ConnectorHealthService
  polls every source, publishes system.connector.health per source under the
  reserved _platform tenant on the signal plane, audits transitions only, and
  reports a source whose check raises as DOWN instead of dying. Worker now runs
  three jobs: dispatcher (0.5s), connector-health (30s), retention (1h), with the
  BusMetricsConsumer as the first real consumer group so the backbone is exercised
  in the running product. Operator surface: GET /platform/status (manage_apps),
  GET /platform/dead-letters + POST .../requeue (manage_apps, tenant-scoped),
  GET /platform/agent-runs[/{id}] (read_audit). agent_runs / agent_run_steps
  tables + store + Alembic c7ed709bb625, tenant-scoped and exercised by tests,
  empty until S5. Verified: 21 REQ-tagged tests; live boot on :8013 showing
  connector-health events published, dispatched and counted, and JSON logs only.

## Not built / deferred

## Deviations

## Tests
- added: 78 (41 S2-T01 / 15 S2-T02 / 22 S2-T03)
- suite result: 202 passed, 0 failed

## Artifacts updated
- 14 functional tests (signalforge/tests/events/)

## Security notes

## Known issues and technical debt

## Demoable checkpoint

## Coverage checklist ticks

## Entry criteria for next sprint:
