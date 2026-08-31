# Sprint 0 log (started 2026-09-01, in progress)
Goal: a bootable skeleton with frozen contracts and the traceability system live.

## Built
- [S0-T01][REQ-000] Repo layout per CLAUDE.md (signalforge/{contracts,domain,application,
  infrastructure,api,workers,seeds,tests}, web/{app,features,lib,design-system},
  reference-app/paysphere, paysphere-config/, specs/, drills/, seeds/, observability/,
  scripts/, docs/); Makefile with run/test/rtm/report/evidence/clean-check targets
  (rtm/report/evidence fail with a clear message until their generators land);
  run.sh and reset.sh skeletons; scripts/check_clean.sh app-agnostic guard.
  Verified: 5 pytest tests in signalforge/tests/test_repo_layout.py (layout, targets,
  guard passes on clean tree, guard catches a planted violation), check_clean.sh OK,
  run.sh boots in skeleton mode. REQ-000 (scaffolding/traceability) to be seeded in
  specs/requirements.yaml at S0-T03.

## Not built / deferred

## Deviations
- Environment vs CLAUDE.md spec on this machine: GNU make not installed (choco install
  blocked by a lock file needing admin rights - install with elevated
  `choco install make -y`; Makefile targets verified by running their recipe commands
  directly in bash); Python 3.11 absent, venv built on Python 3.12.8; Node 22.15.1
  instead of Node 20. No contract deviations.

## Tests
- added: 5 unit (repo layout / build interface / clean guard)
- suite result: 5 passed, 0 failed

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
