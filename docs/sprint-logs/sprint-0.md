# Sprint 0 log (2026-09-01, one day)
Goal: a bootable skeleton with frozen contracts and the traceability system live.

## Built
- [S0-T01][REQ-000] Repo layout per CLAUDE.md (signalforge/{contracts,domain,application,
  infrastructure,api,workers,seeds,tests}, web/{app,features,lib,design-system},
  reference-app/ + its config dir, specs/, drills/, seeds/, observability/,
  scripts/, docs/); Makefile with run/test/rtm/report/evidence/clean-check targets
  (rtm/report/evidence fail with a clear message until their generators land);
  run.sh and reset.sh skeletons; scripts/check_clean.sh app-agnostic guard.
  Verified: 5 pytest tests in signalforge/tests/test_repo_layout.py (layout, targets,
  guard passes on clean tree, guard catches a planted violation), check_clean.sh OK,
  run.sh boots in skeleton mode. REQ-000 (scaffolding/traceability) to be seeded in
  specs/requirements.yaml at S0-T03.
- [S0-T02][REQ-001..005] Frozen contracts in signalforge/contracts/: envelope.py (event
  envelope with ids, occurred_at vs recorded_at, actor, source authority/freshness/simulated,
  confidence, correlation/causation, supersedes, idempotency_key, redaction, hash-chain
  fields, planes, partition_key, canonical payload hashing, well-known topics); canonical.py
  (canonical models for all 13 kinds incl. ExecutionState UNCERTAIN + ReconcileOutcome
  COMPLETED_LATE/NOT_APPLIED/PARTIAL, high-risk action classes); spi.py (13 abstract adapter
  interfaces, reconcile-before-retry primitive on actions, drill scope required on chaos
  inject); errors.py adapter error taxonomy; schemas/action-manifest.schema.json +
  chaos-manifest.schema.json (high risk requires approvers, reversible requires reverse
  action, bounded chaos duration) with manifests.py validators; observe.py /observe/* paths
  + shapes; ingest.py /api/v1/ingest/* batch shapes, HMAC headers, replay window, 202 ack;
  roles.py 7-role x 11-permission matrix with separation-of-duties invariants.
  Verified: 37 REQ-tagged contract tests (REQ-001 envelope, REQ-002 SPI, REQ-003 manifests,
  REQ-004 http shapes, REQ-005 roles); suite 42 passed; check_clean.sh green.
- [S0-T03][REQ-006] Traceability live: specs/requirements.yaml with 51 REQ entries spanning
  all 9 coverage-checklist areas and all 8 sprints (51 rather than ~40 - granularity chosen
  so each sprint's tests tag precisely; ids stable, never renumbered); specs/controls.yaml
  with the five control categories (allowed, human_approved, prohibited, escalation,
  auditability), every prohibited entry flagged floor: true; pytest plumbing in
  signalforge/tests/conftest.py recording test-to-REQ mapping + outcomes to
  var/rtm/test-results.json on every run; scripts/gen_rtm.py joins specs + results into
  docs/20-RTM.md (make rtm), renders the full chain with zero executed tests, lists
  untagged tests, exits 2 on unknown REQ ids. Verified: 6 REQ-006 tests incl. RTM
  generation with and without results; suite 48 passed; RTM committed.

- [S0-T04] Docs: 01-BRD v1 (problem, goals, personas, success measures, risks);
  02-future-state journey narrative naming screens and controls; 03-stories generated
  from specs/requirements.yaml by scripts/gen_stories.py (51 stories, GENERATED header);
  06-solution-architecture skeleton recording what contracts already fix; decision-record.md
  seeded with DR-001..008 (real S0 decisions); stubs with owner + due sprint for
  04, 07, 08, 09, 10, 11, 12, 13, 15, 19, 21, 22, 23 and demo-script.
- [S0-T05][REQ-007] Web scaffold + minimal API: Next.js 14 + Tailwind under web/ with
  design-system/tokens.css as the single theming surface (Tailwind palette resolves to
  tokens; light/dark; no hex in config - tested), authless shell layout, /health page as a
  thin route over features/health/{components,hooks,api}, typed API client in lib/api/client.ts
  with ApiError + unreachable handling, single WebSocket store stub in lib/ws/store.ts;
  signalforge/api/main.py (FastAPI, CORS for dev web) + routers/health.py GET /health.
  Verified: 5 REQ-007 tests (endpoint, CORS, structure, tokens, thin-route rules);
  suite 53 passed; run.sh booted api (:8000, 200) + web (:3000, /health page renders
  the panel) simultaneously, then shut down cleanly.
- [S0-T04, completed post-sprint 2026-09-01] Doc imports, deferred at task time
  because the source files were missing: docs/06 replaced by the authored architecture
  from playbook/signalforge-architecture-final.md (import notes record deltas vs the
  frozen contracts and the cockpit port 3001), and the memory graph N1-N13 from
  playbook/signalforge-memory-graph.md merged into docs/decision-record.md as the
  pre-build section. One departure from "verbatim" recorded in Deviations (DR-009:
  reference-app name genericized to keep check_clean green).

## Not built / deferred
- none. (The S0-T04 doc imports were deferred while the source files were missing;
  the human provided them 2026-09-01 and the import is complete - see the last Built
  entry and Deviations.)

## Deviations
- Environment vs CLAUDE.md spec on this machine: GNU make not installed (choco install
  blocked by a lock file needing admin rights - install with elevated
  `choco install make -y`; Makefile targets verified by running their recipe commands
  directly in bash); Python 3.11 absent, venv built on Python 3.12.8; Node 22.15.1
  instead of Node 20. No contract deviations.
- Import not strictly verbatim: the authored architecture and memory graph name the
  reference app throughout; CLAUDE.md rule 2 (enforced by check_clean.sh) forbids that
  string in docs/. Imported with the name genericized (DR-009); originals kept in
  playbook/. Authorizing instruction: the human provided the files for import on
  2026-09-01 ("i have added these files in playbook").

## Tests
- added: 53 unit (5 repo layout + 37 contract validation + 6 traceability + 5 web/api scaffold)
- suite result: 53 passed, 0 failed (docs/21-test-report.md generation lands with make report)

## Artifacts updated
- 01 BRD v1, 02 future-state, 03 stories (generated), 06 solution architecture (skeleton)
- 05 SPECS/DSL: specs/requirements.yaml (51 REQs), specs/controls.yaml
- 13 Controls matrix (seed): specs/controls.yaml
- 20 RTM: docs/20-RTM.md (generated; 53 tests recorded, 8 REQs test-linked)
- stubs with owners: 04, 07, 08, 09, 10, 11, 12, 15, 19, 21, 22, 23, demo-script

## Security notes
- check_clean.sh enforces the app-agnostic core rule from day one; the guard has a
  negative test proving it fires.

## Known issues and technical debt
- make not runnable until installed with admin rights (see Deviations).
- Web dev server runs on 3000; the imported architecture assigns the cockpit 3001
  (Grafana takes 3000). Move the web port + API CORS origins when Grafana is
  provisioned (observability sprint work).

## Demoable checkpoint
1. `bash run.sh` - boots api on :8000 and web on :3000.
2. Open http://localhost:3000/health - the panel shows status ok, service version and
   latency from GET /health via the typed client; stop the api and Refresh to see the
   designed failure state ("API unreachable").
3. `.venv/Scripts/python.exe -m pytest -q` - 53 passed, all REQ-tagged.
4. `.venv/Scripts/python.exe scripts/gen_rtm.py` (= make rtm) - regenerates docs/20-RTM.md
   with the full 51-REQ chain joined to recorded results.
5. `bash scripts/check_clean.sh` - proves the core is app-agnostic.

## Coverage checklist ticks
- none - the checklist's first items are Sprint 1 capabilities; Sprint 0's acceptance
  (contract tests green, run.sh boots api + web, RTM with full REQ chain) is met.

## Entry criteria for next sprint: met
- Sprint 0 acceptance satisfied: contract validation green (37 tests), run.sh boots
  api + web (verified live), make rtm produces the full REQ chain, artifacts
  01/02/03/05/06/20 present (06 as skeleton pending the human-provided import).
