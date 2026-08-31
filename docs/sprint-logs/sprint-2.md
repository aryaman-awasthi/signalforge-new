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

- [S2-T04][REQ-023,025] Web: timeline primitives and the S21 settings shell.
  web/features/timeline/ - api.ts types track the frozen envelope field for field
  (a test fails if the contract gains a field the cockpit does not decode);
  store.ts merges by event_id and orders by seq, because at-least-once reaches
  the browser too once replay pages and the socket both feed it, and holds the
  seq anchor; useVirtualRows windows a fixed-height list so only the rows near
  the viewport mount and scrollTop maps exactly to an index, which is what lets
  the anchor jump without rendering everything above it; EventTimeline composes
  the list with TopicFilter (chips per topic and per namespace wildcard) and
  SeqAnchorBar (pin, clear, jump to a seq). web/features/platform/ - settings
  screen with Platform health (component pills, bus head/lag/DLQ counts, clock
  source with a visible "drill clock" badge, worker jobs with last error,
  retention watermarks), Dead letters (requeue, with an honest notice when no
  consumer for that group runs on this node) and the timeline pane. /settings
  route is thin; middleware guards it with the org role group as UX over the
  API's real RBAC. Refactor found by the live run: requeue moved off Dispatcher
  into DeadLetterService, because requiring a locally registered consumer group
  made requeue impossible in the split api/worker deployment the architecture
  allows; the API now records the intent, audits it, and reports drained_here.
  scripts/seed_bus_demo.py (demo tooling, not product code) puts a causal chain
  and one poisoned event on the bus so the panes have something real to act on
  before the S3 integrations. next build green.
  Verified: 8 structural REQ tests; live end-to-end through the cookie proxy -
  timeline, dead letter, requeue and drain to resolved.

- [S2-T05][REQ-020..025] Artifacts 07 and 10. docs/07-component-architecture.md
  authored: the layering rule and what enforces it (test_repo_layout walks the
  tree, check_clean guards the app-agnostic core, deps.Container as the single
  composition root), the event backbone as four cooperating pieces with mermaid
  diagrams, why publish is an outbox rather than a queue call, why ordering is
  per partition, how at-least-once plus the ledger makes duplicates harmless,
  where failure goes, the three clocks and which component holds which, the
  observability surfaces, the process model with its job table, the web feature
  split, and a table of what each component may and may not assume.
  docs/10-data-design.md generated by scripts/gen_erd.py (make erd) from the same
  Base.metadata Alembic autogenerates from, so the document cannot drift: mermaid
  ERD per group, every column with nullability and foreign keys, every index with
  the query it serves, plus authored notes on the retention planes, the chain and
  its anchors, lane assignment, tenant isolation and the two SQLite behaviours a
  Postgres move must handle. 5 doc-pinning tests, including one asserting the
  generator is reproducible and one that fails if a new table is missing from
  docs/10. specs statuses REQ-020..025 -> built; RTM, test report and stories
  regenerated; coverage checklist ticked; DR-012..DR-018 recorded.

## Not built / deferred
- none. Every S2 task shipped complete.

## Deviations
- No contract deviations. signalforge/contracts/ and the specs ids are untouched;
  Plane, Topics.CONNECTOR_HEALTH, the envelope's store-assigned fields and
  partition_key were used exactly as frozen in S0.
- One design change made mid-sprint and recorded as DR-018: requeue moved off
  Dispatcher onto DeadLetterService after the live run showed the original
  placement made requeue impossible on an api-only node. No requirement changed.
- Generic domain error categories moved to domain/shared/errors.py and re-exported
  from domain/identity/errors.py, so a second bounded context does not import
  identity for NotFoundError. Behaviour-preserving; the S1 suite proves it.

## Tests
- added: 91 (41 S2-T01 / 15 S2-T02 / 22 S2-T03 / 8 S2-T04 / 5 S2-T05)
- suite result: 215 passed, 0 failed (docs/21-test-report.md generated by make report)

## Artifacts updated
- 07 component architecture (docs/07-component-architecture.md, authored)
- 10 data design (docs/10-data-design.md, generated by make erd)
- 14 functional tests (signalforge/tests/events/, 77 tests; plus web and doc tests)
- 20 RTM regenerated (docs/20-RTM.md - 14/14 S0-S2 requirements green, 215 tests joined)
- 21 test execution report (docs/21-test-report.md, generated)
- 03 stories regenerated from specs
- 05 specs: requirements.yaml statuses REQ-020..025 -> built
- decision-record.md: DR-012 through DR-018

## Security notes
- New surfaces: GET /events replay and causal chain, the seal endpoints,
  /platform/status, /platform/dead-letters with requeue, /platform/agent-runs,
  /metrics. Every one that can name a tenant sits behind the RBAC dependency and
  is covered by the router-coverage walk in the security suite.
- Two endpoints are deliberately unauthenticated and the compensating control is
  capability rather than policy (DR-017): /health returns component statuses and
  no identifiers, and the metrics registry rejects tenant, incident, user and org
  label names at declaration time, so an identifying platform metric cannot be
  declared. Both properties are asserted in signalforge/tests/security/.
- Tenant isolation extended to the new stores: replay, seals, dead letters and
  agent runs all carry the predicate inside the repository. Two cross-tenant
  paths exist and are documented at their definitions and in docs/10 section 6:
  EventStore.read_for_dispatch (one dispatcher serves the install; handlers get
  the tenant on the envelope) and EventStore.tenants (the retention work list).
- The audit chain is unchanged and still cannot be disabled. Dead-lettering, seal
  creation, requeue and retention prunes each write an audit entry, so the new
  failure paths are evidenced rather than only logged.
- Retention is the only code path that deletes a recorded event; it refuses the
  incident plane in two independent places and refuses to cross a seal. Prunes
  leave a hash anchor, so a prune is provable and a deletion is detectable.
- Residual risks recorded: seq is a global counter, so a tenant can infer the
  install's total event volume from gaps in its own timeline (accepted - the
  architecture specifies a global recording order, and the alternative, per-tenant
  sequences, would break the single-dispatcher ordering guarantee); /metrics is
  unauthenticated on the loopback bind; the chain tip is read and written in one
  transaction, which is safe on SQLite's single writer and needs an explicit lock
  on Postgres (named in docs/10 section 8).

## Known issues and technical debt
- make still requires an elevated install (S0 carry-over); every target verified by
  running its recipe directly, including the new make erd.
- Web port 3000 vs cockpit 3001 (S0/S1 carry-over, moves with observability work).
- The /ws endpoint does not exist yet, so the timeline is fed by replay paging
  only. The store and the catch-up call are already shaped for the socket
  (merge by event_id, resume from lastSeq); wiring lands with S3 notifications.
- scripts/seed_bus_demo.py owns its own consumer group, so the dead letter it
  parks reports drained_here=false in the UI and is drained with --drain. From S3
  the adapters' groups are registered in the running server and the button drains
  directly. This is the split-deployment behaviour, not a workaround.
- Lane count is fixed at 16; changing it needs a re-shard migration (DR-012).
- The retention sweep scans eligible rows per tenant per run. Fine at this scale;
  a batched delete with a seq ceiling is the obvious change if a tenant's signal
  plane ever gets large.

## Demoable checkpoint
1. `bash run.sh` - migrations apply, api on :8000 with the worker group started,
   web on :3000.
2. `curl -s localhost:8000/health` - status ok with database, event-bus and worker
   components. `curl -s localhost:8000/metrics | head -30` - Prometheus text;
   after a few seconds signalforge_events_published_total{topic="system.connector.health"}
   and signalforge_events_consumed_total climb together, which is the bus working
   end to end in the running product. The API's own log is JSON, one line per
   request, carrying a request id.
3. Sign up at http://localhost:3000/signup, then put traffic on the bus:
   `python scripts/seed_bus_demo.py --tenant <org id from signup> --poison`.
4. Open http://localhost:3000/settings:
   - Platform health: components, head seq, per-group lag, dead-letter counts,
     clock source (wall), the three worker jobs with run counts.
   - Dead letters: one row, state dead, 3 attempts, the handler's error. Press
     Requeue - it moves to requeued and says plainly that no consumer for that
     group runs on this node.
   - Event timeline: the causal chain with topic filter chips and a seq anchor.
5. `python scripts/seed_bus_demo.py --tenant <org id> --drain` plays the worker
   that owns that group; refresh - the dead letter is resolved.
6. Recorded live this sprint: replay over the cookie proxy returning
   alert.fired -> incident.declared -> incident.paged -> incident.brief.sent in
   seq order with causation links; GET /events/{id}/chain returning that exact
   chain out of interleaved noise; seal created by an incident commander,
   verified by an auditor, and the auditor's own seal attempt refused with
   403 permission close_incident.
7. `.venv/Scripts/python.exe -m pytest -q` - 215 passed. The acceptance test
   signalforge/tests/events/test_crash_recovery.py spawns a real consuming
   subprocess, kills it mid-consumption and proves nothing was lost, nothing was
   applied twice and order held within each partition.
8. `python scripts/gen_rtm.py`, `python scripts/gen_erd.py`,
   `bash scripts/check_clean.sh` - all green.

## Coverage checklist ticks
- S2 Transactional outbox, dispatcher, consumer groups, offsets, ordered per incident
- S2 Retries, dead letters + requeue UI, catch-up after crash
- S2 Replay API; signal vs incident planes; seal with hash; virtual-clock time stamps
- S2 Platform self-observability: logs, /metrics, /health, connector health events

## Entry criteria for next sprint: met
- Sprint 2 acceptance satisfied on all three counts: a real process killed
  mid-consumption loses no event and preserves per-partition order (subprocess
  test); a poisoned handler visibly lands its event in the dead-letter store and
  requeue works, shown both in tests and live in the settings screen; replay over
  HTTP returns the exact causal chain. Artifacts 07, 10, 14, 20, 21 updated,
  run.sh boots api + web with the worker group, check_clean green.
- S3 can start against a bus that is already ordered, replayable and observable:
  adapters register a HealthSource and a consumer group, and publish through the
  same outbox.
