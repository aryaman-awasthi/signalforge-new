# 06 - Solution architecture

Owner: engineering. Status: IMPORTED from the authored architecture document
(signalforge-architecture-final.md, provided 2026-09-01), replacing the S0-T04 skeleton.

## Import notes (deltas against the repo as built - the repo wins where frozen)
1. The reference payments app is named generically throughout this document: the
   app-agnostic core rule (CLAUDE.md rule 2, enforced by check_clean.sh) forbids its
   name outside reference-app/, drills/, seeds/ and tests. See reference-app/ for it.
2. Where endpoint spellings differ from signalforge/contracts/ (frozen S0-T02, e.g.
   /observe/actions/{action_id}/execute and /observe/actions/executions/{idempotency_key};
   query-param /observe/logs and /observe/traces; envelope event_id as UUID not ULID),
   the frozen contracts are authoritative; this document records the authored intent.
3. Repo layout follows CLAUDE.md (reference-app/ subdirectory, docs/06 not
   docs/ARCHITECTURE.md, roles matrix in contracts/roles.py); section 7 below is the
   authored layout, kept for the record.
4. Ports: the cockpit runs on 3001 (Grafana takes 3000). The Sprint 0 scaffold used
   3000 for the web dev server; it moves to 3001 when Grafana is provisioned - tracked
   as a known item in the sprint log.
5. The authored sprint numbering (0-5) predates the playbook's 8-sprint plan (S0-S7);
   the playbook governs.

Stack locked: **Spring Boot** (the reference payments app, the first onboarded app),
**FastAPI** (SignalForge platform), **Next.js + Tailwind CSS** (cockpit; replaces the
earlier Angular choice), **on-device RDBMS: SQLite in WAL mode** for SignalForge and
**H2 file mode** for the reference app. Everything runs on one machine, no Docker.
Prometheus and Grafana run as downloaded binaries. LLM only through the GenAI Lab
LiteLLM gateway - chat *and* embeddings - reached through a single provider whose
approved host is a constant in code rather than configuration (S5-T02, REQ-051).

Why these databases: SQLite WAL gives SignalForge a zero-install, transactional,
single-file RDBMS that supports the append-only outbox and concurrent readers;
SQLAlchemy models are Postgres-ready, so enterprise deployment is a connection-string
change. H2 in file mode is native to Spring Boot and gives the reference app durable,
restart-safe storage with `AUTO_SERVER=TRUE` if a second region process is ever added.
If a full server RDBMS is ever demanded, a local PostgreSQL binary drops in behind the
same SQLAlchemy models; nothing else changes.

---

## 1. System diagram (processes and ports)

```text
                                   BROWSER (judges, team)
                                          |
                          Next.js cockpit  http://localhost:3001
                          (App Router, Tailwind, WebSocket client)
                                          |
                          REST + WebSocket (JWT bearer)
                                          |
 +----------------------------------------v-----------------------------------------+
 |  SIGNALFORGE  (FastAPI, one process: api + worker)          http://localhost:8000 |
 |                                                                                   |
 |  api/        auth, orgs, users, roles, apps, wizard, incidents, actions,          |
 |              approvals, comms, validation, drills, copilot, events, /ws           |
 |  ingest/     /api/v1/ingest/{changes|alerts|topology|messages|tickets}  (HMAC)    |
 |  core/       detection, lifecycle, warroom+paging, graph, evidence, hypothesis,   |
 |              nba, history, policy+gateway, ledger, comms, validation, drills,     |
 |              audit+replay, notifier                                               |
 |  agents/     LangGraph Incident Governor + agents   --->   LiteLLM proxy          |
 |  bus/        events outbox, dispatcher, consumer groups, offsets, replay          |
 |  adapters/   metrics|logs|traces|health|alerts|changes|topology|collab|itsm|      |
 |              actions|notifications|knowledge|chaos  (registry + manifests)        |
 |  db:         data/signalforge.db  +  data/rollups.db   (SQLite WAL, SQLAlchemy)   |
 +----+-------------------+--------------------+---------------------+--------------+
      | pull /observe/*   | query_range        | iframe panels       | webhook (alerts)
      v                   v                    v                     |
 +-----------+      +-----------+        +-----------+               |
 | REFERENCE |<-----| PROMETHEUS|<-------|  GRAFANA  |---------------+
 | APP :8080 |scrape| :9090     |  ds    |  :3000    |  alert rules post to ingest
 +-----------+      +-----------+        +-----------+
 Spring Boot, H2 file db, Micrometer, fabric simulator, region label A|B,
 /observe/{metrics,logs,traces,health,actions,executions,chaos}, traffic generator

 reference-app config repo:  local git repo; deploy.sh applies config and posts
                             deployment.started / deployment.completed to ingest
```

Trust boundaries: cockpit to SignalForge uses JWT; ingest uses per-app token + HMAC;
SignalForge to the reference app uses two API keys (read-only for /observe pulls,
remediation for /observe/actions); chaos endpoints accept only the drill-scoped key.

## 2. Stack summary

| Component | Technology | Port | Storage |
|---|---|---|---|
| Reference app (onboarded) | Java 17, Spring Boot 3, Micrometer + Prometheus registry, springdoc | 8080 | H2 file under its data/ |
| SignalForge platform | Python 3.11, FastAPI, SQLAlchemy 2 (async, aiosqlite), Alembic, LangGraph, pydantic v2, APScheduler for pollers | 8000 | SQLite WAL (`signalforge.db`, `rollups.db`) |
| Cockpit | Next.js 14 (App Router), TypeScript, Tailwind CSS, React Query, Zustand, Recharts, native WebSocket | 3001 | none |
| Metrics stack | Prometheus, Grafana (provisioned) | 9090, 3000 | their own files |
| LLM | TCS GenAI Lab LiteLLM gateway (`https://genailab.tcs.in/v1`), OpenAI-compatible over httpx; one `llm.provider`; models by classification, ids from config | external | `llm_cache` table in signalforge.db |

### 2.1 Model path configuration (S5-T02, REQ-051)

Every live model call - chat and embeddings alike - leaves through one provider,
`signalforge/infrastructure/llm/provider.py`, implementing the domain port
`domain/llm/ports.LlmProvider`. Application code holds the port; no application or
domain module imports an SDK or names a model id. The full template with comments is
`.env.example`; nothing here is required for the platform to boot.

| Variable | Default | Notes |
|---|---|---|
| `GENAILAB_BASE_URL` | `https://genailab.tcs.in/v1` | Accepted with or without `/v1`. The **host** is an allowlist in `infrastructure/llm/hosts.py`, not configuration: an endpoint outside it is refused, no client is built, and the platform runs deterministically. |
| `GENAILAB_API_KEY` | *(unset)* | `OPENAI_API_KEY` is accepted as a compatibility alias; `GENAILAB_API_KEY` wins and the alias selects nothing - the endpoint stays the gateway. `OPENAI_BASE_URL` and `OPENAI_API_BASE` are ignored outright. |
| `LLM_MODEL_GENERAL` | `genailab-maas-gpt-5.4-mini` | Fast path: classification, extraction, short rewrites. |
| `LLM_MODEL_REASONING` | `genailab-maas-gpt-5.4` | Multi-step judgement. |
| `LLM_MODEL_EMBEDDINGS` | `azure/genailab-maas-text-embedding-3-large` | kb-index blends these with BM25 and falls back to BM25 alone when they degrade. |
| `LLM_TIMEOUT_S` | `20` | Bounded, capped at 120s. Every call has one. |
| `LLM_CACHE` | `true` | `llm_cache`, keyed by model + prompt/config version + normalised input + schema, scoped per tenant. |
| `LLM_MAX_ATTEMPTS` | `3` | Retries transient failures only (timeouts, transport, 429, 5xx); a 400 or 401 is never retried. |
| `LLM_INCIDENT_TOKEN_BUDGET` | `200000` | Per incident, checked before a call and charged after it. |
| `LLM_CA_BUNDLE` | *(unset)* | TLS verification is always on; a corporate interception certificate is presented as a CA bundle. There is no variable that disables verification. |

A missing key, an unreachable gateway, a timeout, an off-schema answer or a spent
budget all land in the same place: the caller's deterministic fallback, with
`degraded=True`, a closed-vocabulary reason and one `system.llm.degraded` event per
episode. Verify an environment before a demo with `python scripts/llm_smoke.py`
(opt-in, the only thing in the repository that talks to a live model).

## 3. Reference app (Spring Boot) internals

Modules (single Gradle/Maven project, package per module):

- `payments`: REST API for the four journeys (UPI, Card, Wallet, Refund); every request
  carries `X-Region`; synthetic probes send `X-Synthetic: true` and are excluded from
  business counters.
- `ledger`: H2 tables payments, settlements, executions; payment states INITIATED,
  PENDING, SUCCESS, FAILED, UNKNOWN (UNKNOWN arises from real races when the fabric
  fails mid-flight).
- `fabric`: the network simulator; links GW-A, GW-B, AGG-A, AGG-B, FIB-7, CORE, RAIL
  with injectable latency, loss, down; per-region routing table; failover swaps B's route.
- `config`: per-region runtime config registry (timeouts, pool size, route), hot-applied;
  versioned; `apply` supports a chaos-injectable server-side delay so a client timeout
  can hide a late success.
- `observe`: the app-kit surface. `/observe/metrics` (Prometheus text with region and
  journey labels, includes business counters), logs search/aggregate/sample over the
  JSON log file, traces recent/by-id from an in-memory ring buffer, `/observe/health`
  per component and region, actions manifest + execute + executions by idempotency key,
  chaos manifest + inject.
- `actions`: rollback-config (per region, idempotent, reconcilable), failover-route,
  throttle-traffic, run-reconciliation-job, read-config, restart-gateway (exists so
  policy can block it). Every execution writes to the executions table first, which is
  what reconcile reads.
- `chaos`: `@ChaosFault` annotated faults: fabric.link.loss, rollback.delay,
  db.pool.shrink, rail.latency; manifest generated by annotation scan; guarded by the
  drill key.
- `trafficgen`: seeded generator placing real payments for both regions at a steady rate.

Logging: logback JSON encoder to a JSONL file with ts, level, requestId, traceId,
region, journey, endpoint, status, latencyMs, errorType, message.

## 4. SignalForge (FastAPI) internals

Process model: one process, two asyncio groups. `api` serves REST + `/ws`; `worker`
runs the dispatcher, pollers (metrics 5 s, health 5 s, probes 30 s, connector health
30 s), the agent runtime, notifier, validation ticks and the drill clock. Split into
two processes for enterprise by a flag.

- `contracts/`: frozen first: event envelope, adapter SPIs, canonical models, action
  and chaos manifest schemas, /observe and /ingest shapes, roles matrix.
- `bus/`: `events` table (seq, event_id, topic, partition_key, tenant_id, envelope
  JSON, prev_hash, hash) written in the same transaction as state (outbox); dispatcher
  fans out to consumer groups with one ordered lane per (group, hash(partition_key));
  `consumer_offsets`, `dead_letters`; `GET /events` replay; hash chain sealed at close.
- `core/detection`: rollups to `rollups.db`, baselines, anomaly (3 sustained evals),
  SLO burn (fast ~14x/5m proposes, slow ~6x/30m opens Watch), corroboration (2
  families, one customer-facing), health score, storm clustering, severity proposal.
- `core/lifecycle` + `warroom`: state machine Steady, Watch, Proposed, Active,
  Recovering, Closed; human declaration and closure; paging by blast radius with ack,
  escalation timers, join brief, commitments to tasks.
- `core/graph`: versioned nodes (fact, assumption, hypothesis, evidence, impact,
  action, approval, communication) with provenance; NetworkX topology per version;
  blast radius confirmed vs predicted.
- `core/evidence`: progressive broad, aggregate, narrow; budgets; summaries +
  result_ref; redaction.
- `core/policy` + `gateway`: user-editable YAML above a hardcoded floor (high-risk
  never auto-executes); approvals with expiry; idempotency; checkpoints; timeout leads
  to UNCERTAIN, reconcile before retry; duplicate prevention; compensation; human
  takeover; execution ledger.
- `core/comms`: drafts by audience grounded on graph node ids; lifecycle draft,
  approved, issued, contradicted, corrected with redlines.
- `core/validation`: independent reads of the reference app on four layers; stability
  window with reset; closure gate; failure path returns the incident to Active and
  feeds hypotheses.
- `agents/`: Governor (LangGraph supervisor) + Conversation Understanding, Telemetry,
  Topology, Change, Historical, Hypothesis, NBA, Safety, Communication, Validation.
  LLM only where language lives; structured outputs; cache by input hash; budget;
  20 s timeout; deterministic fallback per agent; degraded badge.
- `adapters/`: registry over `manifest.yaml` folders; real set (prometheus,
  observe-logs, observe-traces, observe-health + synthetic-probe,
  grafana-alert-webhook, deploy-webhook, topo-import, teams-sim, itsm-lite,
  observe-actions, page-sim + in-app, kb-index, observe-chaos) plus interface + mock
  for datadog, splunk, servicenow, jenkins, github.
- `auth`: argon2id, JWT access 15 min + rotating refresh, invites, RBAC dependency on
  every router from the roles matrix, tenant_id filter enforced in the repository layer.

## 5. Cockpit (Next.js + Tailwind)

- **Next.js 14 App Router, TypeScript.** Almost everything is a client component driven
  by the live event stream; SSR is not needed, so run `next build && next start` (or
  `next dev`) on 3001 with CORS allowed from SignalForge.
- **Auth**: login posts to FastAPI; refresh token in an httpOnly cookie set through a
  Next.js route handler proxy (`/app/api/auth/[...]/route.ts` forwards to :8000 so the
  cookie is first-party); access token kept in memory; `middleware.ts` guards route
  groups by role claim.
- **Routes mirror the 23 screens**:

```text
app/
  (public)/login, signup
  (org)/setup                      S2
  (app)/apps                       S4
  (app)/apps/[appId]/onboard       S3 wizard, steps as nested routes 1..7
  (app)/apps/[appId]               S5, S6 (Watch banner is state, not a route)
  (incident)/incidents/[id]        S9 war room shell with panel slots
      brain      S10   actions    S11   ledger   S12   topology S13
      history    S14   comms      S15   verdict  S16   copilot  S18
  (incident)/incidents/[id]/replay S20
  (roles)/exec/[id], sme/[id], comms/[id], audit/[id]   S17
  (drills)/drills, drills/[runId]  S19
  (settings)/settings              S21 (adapters, ingest, policies)
  (settings)/integration-health    S23
  m/page/[pageId]                  S22 mobile paging
```

- **Realtime**: one native WebSocket to `ws://localhost:8000/ws?token=...`; server
  filters by tenant and role; messages are bus envelopes. Client keeps `last_seq` in
  Zustand and on reconnect catches up via `GET /events?after_seq=`. React Query handles
  request data; the socket invalidates or patches query caches by topic.
- **State**: Zustand stores per incident (situation, hypotheses with history, plan,
  ledger, verdict) fed only by events, so the replay scrubber reuses the same reducers
  over `GET /events?until=`.
- **UI kit**: Tailwind config seeded from the Claude Design `styles.css` tokens
  (colors, spacing, type scale); shared components for the fixed badge vocabulary,
  state pills, confidence bar + sparkline (Recharts), plan diff rows, ledger stepper,
  layer tiles, ack rows, topology view (SVG from the graph JSON), Grafana iframe panel
  with the built-in chart fallback.
- **Role views** are layouts over the same event stream with panels filtered
  client-side and enforced server-side.

## 6. Cross-service contracts (unchanged, restated)

- Pull: SignalForge to the reference app `/observe/*` with the read-only key; actions
  with the remediation key; chaos with the drill key.
- Push: the app's pipeline and Grafana to SignalForge `/api/v1/ingest/*` with token +
  HMAC, idempotent by event_id, 202.
- Prometheus scrapes the app's `/observe/metrics`; SignalForge's prometheus adapter
  uses `query_range`; Grafana panels optionally embedded in the cockpit.
- Bus topics, envelope, adapter SPIs and manifests exactly as in the decision record
  (N5, N6).

## 7. Repository layout (authored; the repo follows CLAUDE.md - see import note 3)

```text
repo/
  <reference app>/      Spring Boot app (modules above), logs/, data/
  <its config repo>/    shared.yml, region-a.yml, region-b.yml, deploy.sh
  signalforge/          FastAPI: api/ ingest/ core/ agents/ bus/ adapters/ contracts/
                        models/ config/ tests/
  web/                  Next.js cockpit (structure in section 5)
  observability/        prometheus.yml, grafana/provisioning/{datasources,dashboards,alerting}
  drills/               recovery_collision.yaml, transcripts/*.jsonl, topology/v1..v3.json, pirs/*.md
  data/                 signalforge.db, rollups.db, secrets.key   (gitignored)
  docs/                 architecture (this), demo script, runbook
  run.sh  reset.sh
```

`run.sh` order: reference app, Prometheus, Grafana, SignalForge (alembic upgrade +
seed if empty), Next.js, then a readiness check that curls all five. `reset.sh`: stop,
wipe H2 + SQLite + logs, reset the config repo to v11, reseed (NovaPay org, seven
users, app bindings, policies, Recovery Collision drill), restart.

## 8. Security baseline (summary)

argon2id + JWT + rotating refresh in httpOnly cookie; RBAC per app on every route;
tenant isolation enforced in the repository layer; Fernet-encrypted connector secrets;
separate read-only, remediation and drill keys toward the reference app; HMAC on
ingest; hardcoded policy floor (high-risk never auto-executes, audit cannot be
disabled, expiry capped); redaction on evidence samples; append-only audit with hash
chain; rate limits on login and ingest; `/health` and `/metrics` on both services.

## 9. Deltas from the previous architecture document

1. Frontend is Next.js + Tailwind instead of Angular; auth cookie proxying and the
   route map above are the only new design work.
2. Databases fixed: SQLite WAL for SignalForge, H2 file for the reference app;
   Postgres remains the enterprise swap.
3. Everything else (bus, adapters, agents, policy floor, validation, drills, Recovery
   Collision) stands as decided.
