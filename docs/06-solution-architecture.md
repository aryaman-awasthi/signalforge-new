# 06 - Solution architecture (skeleton, Sprint 0)

Owner: engineering. Status: SKELETON - the authored architecture document
(signalforge-architecture-final.md) was not present in the workspace at S0-T04;
this skeleton records what the codebase already fixes and must be replaced by the
import when the file is provided (tracked in the sprint 0 log under "Not built").

## Fixed by contracts and CLAUDE.md (authoritative today)

### Layering (hexagonal, dependencies point inward)
api -> application -> domain; infrastructure implements domain ports. Domain never
imports FastAPI, SQLAlchemy, Next.js or vendor SDKs. Web mirrors it: thin routes,
logic in features/, one WebSocket store, tokens from design-system/.

### Public contracts (signalforge/contracts/, frozen)
- Event envelope: identity, dual time (occurred_at may follow a drill clock;
  recorded_at is wall), actor, source authority/freshness/simulated, confidence,
  correlation/causation/supersedes, idempotency, redaction, hash-chain fields,
  signal/incident planes, partition key incident->app->tenant.
- Adapter SPI: 13 kinds with canonical models; pure normalize() for push payloads;
  reconcile-before-retry primitive on actions; drill scope required for chaos.
- Manifests: action + chaos JSON schemas (high risk requires approvers; reversible
  requires a reverse action; chaos duration bounded).
- HTTP surfaces: /observe/* (what an app exposes) and /api/v1/ingest/* (push with
  bearer + HMAC + replay window + idempotent event_id, 202 async).
- Roles matrix: 7 roles x 11 permissions with separation of duties.

### Runtime shape (single machine, no Docker)
FastAPI api + workers over SQLite WAL (SQLAlchemy 2 async, Postgres-ready);
transactional outbox event spine (S2); Next.js 14 web; local Prometheus/Grafana
binaries; one llm.provider adapter to the configured organizer endpoint with cache,
budgets and deterministic degraded mode.

## To be filled by the imported document
- Component and deployment diagrams; data flow diagrams with trust boundaries.
- Sizing, retention and performance envelopes.
- Rationale narratives and rejected alternatives (cross-linked to decision-record.md).
