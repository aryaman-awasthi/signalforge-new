# 09 - Deployment

Owner: engineering. Sprint 7. Status: complete.

This build targets **one machine, no Docker** (CLAUDE.md environment). That is a
deliberate constraint of the exercise, not an architectural limit: the schema is
Postgres-ready and every process boundary below is a real one.

## 1. What runs

| Process | Command | Port | Required for |
|---|---|---|---|
| API + worker | `python -m uvicorn signalforge.api.main:app` | 8000 | everything |
| Cockpit | `npm run dev` (or `npm run build && npm start`) in `web/` | 3000 | the browser |
| Reference harness | `bash reference-app/*/run.sh` | 8080 | the demo only |
| Prometheus | `observability/bin/prometheus` | 9090 | optional dashboards |
| Grafana | `observability/bin/grafana` | 3001 | optional dashboards |

The worker group (dispatcher, rollup poller, detection evaluator, probe runner,
escalation sweep, bridge harvest, brain pass, approval expiry, uncertain-execution
watch) runs **inside the API process**, started by its lifespan and disabled by
`SIGNALFORGE_WORKER_ENABLED=0` on nodes that should serve requests only. One
node runs it; the offsets in the event store make that safe to move.

The reference harness is demo data (CLAUDE.md rule 7). Deleting `reference-app/`
leaves a working product and a `run.sh` that says so.

## 2. Prerequisites

- Python 3.11+ with a virtualenv at `.venv` (this build runs 3.12).
- Node 20 and `npm install` in `web/`.
- Java 17 and Maven, **only** to build the reference harness. Without them
  `run.sh` skips it and continues.
- No Docker. No network on the critical path.

## 3. Boot

```bash
bash reset.sh     # deterministic baseline
bash run.sh       # migrate, start, wait for readiness, print URLs
```

`run.sh` waits until each component answers a real request before reporting it
up, and exits non-zero with a log path if something essential does not start.
Logs land in `var/run/`.

## 4. Configuration

Everything is environment, and nothing is hardcoded. `.env.example` is the
complete list; copy it to `.env` and fill it in. `run.sh`, `reset.sh`,
`scripts/demo.sh` and `make llm-smoke` each source `$ROOT/.env` before they do
anything, so the file the operator writes is the environment the platform reads.
A value in `.env` wins over one already exported in the shell; comment it out
rather than exporting around it. Nothing else loads the file — a process started
by hand (`uvicorn ...`) sees only what its own shell exports. These are the
variables that matter for a deployment:

| Variable | Default | Notes |
|---|---|---|
| `SIGNALFORGE_DB_URL` | `sqlite+aiosqlite:///var/signalforge.db` | Postgres-ready DSN |
| `SIGNALFORGE_ROLLUP_DB_URL` | `sqlite+aiosqlite:///var/rollups.db` | a *second* database: derived, prunable |
| `SIGNALFORGE_JWT_SECRET` | generated into `var/keys/jwt.secret` | set explicitly in a real deployment |
| `SIGNALFORGE_FERNET_KEY` | generated into `var/keys/fernet.key` | **rotating this makes stored credentials unreadable** |
| `SIGNALFORGE_PORT` / `SIGNALFORGE_WEB_PORT` | 8000 / 3000 | |
| `SIGNALFORGE_WORKER_ENABLED` | `1` | set to `0` on request-only nodes |
| `SIGNALFORGE_ADAPTERS_DIR` | `signalforge/infrastructure/adapters` | where adapter manifests are mounted |
| `GENAILAB_BASE_URL` | `https://genailab.tcs.in` | the only outbound AI destination |
| `GENAILAB_API_KEY` | unset | **absent is a supported mode** - deterministic, badged |
| `LLM_MODEL_GENERAL` / `_REASONING` / `_EMBEDDINGS` | gpt-4.1 / sonnet-4.6 / text-embedding-3-large | swappable by config to any approved model that accepts `temperature=0` |
| `LLM_TIMEOUT_S`, `LLM_CACHE` | 20, true | |
| `LLM_CA_BUNDLE` | unset | a PEM to trust *in addition* to the system roots, for a network that intercepts TLS. There is no variable that turns verification off. |

SignalForge asks every model for a deterministic, schema-constrained answer
(`temperature=0`), because an incident record that paraphrases itself between two
reads of the same evidence is not evidence. A model id that refuses
`temperature=0` therefore degrades every AI surface rather than merely varying
it. `make llm-smoke` is the one command that proves the path end to end — TLS,
credential, catalogue membership, a real chat call and a real embedding — and it
names the model and the reason when it fails.

Two keys are generated on first boot and persisted under `var/keys/` so that a
restart does not invalidate every session. `reset.sh` deletes them, which is
correct for a demo baseline and would be wrong in production - set both
explicitly there.

## 5. Data

| Path | Holds | Safe to delete |
|---|---|---|
| `var/signalforge.db` | tenants, incidents, graph, ledger, events, audit chain, LLM response cache | only with the incident record |
| `var/rollups.db` | metric rollups | yes - derived, and pruned on a schedule |
| `var/keys/` | JWT secret, Fernet key | **no** - see above |
| `var/rtm/`, `var/nfr/`, `var/run/` | test recording, measurements, logs | yes |
| `reference-app/*/data` | the harness's H2 database | yes |

Migrations are Alembic (`alembic upgrade head`), applied by `run.sh`. The audit
chain and the event chain are append-only; there is no migration that rewrites
them, and there is deliberately no code path that disables audit.

## 6. Moving to Postgres

1. Point `SIGNALFORGE_DB_URL` at `postgresql+asyncpg://...` and install `asyncpg`.
2. `alembic upgrade head`.
3. Leave `SIGNALFORGE_ROLLUP_DB_URL` on its own database - it is pruned
   independently and does not belong in the same lifetime as the incident record.

No application code changes: the repositories are SQLAlchemy 2 async throughout
and no raw SQLite SQL exists outside the test helpers.

## 7. Observability

- `/health` - liveness plus per-source detail (database, bus, worker, connectors).
- `/metrics` - Prometheus exposition. Every label is a closed vocabulary from an
  enum; no incident id, tenant id or action argument appears in any series.
- Structured JSON logs on stdout, one line per request with a request id.
- `observability/` carries the Prometheus scrape config and the Grafana
  provisioning; the binaries are local under `observability/bin`.

The one metric worth alerting on is
`signalforge_policy_floor_rejections_total`: a non-zero value is a control
actively preventing an unsafe configuration.

## 8. Security posture at deployment time

- Bind to `127.0.0.1` unless a reverse proxy terminates TLS in front.
- Set `SIGNALFORGE_JWT_SECRET` and `SIGNALFORGE_FERNET_KEY` explicitly and back
  the Fernet key up; connector credentials are encrypted with it and are
  unrecoverable without it.
- Ingest keys are per (app, stream) and carry their own HMAC secret; rotate them
  through `/ingest-keys` rather than by editing the database.
- The only outbound destination on the AI path is `GENAILAB_BASE_URL`. There is
  no OpenAI endpoint and no second provider anywhere in the tree.

## 9. Rollback

This build has no schema-destructive migrations. To roll back a release:

```bash
git checkout <previous tag>
alembic downgrade <previous revision>   # only if the newer release added one
bash run.sh
```

The event and audit chains survive both directions: they are append-only and no
migration rewrites their hashes. See `docs/22-release-package.md` for the
per-release notes.

## 10. Known operational limitations

- Single node. Horizontal scale would need the worker group pinned to one node
  (the switch exists) and a shared Postgres.
- SQLite WAL is fine for a demo estate and one machine; it is not a
  multi-writer story.
- Prometheus and Grafana are optional local binaries, not a managed stack.
