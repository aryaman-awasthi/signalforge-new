# 22 - Release package

Owner: engineering. Sprint 8. Status: complete.

**SignalForge 0.2.0** - enterprise incident command centre.
Release baseline: the Sprint 8 closure commit on `main`.

## The archive

`dist/signalforge-<shortsha>.zip`, produced by `git archive` from the closure
commit, so what ships is exactly what is committed and nothing else. Verified
against the exclusion list before release: no `.env`, no `var/`, no
`node_modules`, no `.next`, no `.venv`, no `.git`, no `target/`, no `playbook/`
and no `CLAUDE.md`. It ships `.env.example` and the reference harness's source.

Unzip it into an empty directory and start with `bash reset.sh` - which is what
found, in Sprint 8, that `reset.sh` had been guarding itself with a marker the
package deliberately excludes and so refused to run inside the product it shipped
with. It runs now.

A full `run.sh` from the archive needs `npm ci` in `web/` and a Maven build of
the reference harness, both of which need a network the finale machine may not
have. Fetch them once on a connected machine and the archive boots offline.

## What is in the release

| Component | Version | Where |
|---|---|---|
| SignalForge API + workers | 0.2.0 (`signalforge/infrastructure/config.py:VERSION`) | `signalforge/` |
| Cockpit | Next.js 14, App Router | `web/` |
| Frozen contracts | schema_version 1 | `signalforge/contracts/`, `specs/` |
| Adapter bundle | 13 kinds, built-in + simulated + enterprise interfaces | `signalforge/infrastructure/adapters/` |
| Reference harness | Java 17, H2 | `reference-app/` (demo data - deleting it leaves a working product) |
| Drills | Recovery Collision, S4 acceptance | `drills/` (data) |
| Observability provisioning | Prometheus scrape config, Grafana dashboards | `observability/` |

There is no `dist/` tarball. The release *is* the checkout: this build runs from
source on one machine by design (CLAUDE.md environment), and a packaged artefact
that had to be unpacked to be run would add a step and prove nothing. `reset.sh`
plus `run.sh` is the install.

## Install

```bash
python -m venv .venv && .venv/Scripts/pip install -e ".[dev]"   # or bin/pip on POSIX
cd web && npm install && cd ..
bash reset.sh
bash run.sh
```

Prerequisites, ports, environment variables and the Postgres path are in
`docs/09-deployment.md`. Config templates are `.env.example` (the platform) and
the reference config repository (the harness's deploy hooks).

## What shipped in this release

Sprints 0-7. The capability map is `playbook/coverage-checklist.md`; the
requirement-by-requirement position is `docs/20-RTM.md`, generated from the test
run rather than written by hand.

Headline capabilities:

- **Onboarding as a product flow.** Any application onboards through the frozen
  `/observe/*` and `/api/v1/ingest/*` contracts and a seven-screen wizard. No
  app-specific code enters the core; `scripts/check_clean.sh` enforces it on
  every commit.
- **Detection that proposes and never declares.** Rollups, baselines, sustained
  anomalies, SLO burn, storm clustering, a corroboration rule that needs two
  independent families with one customer-facing, and a severity proposal that
  carries `requires_human: true`.
- **A war room with real people in it.** Paging by blast radius, ack boards with
  per-row countdowns, escalation through the rotation, join briefs for late
  arrivals, commitments that become tasks.
- **An incident brain that shows its arithmetic.** A versioned graph with
  provenance and supersession, hypotheses whose confidence is deterministic and
  explained, contradiction detection between human claims and telemetry,
  CONFIRMED versus PREDICTED blast radius, a copilot grounded on graph nodes.
- **Governed execution.** A policy floor in code that no configuration can
  lower, per-app YAML that may only tighten it, a Tool Gateway with approvals,
  idempotency, `UNCERTAIN` on timeout, reconcile-before-retry, duplicate
  prevention, compensation and human takeover.
- **Layered validation.** Technical, service, customer and business layers read
  the application independently of the brain; the customer layer blocks closure.
- **Audit and learning.** Hash-chained events and audit log, seals, incident
  replay with a scrubber, and a post-incident review whose lessons are verified
  only when their citations resolve.
- **Drills.** A deterministic engine on a virtual clock, and Recovery Collision:
  one whole incident, 132 steps, 35 checkpoints, no model, under a minute.

## Verification for this release

| Gate | Result |
|---|---|
| Full Python suite | see `docs/21-test-report.md` (generated) |
| Security suite | green (`signalforge/tests/security/`) |
| AI evaluation suite | green (`signalforge/tests/ai_eval/`) |
| Non-functional suite | green - `docs/16-non-functional-tests.md` |
| Performance | measured - `docs/17-performance-tests.md` |
| Vulnerability | `docs/19-vulnerability-assessment.md` (npm audit executed; pip-audit blocked, and said so) |
| Recovery Collision drill | green, no model, no network |
| Twelve demonstration checks | `python scripts/demo_check.py` |
| App-agnostic guard | `bash scripts/check_clean.sh` |
| Frontend | `npm run typecheck && npm run build` |

## Upgrade and rollback

**Upgrading** is `git pull`, `pip install -e ".[dev]"`, `npm install`, then
`bash run.sh` - which applies migrations before it starts serving.

**Rolling back** is `git checkout <previous tag>` and, only if the newer release
added a migration, `alembic downgrade <previous revision>`. This release adds no
schema-destructive migration.

The event chain and the audit chain survive both directions. They are
append-only, no migration rewrites their hashes, and there is deliberately no
code path that disables audit - so a rollback cannot quietly orphan the record of
what was done during the release it is rolling back.

**Do not rotate `SIGNALFORGE_FERNET_KEY` as part of an upgrade.** Connector
credentials are encrypted with it and are unrecoverable without it.

## Known limitations in this release

Carried forward into `docs/23-user-guide.md` and the Sprint 7 log:

1. **Five named role screens are not built.** IC, SME, Comms, Executive and
   Auditor views were planned as distinct screens in Sprint 6. The permissions
   behind them are enforced end to end and every role can do its job through the
   screens that exist; what is missing is the tailored layout per role.
2. **Audit replay and the PIR have no dedicated screen.** Both are complete over
   the API and tested end to end; the cockpit shows the event-plane timeline with
   its own scrubber, but the replay and the review render as JSON.
3. **`next` and `postcss` carry high advisories** whose fix is a major
   Next upgrade. Accepted deliberately for this release, scheduled first
   afterwards. See `docs/19`.
4. **`pip-audit` did not run on the build machine** - TLS interception breaks
   certificate verification against both advisory services. No claim is made
   about the Python dependency set.
5. **Single node, SQLite.** Postgres-ready and untested on Postgres.
6. **The Drill Author agent is not built.** Drills are authored as data by hand;
   the engine that runs them is complete.
7. **The twelve demonstration checks are derived** from the guide's compliance
   map in `playbook/00-INDEX.md` rather than from the guide's own text, which is
   not in this repository. See `docs/demo-script.md`.
