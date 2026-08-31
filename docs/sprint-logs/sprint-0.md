# Sprint 0 log (started 2026-09-01, in progress)
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

## Not built / deferred

## Deviations
- Environment vs CLAUDE.md spec on this machine: GNU make not installed (choco install
  blocked by a lock file needing admin rights - install with elevated
  `choco install make -y`; Makefile targets verified by running their recipe commands
  directly in bash); Python 3.11 absent, venv built on Python 3.12.8; Node 22.15.1
  instead of Node 20. No contract deviations.

## Tests
- added: 42 unit (5 repo layout + 37 contract validation)
- suite result: 42 passed, 0 failed

## Artifacts updated
- none yet (Makefile/scripts are tooling; artifact docs start at S0-T03/T04)

## Security notes
- check_clean.sh enforces the app-agnostic core rule from day one; the guard has a
  negative test proving it fires.

## Known issues and technical debt
- make not runnable until installed with admin rights (see Deviations).

## Demoable checkpoint
- `bash run.sh` boots in skeleton mode; `bash scripts/check_clean.sh` returns OK;
  `.venv/Scripts/python.exe -m pytest -q` shows 5 passing.

## Coverage checklist ticks
- none yet

## Entry criteria for next sprint: pending (sprint 0 in progress)
