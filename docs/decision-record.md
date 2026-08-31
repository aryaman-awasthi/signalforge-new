# Decision record

Owner: engineering. Two parts: DR-xxx entries record decisions taken during the build,
newest first (format: id / date / decision / why / consequences); the "Imported memory
graph" section at the bottom is the authored pre-build decision record
(signalforge-memory-graph.md, provided and imported 2026-09-01) - every N-line there
is treated as already decided.

## DR-024 (2026-09-01, S3) The capability matrix is a declaration view, not a runtime view
Coverage per kind is computed from the manifests' `availability`, so an
interface-only adapter is listed and never counted, while a `built_in` manifest
whose runtime is missing still counts. Why: the matrix answers "what can this
installation's adapter set do", which is a property of the adapters, and keeping
it a pure function of manifests keeps it in the domain with no knowledge of what
a particular node happens to have loaded. The runtime question is answered
separately and per node by the registry listing's `runnable`/`status`.
Consequence: a packaging fault (manifest without runtime) shows as "runtime
missing" in the listing rather than as a coverage gap, and the two views are
documented as different questions.

## DR-023 (2026-09-01, S3) Connector health is polled through a provider, not a fixed list
ConnectorHealthService gained register_provider: an async callable re-asked on
every poll. Static registration was enough for the database, the bus and the
worker, which exist for the process's lifetime; bindings do not. Why: a binding
created, disabled or deleted while the process runs has to join or leave the
loop without a restart, and "an operator turned it off" must mean it stops being
polled now. Consequence: one extra database read per poll cycle, and a provider
that raises is logged and skipped rather than costing the platform its own
verdict.

## DR-022 (2026-09-01, S3) A HealthSource knows which tenant it belongs to
HealthSource gained tenant_id, app_id and metric_label; a null tenant means the
platform itself. Why: one poll loop now produces both this node's readiness and
every customer's connector health, and three different rules have to follow from
one fact. Readiness (/health) counts platform sources only, so a customer's dead
integration never takes a node out of rotation. Visibility (/platform/status)
filters to the asking tenant. Metrics key on metric_label - the adapter id, not
the binding id - because /metrics has no session, and several bindings of one
adapter collapse to their worst verdict. Consequence: platform_report() was
split from report(), and signalforge_connector_bindings{state} was added so the
scrape can count bindings without naming one.

## DR-021 (2026-09-01, S3) A binding with no runtime is UNKNOWN, never UP or DOWN
An adapter whose manifest is installed but whose runtime is not - the honest
state of every enterprise connector in this deployment - reports UNKNOWN with
the reason, and the integration health screen shows that rather than a green
tick or a red cross. Why: nothing was measured, and both a tick and a cross
claim a measurement. Overall status already treats UNKNOWN as degraded rather
than healthy (S2), so an unmeasurable connector cannot be mistaken for a
working one. Consequence: the health table has three failure shades - terminal
(auth rejected, orphaned binding, missing credential) is DOWN at once, transient
(timeout, unreachable, ping false) is DEGRADED until the third consecutive
failure, and unmeasurable is UNKNOWN.

## DR-020 (2026-09-01, S3) Credential scope is part of the binding's identity
Bindings are unique on (tenant, app, adapter, credential_scope), so read-only
and remediation for the same adapter are two rows with two secrets. Why the
architecture already said separate keys: they have different blast radii, and a
single row with a scope field would have made rotating or revoking one touch the
other. Consequence: the wizard creates one binding per scope, the floor
"read-only can never drive remediation" is structural rather than a check, and
deleting a binding deletes exactly its own secrets because the binding id is in
the secret name.

## DR-019 (2026-09-01, S3) The capability catalogue is read off the SPI, not written down
A manifest's `capabilities` are validated against the abstract methods of that
kind's SPI class, reflected at import time. Why: the alternative is a second
list beside contracts/spi.py that drifts the first time a kind gains a method,
and a manifest promising a capability with no SPI name fails at 3am with an
AttributeError instead of at load with a sentence. Consequence: adding an SPI
method makes it declarable on the same commit; `ping` is excluded because every
adapter must answer it and the health loop depends on that being unconditional.

## DR-018 (2026-09-01, S2) Requeue is a state transition, not a dispatcher call
Requeueing a dead letter marks it for redelivery and audits the operator's
intent; whichever dispatcher owns that consumer group drains it. The first
implementation put requeue on Dispatcher and refused when the group was not
registered there. Why changed: the architecture allows splitting api and worker
into two processes, and on an api-only node the API owns no consumer groups at
all, so requeue would have been impossible in a deployment shape we ship for.
Consequence: DeadLetterService owns list and requeue; the API reports
drained_here so an operator is told when no local worker will pick it up, and
the UI surfaces that instead of pretending it moved.

## DR-017 (2026-09-01, S2) /health and /metrics are unauthenticated, and cannot name a tenant
A liveness probe has no session and a Prometheus scrape has no token, so both
stay public on the loopback bind. The compensating control is capability, not
policy: /health returns component statuses only, and the metrics registry
rejects tenant, incident, user and org label names at declaration time, so a
metric that could identify a customer cannot be declared. Both are asserted in
the security suite alongside the router-coverage walk. Consequence: the public
route list in test_authz_matrix grew by two entries, each justified there;
anything an operator needs that does name a tenant lives on /platform/status
behind manage_apps.

## DR-016 (2026-09-01, S2) Platform-scoped events use a reserved _platform tenant
Events about the platform itself - connector health, and later adapter
lifecycle - are published under tenant id "_platform" rather than duplicated per
customer or given a nullable tenant. Why: the envelope contract requires a
tenant, replay is tenant-scoped, and a reserved id means no customer can read
platform internals through their own timeline without a single special case in
the query path. Consequence: operators read them through /platform/status and
/metrics; if a per-tenant view of adapter health is wanted in S3, adapters
publish under the real tenant because they are bound to one.

## DR-015 (2026-09-01, S2) Sealing is close_incident work; replay is read_audit
The frozen roles matrix makes Auditor strictly read-only, so sealing - which
writes a permanent record - cannot hang off read_audit. It hangs off
close_incident, which matches when a seal is actually taken: at the end of an
incident. Replay, the audit surface, hangs off read_audit. Why: keeping the
frozen matrix honest beats inventing a permission. Consequence: an Auditor can
verify any seal but never create one; the S4 incident lifecycle calls the same
seal service when an incident closes.

## DR-014 (2026-09-01, S2) The hash chain is per (tenant, plane), anchored on prune
Signal-plane events age out; incident-plane events are kept. One chain across
both would be broken by the first retention prune, so chains are per (tenant,
plane) and a prune records the payload hash of the last event it removed.
Verification of the survivors starts from that anchor. Why: without it a prune
and a deletion are indistinguishable, which would make the whole chain
unfalsifiable. Consequence: retention_watermarks is part of the audit evidence,
not bookkeeping; seals bind (seq, event_id, payload_hash, prev_hash) so position
and identity are covered, not only content.

## DR-013 (2026-09-01, S2) Two clocks in the container, wall time for anything durable
wall_clock drives token expiry, refresh windows, rate limits, recorded_at,
retention cutoffs and offsets; clock is the switchable platform clock a drill can
point at scenario time, and only occurred_at follows it. Why: a drill running at
100000x would otherwise expire every session and delete a week of real telemetry,
both of which are now asserted by test. Consequence: an injected clock (tests,
drills) is used for both, which is what lets a frozen clock still prove an expiry;
S7's drill engine switches only the platform clock.

## DR-012 (2026-09-01, S2) Lane count is a stored on-disk constant, not config
Lanes come from blake2b(partition_key) % 16, computed at write time and stored on
the row. Why: the mapping must be identical across processes and restarts, and
Python's hash() is salted per process - two workers disagreeing about a lane is
silent reordering rather than a visible error. Consequence: changing LANE_COUNT
re-shards the bus and requires a migration that recomputes every lane value; the
constant lives in the domain, not in Settings.

## DR-011 (2026-09-01, S1) Escalation test-fire is the IC-only demo endpoint
S1's acceptance needs an Executive denied an "IC-only endpoint", but incident
endpoints only land in S4. The simulated escalation test-fire (POST
/escalation/policies/{id}/test) is gated on run_drills - held by incident
commander and org owner, never Executive - and is a genuine S1 feature (verify
paging order and timers before a real incident, nobody actually paged, audit
event emitted). Why: an honest permission-gated endpoint beats a placeholder;
no stubs in product code. Consequence: the S4 incident routers add
declare/severity/close gates on top; this endpoint stays.

## DR-010 (2026-09-01, S1) Dev key fallback under var/keys/, env always wins
SIGNALFORGE_JWT_SECRET and SIGNALFORGE_FERNET_KEY come from the environment;
on the single dev machine, absent vars are generated once and persisted under
var/keys/ (gitignored) so restarts keep sessions and secrets decryptable. Why:
run.sh must boot with zero setup, and an ephemeral key would silently invalidate
every session and stored secret on restart. Consequence: production posture
requires the env vars; recorded as a residual risk in docs/12 section 4.

## DR-009 (2026-09-01) Imported docs genericize the reference app's name
The authored architecture and memory graph were imported into docs/06 and this file
with the reference app's name replaced by generic phrasing. Why: CLAUDE.md rule 2
(enforced by check_clean.sh) allows that string only in reference-app/, drills/,
seeds/ and tests; a verbatim import would fail the pre-commit guard. Consequence:
content is otherwise faithful; the originals remain in playbook/ (ungoverned by the
guard) for exact reference.

## DR-008 (2026-09-01) Stories and RTM are generated views
docs/03 and docs/20 are rendered by scripts from specs/requirements.yaml plus recorded
test results. Why: one source of truth; handwritten copies drift. Consequence: editing
those docs by hand is prohibited; regenerate instead.

## DR-007 (2026-09-01) 51 requirements instead of ~40
Granularity chosen so each sprint's tests tag precisely (e.g. UNCERTAIN handling
REQ-062 separate from gateway decisions REQ-061). Ids are stable and never renumbered.

## DR-006 (2026-09-01) Every prohibited control is floor
specs/controls.yaml marks all prohibited entries floor: true and a test enforces it.
Why: a "configurable prohibition" is an oxymoron; the floor is the demo's security story.

## DR-005 (2026-09-01) Reconcile-before-retry lives in the actions SPI
ActionsAdapter.get_execution(idempotency_key) is part of the frozen contract, not an
infrastructure afterthought. Why: "API succeeded but response lost" must be answerable
by every actions integration or safe retries are impossible (guide section 06).

## DR-004 (2026-09-01) Chaos inject requires an explicit scope argument
No default value, enforced by test. Why: chaos outside an authorized drill scope is
prohibited at the floor; the SPI shape should make the unsafe call unwritable.

## DR-003 (2026-09-01) Push ingest signs timestamp.body with HMAC-SHA256
Headers X-SignalForge-Signature/-Timestamp(/-Key-Id), 300 s replay window, event_id as
the idempotency unit, 202 async. Why: replay resistance plus at-least-once semantics
without synchronous processing on the ingest path.

## DR-002 (2026-09-01) Envelope carries dual time and store-assigned fields
occurred_at (may follow a drill virtual clock) vs recorded_at (wall, store-assigned);
seq, payload_hash, prev_hash also store-assigned. Why: drills replay realistically
(S2/S7) while the audit chain stays strictly in recording order.

## DR-001 (2026-09-01) check_clean assembles its own pattern from halves
scripts/check_clean.sh greps for the reference-app name without containing it, so the
guard cannot trip itself and needs no self-exclusion. Consequence: the sprint log and
docs must reference "the reference app" generically outside allowed paths.

---

# Imported memory graph (pre-build decisions, authored before Sprint 0)

Imported 2026-09-01 from signalforge-memory-graph.md (kept verbatim in playbook/);
the reference app's name is genericized per DR-009. Where an N-line conflicts with a
frozen contract or the playbook, the later artifact governs (noted inline).

## N1. Event
- TCS AI Friday Season 2 National Finals, Problem Statement 08, ARE-AI-01 "Autonomous
  Major Incident Intelligence Command Centre". Window 29 Aug to 4 Sep 2026, demo 4 Sep.
  Judges want a full product, not a demo.
- Judging musts: live evolving incident with bridge and chat, at least 3 hypotheses
  with visible confidence movement, historical mistake prevention, one human-approved
  multi-step recovery, partial-execution reconciliation, role-specific communication,
  layered recovery validation, audit replay. Humans own: declaration, severity,
  high-risk approvals, external comms, closure.

## N2. Environment
- Single machine, no Docker. Binary downloads allowed: Prometheus and Grafana are
  running. LLM only through TCS GenAI Lab LiteLLM proxy (OpenAI-compatible) via
  LangChain and LangGraph. No real regions, Teams, or telephony: regions, bridge,
  chat, SMS and voice are simulated and labelled SIMULATED.

## N3. Product = SignalForge (command centre) + the reference payments app (first onboarded)
- SignalForge: multi-tenant SaaS-style platform. Sign-up creates org, first user is
  Org Owner, invites team, assigns roles per app, onboards apps through a wizard,
  shows steady-state health, escalates Watch to Proposed to Active war room, validates
  recovery, closes with PIR and replay. Drills (game days) are a product feature.
- Reference app: Java 17 Spring Boot modular monolith, H2, Micrometer. Payments app
  with journeys UPI Pay, Card Pay, Wallet Top-up, Refund. Region is a label (A Mumbai,
  B Chennai) on requests, metrics, logs, health, config. Internal fabric simulator
  with links GW-A, GW-B, AGG-A, AGG-B, FIB-7, CORE, RAIL; per-region routing; failover
  reroutes B via GW-A. Chaos flags make real failures (fault is injected, consequences
  are real).
- Roles: Org Owner, Incident Commander, Tech Lead, SMEs (App, DB, Network, Payments),
  Comms Lead, Executive, Auditor. On-call order and escalation timers per role.
  RBAC per app.

## N4. Positioning (vs market)
- Competitors do "AI SRE that investigates" (Datadog Bits, PagerDuty agents,
  incident.io, BigPanda + ServiceNow, Resolve AI). SignalForge's whitespace, straight
  from the brief: versioned facts vs assumptions with provenance and preserved
  superseded versions, targeted re-planning not reset, reconcile-before-retry of
  uncertain executions, correction of already-issued comms, historical relevance
  downgrade on topology mismatch, four-layer recovery verdict with stability window.

## N5. Integration model (API-first, plug and play)
- Pull: SignalForge calls the app's `/observe/*` endpoints: metrics (Prometheus text,
  includes business counters like payments_success_total, payments_unknown_total,
  settlement_queue_depth), logs (search, aggregate, sample), traces (recent, by id),
  health (per component and region), actions (manifest, execute, executions by
  idempotency key), chaos (manifest, inject or clear; drill scope only). [Exact path
  shapes: signalforge/contracts/observe.py, frozen S0-T02.]
- Push: app or pipeline calls `/api/v1/ingest/{changes|alerts|topology|messages|tickets}`
  with per-app token + HMAC, idempotent by event_id, 202 async. Deploy script sends
  deployment.started before and deployment.completed after every change ("change window").
- Business KPI connector was removed as a kind; business signals ride on metrics plus
  synthetic probes per journey and region (probes run by SignalForge with a synthetic
  header, excluded from KPIs).
- Adapter framework: one fixed SPI and canonical model per kind (metrics, logs,
  traces, health+probes, alerts, changes, topology, collaboration, itsm, actions,
  notifications, knowledge, chaos). Adapters are folders with manifest.yaml +
  adapter.py + config.schema.json, discovered by a registry, bound per app with
  encrypted credentials. Read-only and remediation scopes are separate bindings.
  Enterprise adapters (Datadog, Splunk, ServiceNow, Jenkins, GitHub, Grafana)
  implement the same SPIs; in the demo they exist as interface + mock, shown
  "Available" in the registry.
- Manifests rule: schema is hardcoded in SignalForge; content is declared by the app
  (app kit annotations serve action and chaos manifests). Agents may propose (PR) and
  compose (Drill Author agent drafts drill timelines from manifests + topology, human
  saves) but never invent faults or actions.
- Grafana wiring: Prometheus scrapes the app's `/observe/metrics`; SignalForge's
  prometheus adapter uses query_range; Grafana Alerting posts to ingest alerts;
  optional embedded Grafana panel in Health panels with built-in charts as default and
  fallback. Provisioned by YAML, started by run.sh.

## N6. Event bus and storage
- Append-only `events` table in SQLite WAL is the source of truth (transactional
  outbox). In-process dispatcher, consumer groups with stored offsets, at-least-once +
  idempotent handlers, ordering per partition_key (incident_id else app_id), retries
  then dead_letters, replay endpoint, hash chain sealed at close. Backend is itself an
  adapter (swap to Redis Streams or Kafka later). Postgres-ready via SQLAlchemy.
- Bus carries facts ("something happened"), never telemetry. Metrics go to 5 s
  rollups; logs and traces are fetched on demand into evidence objects (summary +
  reference), progressive broad to aggregate to narrow, with query and size budgets.
- Envelope: event_id, seq, topic, tenant_id, app_id, incident_id, occurred_at (drill
  virtual clock) vs recorded_at, producer, actor, source {connector, authority,
  freshness, simulated, ref}, confidence, correlation_id, causation_id, supersedes,
  idempotency_key, payload. [Frozen shape: signalforge/contracts/envelope.py.]
- Topic families: signal.*, change.*, topology.version.published, human.* (utterance,
  statement.extracted with types fact/assumption/question/decision/commitment/blocker/
  contradiction), incident.*, warroom.*, evidence.* (incl contradiction.detected),
  graph.* (node.asserted/invalidated, blastradius.updated), hypothesis.*, history.*
  (match, warning, relevance.updated), nba.*, plan.revised (kept/paused/added),
  action.* (policy.evaluated, approval.*, execution.started/checkpoint/completed/
  timeout/uncertain/reconciled, duplicate.prevented, compensated, takeover), comm.*
  (draft/approved/issued/contradicted/corrected), validation.* (layer.evaluated,
  window.*, verdict.issued), notification.*, learning.*, drill.*, system.*
  (connector.health, agent.run, llm.degraded, audit.sealed).

## N7. Agents (LangGraph, Incident Governor supervisor, event-triggered)
- Eleven agents per the brief. LLM only for language: Conversation Understanding,
  narrative parts of Hypothesis and NBA, Communication, join briefs, PIR prose, IC
  Copilot. Deterministic code: telemetry correlation and storm clustering, topology
  blast radius (NetworkX, confirmed vs predicted), change linking, historical
  topology-fingerprint distance, hypothesis confidence scoring (explicit evidence
  weights, full history), NBA scoring (urgency x impact x information gain minus
  risk), policy, Tool Gateway, validation.
- LLM hygiene: structured outputs, cache by input hash (warmed in rehearsal), token
  budget, 20 s timeout, deterministic fallback per agent, degraded-mode badge. Demo
  never depends on a live LLM call.

## N8. Governance and remediation
- Policy engine: user-editable per app (risk overrides, allow/approve/block, approver
  roles, approval expiry, confidence thresholds, retries, stability window, severity
  matrix, paging) with a hardcoded floor: high-risk classes (rollback, restart,
  failover, traffic shift, config change, customer-impacting) can never auto-execute;
  read-only can never mutate; expiry capped; audit cannot be disabled. Policy edits
  are versioned, on the timeline, dry-run preview, mid-incident edits require
  confirmation.
- Tool Gateway: validate, authn, authz, policy, approval with expiry, idempotency key,
  checkpoints, timeout leads to UNCERTAIN which blocks retry until a read-only
  reconcile determines COMPLETED_LATE / NOT_APPLIED / PARTIAL, then mark, retry,
  compensate or cancel; duplicate prevention; human takeover at any step.
- Validation: independent reads of the app (not the graph) on four layers: technical
  (error rate, p95, saturation), service (probes per journey and region), customer
  (no UNKNOWN or stuck transactions, SLO success), business (settlement queue, TPM).
  All pass, then stability window (15 min, resets on regression), then closure
  recommended; IC closes. Revalidation failure: verdict NOT RECOVERED with failing
  checks, back to Active, failure becomes hypothesis evidence (incomplete fix vs wrong
  cause), NBA proposes follow-up or declared rollback via approval, escalate after N
  cycles. No silent retry loops.

## N9. Detection (steady to declared)
- Golden signals: traffic, errors, latency, saturation, availability, business.
  Baselines on rolling windows, anomaly = beyond threshold sustained 3 evals. SLO
  burn: fast burn (~14x/5m) proposes an incident, slow burn (~6x/30m) opens Watch.
  Corroboration: propose only when 2+ independent families agree and one is
  customer-facing. Health score weighted (availability 30, errors 25, business 20,
  latency 15, saturation 10), worst region wins. Severity from impact (P0 Tier-1
  journey below ~80 percent everywhere or loss rate; P1 journey below SLO in one
  region or sustained 3x latency with errors or UNKNOWN accumulating; P2 no customer
  impact). Alert storms clustered by time + topology, children suppressed with
  visible count. Declaration is always human.

## N10. Demo scenario "Recovery Collision" (drill YAML drives it; all consequences real)
- Steady 2 percent errors, p95 180 ms. Config v12 (gateway timeout 3000 to 800 ms)
  deployed 09:51. Onset 10:03, Watch 10:04, Proposed 10:06, IC declares P0 10:07
  (INC-2026-0917). Hypotheses: H1 config 0.62, H2 DB pool 0.25, H3 network 0.13;
  discriminating test raises H1 to 0.72. Rollback v12 to v11 approved 10:14; region A
  completes; region B times out (injected server-side delay, apply lands late) so
  state UNCERTAIN; SRE says "rollback done in both regions" which becomes a flagged
  contradiction; retry blocked "external state unknown". FIB-7 loss 0.3 injected; A
  rolled back but errors persist. Topology v2 (both regions share FIB-7) published
  10:21: blast radius recomputed, H3 to 0.58, H1 to 0.35; failover B via GW-A 10:23
  (topology v3, plan diff kept/paused/added). INC-2025-0142 relevance 0.81 to 0.34 on
  topology mismatch; its "restart prolonged outage" warning stays and blocks restart.
  Exec update v1 (config-only cause, issued 10:15) marked CONTRADICTED; v2 redline
  approved 10:29. Reconcile finds v11 in B: COMPLETED_LATE, duplicate retry
  prevented. 312 payments UNKNOWN blocks the customer layer; reconciliation job
  (approved) resolves them; stability window; closed 10:52; audit sealed; PIR drafts
  with verified lessons only.

## N11. Stack and build
- SignalForge backend: Python 3.11, FastAPI, SQLAlchemy (SQLite WAL, Postgres-ready),
  LangGraph, api + worker in one process for demo. Cockpit: dark ops theme, fixed
  badge vocabulary (SIMULATED, UNCERTAIN, RECONCILED, DUPLICATE PREVENTED,
  CONTRADICTED, CORRECTED, CONFIRMED vs PREDICTED, READ-ONLY, REMEDIATION, PULL,
  PUSH). [Cockpit stack superseded to Next.js 14 + Tailwind by the architecture
  document, delta 1.] Reference app role unchanged. run.sh starts everything;
  reset.sh wipes to a seeded demoable state (org NovaPay Technologies; people: Priya
  Nair IC, Arjun Mehta SRE Lead, Kavya Rao DB SME, Rohit Sen Network SME, Meera Iyer
  Comms, Sanjay Kulkarni Exec, Anita Desai Auditor).
- Security baseline: argon2 + JWT + refresh, invites, RBAC matrix per app, tenant_id
  everywhere with enforced filters, Fernet-encrypted connector secrets, ingest token +
  HMAC, redaction list on evidence samples, platform /health and /metrics, dead-letter
  queue visible.
- Claude Code method: CLAUDE.md with design rules; contracts frozen first (envelope,
  SPIs, manifests, API shapes); one small task per session with acceptance tests;
  tests-first for policy, idempotency, reconciliation, RBAC. [Authored sprint split
  0-5 superseded by the playbook's S0-S7.]

## N12. UI state
- Claude Design prototype exists: 23 screens S1 to S23 (landing, org setup, 7-step
  wizard with Integrations cards showing adapter dropdown + PULL/PUSH and scope
  badges, discover and map with region-label selector and manifest-imported actions,
  topology, SLOs and policies, baseline and test, go live with readiness score;
  steady; watch; proposed; war room forming with ack board; war room cockpit;
  incident brain; NBA and approval; execution ledger; blast radius with topology
  versions; warning and guidance; communications with redlines; layered verdict; role
  views; copilot; drill console with chaos manifest and Generate a drill; closed with
  replay and PIR; settings with adapter registry and ingest; mobile paging;
  integration health).
- Pending UI fixes: per-adapter config fields under each Integrations dropdown
  (Prometheus needs base URL etc.; observe-* prefill from App base URL in Basics;
  push cards show copyable ingest URL + token instead of inputs; direction-correct
  test results, include one failure state), change-window chip, reference-UI panel
  names verbatim (Affected Services etc.), plan diff triad, policy editor with locked
  high-risk rows, per-row escalation timers, visible virtual clock in drills.

## N13. Open items
- Write the Recovery Collision drill YAML (timeline of human, machine, inject,
  checkpoint events with exact offsets and the endpoints each calls).
- Optional upgrade: run the reference app as two region processes sharing H2 (only if
  everything else is done).
- Build per sprint plan; rehearse twice from reset before the demo.
