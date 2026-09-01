# Sprint 7 log (2026-09-01/02, one session)
Goal: game days as a feature, full audit replay, a review whose lessons have to be
earned, non-functional evidence, all 23 artifacts consistent, release cut.

This log doubles as the release note; `docs/22-release-package.md` is the
installable half of it.

## Built

- **[S7-T01][REQ-070] Deterministic drill engine.**
  `signalforge/domain/drills/` (scenario schema, closed world/clock/job
  vocabularies, checkpoint expressions), `signalforge/application/drills/`
  (engine + three ports), `signalforge/infrastructure/drills/` (file loader,
  stepped scenario clock, HTTP transport), `signalforge/tests/drills/`.

  Seven step kinds - `human`, `machine`, `world`, `inject`, `clock`, `platform`,
  `checkpoint`. Human and machine steps go through SignalForge's own public API
  with tokens minted by the real auth service; `world` steps touch only the
  watched application over the frozen observe contract; `platform` steps run the
  product's *own* scheduled jobs from the same composition root at moments the
  scenario chooses. A drill therefore controls **when the platform looks**, never
  what it concludes.

  *Verified:* `signalforge/tests/drills/test_drill_engine.py` - a checkpoint that
  fails names the observed value, not only the expected one; a real 3600-second
  approval TTL expires in milliseconds because scenario time moved and nothing
  slept; the world vocabulary refuses a verb that would reach into the platform;
  a scenario that moves time backwards is refused at load.

- **[S7-T01][REQ-070, REQ-018] The drill boundary, structurally.**
  `signalforge/tests/drills/test_drill_boundary.py`. The API package does not
  import the drill engine at all (asserted by walking every api module and
  reading what it pulled in); no published route in the live OpenAPI document
  names a drill, chaos or injection; the chaos adapter offers exactly one
  credential scope, `drill`, and binding it read-only or remediation is refused
  by the contract; a scenario whose scope is not `drill:<id>` is refused before
  its first step.

- **[S7-T02][REQ-071] Recovery Collision, authored purely as data.**
  `drills/recovery-collision.yaml` - 132 steps, 35 checkpoints, no product code.
  One whole incident: deployment announced over the frozen ingest path with a
  real HMAC; two independent families corroborate a customer-facing P0 and a
  person declares it; an unanswered page escalates through the rotation; three
  hypotheses ranked, one raised by a measurement and then contradicted by a
  second engineer, with the contradiction surfaced rather than averaged;
  a high-risk rollback refused auto-execution by the floor, approved by a second
  person, timed out, landed UNCERTAIN, refused a retry, reconciled to
  COMPLETED_LATE and refused to run twice; the estate recovers while the customer
  journey does not, so closure is refused; an issued customer statement is
  contradicted by later evidence and its correction needs its own approval by
  somebody other than its author; the stability window is held in scenario time;
  a person closes it; and an auditor replays the whole thing the next morning.

  *Verified:* `signalforge/tests/drills/test_recovery_collision.py` - it passes
  with `GENAILAB_API_KEY` absent, every injection carries the drill's own scope,
  and the audit chain verifies after two hundred-odd entries.

- **[S7-T03][REQ-072, REQ-016] Audit replay.**
  `signalforge/domain/audit/` (entry shapes, the topic-to-meaning table),
  `signalforge/application/audit/replay_service.py`,
  `signalforge/api/routers/audit.py`, `SqlAuditReader` in
  `signalforge/infrastructure/security/audit.py`, `AuditReader` port on
  `IdentityUnitOfWork`'s sibling `EventUnitOfWork`.

  Every line answers six questions - what, when, who, what justified it, what
  changed, what can be opened. Chain verification runs over the tenant's **whole**
  log rather than the incident's subset, because a chain verified over a filtered
  selection verifies nothing. `?until_seq=` is the scrubber and is the same code
  path with a different bound.

  *Verified:* `signalforge/tests/audit/test_audit_replay.py` - the replay carries
  actor by name, the floor rules that justified a refusal and the permission an
  approver exercised; a scrubbed view is a byte-prefix of the whole; an **edited**
  payload makes the chain report invalid and names the first broken seq; a
  **deleted** row breaks the chain at its successor; another tenant's incident
  replays as nothing rather than as a 403 that confirms it exists; an Auditor can
  read it and cannot close the incident.

- **[S7-T04][REQ-072] Post-incident review with a verified-lessons gate.**
  `signalforge/domain/pir/review.py`, `signalforge/application/pir/service.py`.
  Sixteen sections built from the replay, the incident row, the execution ledger,
  the approvals, the validation cycles, the communications and the graph. It
  **refuses to generate at all** when the chain does not verify - a banner is not
  enough for the artefact people quote six months later.

  The gate is in the domain and has no override: a `Lesson` cannot be constructed
  VERIFIED without citations, and `verify` promotes one only when every citation
  resolves to a record *this incident* produced. Anything else stays PROPOSED
  carrying the refs that did not resolve.

  *Verified:* `signalforge/tests/audit/test_pir.py` - ten tests, including four
  pure-domain ones on the gate itself. Two generations of one review are
  byte-identical, because a PIR is derived rather than stored.

- **[S7-T05][REQ-073] Non-functional evidence, generated from a run.**
  `signalforge/tests/nfr/`, `scripts/gen_nfr_report.py`, `make nfr`.
  Artifacts 16 and 17 are written from `var/nfr/` and refuse to generate without
  measurements behind them.

  The resilience suite walks the whole designed-failure catalogue in one place:
  no model configured, adapter timeout to UNCERTAIN with the retry refused,
  duplicate submission recognised, an approver who loses the permission between
  approving and running, a healthy estate with a failing customer journey
  refusing to close, and one dead connector that does not take the cockpit with
  it. Websocket fallback and crash recovery are proven in their own suites and
  referenced rather than duplicated.

  The benchmark times ten cockpit reads on an incident that has a graph, a ledger
  with an approval, a validation cycle and a communication: thirty samples each,
  median and p95 recorded with the machine beside them, a ceiling that fails a
  test rather than a target that flatters one, and an explicit statement that it
  is not a load test.

- **[S7-T05][REQ-073] Vulnerability assessment.** `docs/19`. `npm audit`
  executed: 4 high, all triaged, none in this repository's own code.
  `pip-audit` installed but **blocked** - TLS interception on this machine breaks
  certificate verification against both PyPI and OSV - so the document records
  the failure and the command to run elsewhere, and makes no claim about the
  Python dependency set.

- **[S7-T06][REQ-073] Release: reset, run, evidence, demo.**
  - `reset.sh` refuses to run outside a SignalForge checkout, deletes only what a
    run wrote, and prints what it touched and what it did not.
  - `run.sh` waits until each component answers a real request before reporting
    it up, exits non-zero with a log path when something essential fails, and
    prints every URL plus whether a model gateway is configured.
  - `scripts/evidence.py` + `make evidence REQ=...` - the guide's live
    traceability challenge. Resolves a requirement to its text, the source that
    names it, its REQ-tagged tests and their last outcome, the topics that
    implementation defines, and **live events from the running platform** for
    those topics. Nothing is hand-mapped.
  - `scripts/demo_check.py` - the twelve final-demonstration checks with their
    guide section, action, surface, expected result, requirements, backing tests
    and operator fallback, runnable as one command.
  - `scripts/demo.sh` - the live half, driving a running install to the moment
    before a declaration.

- **[S7-T06] Artifacts.** 09 (deployment), 16 (non-functional), 17
  (performance), 19 (vulnerability), 22 (release package), 23 (user guide) and
  `demo-script.md` written; 20 (RTM) and 21 (test report) regenerated from the
  final run.

## Not built / deferred

- **Drill Author agent** (planned in S7-T01: an agent drafting a timeline from
  manifests and topology for human review). *Reason:* the sprint's release-
  critical path is a drill that runs deterministically, and an agent that drafts
  scenarios adds authoring convenience rather than evidence. *Impact:* drills are
  written as data by hand; `drills/recovery-collision.yaml` shows the shape.
  *Plan:* post-release; the scenario schema is already the contract it would
  target.

- **A dedicated screen for audit replay and the PIR.** Both are complete over the
  API and covered end to end by tests, and the cockpit already has an
  event-plane timeline with a seq scrubber. *Impact:* the replay entries and the
  review render as JSON rather than as a page. *Plan:* post-release, alongside
  the five role screens below.

- **Five named role screens** (IC, SME, Comms, Executive, Auditor) - carried from
  Sprint 6 and still not built. Permissions are enforced end to end and every
  role can do its job through the screens that exist; the tailored per-role
  layouts are what is missing. This is the only line still unticked in
  `playbook/coverage-checklist.md`.

- **`pip-audit`** - see above. Environment limitation, recorded rather than
  worked around.

- **The `next` major upgrade** that would clear two high advisories. Deliberately
  deferred: rewriting the App Router surface in the final sprint is a larger risk
  to this release than the finding. Scheduled first afterwards.

## Deviations

- **No contract or spec was changed.** `signalforge/contracts/` and `specs/` are
  untouched apart from four `status:` fields moving `planned -> built`
  (REQ-070..073), which the catalogue's own header permits.
- **`dist/` was not produced.** Sprint 7's task list names "release package
  (dist/ with config templates, versions, rollback notes)". This build runs from
  source on one machine by design, so the release *is* the checkout and
  `reset.sh` + `run.sh` is the install; the config templates, versions and
  rollback notes the task asks for are all in `docs/22` and `docs/09`. Recorded
  as a deviation rather than quietly dropped.
- **The twelve demonstration checks are derived**, not transcribed. The guide's
  section 16 text is not in this repository; the list is built from the guide's
  compliance map in `playbook/00-INDEX.md` plus the two checks the repository
  names explicitly (live traceability; a control actively blocking an unsafe
  action, which `docs/08` records as the twelfth). `docs/demo-script.md` says so
  in its own words, and `scripts/demo_check.py` is the single place to change the
  mapping if the guide's list differs.

## Tests

- **Added this sprint:** 4 drill-boundary, 6 drill-engine, 3 Recovery Collision,
  6 audit replay, 10 PIR (4 of them pure domain), 8 non-functional including the
  benchmark. 37 tests, all integration or boundary - no shallow additions.
- **Suite result: 1255 passed, 5 skipped, 0 failed** (21m20s). Generated into
  `docs/21-test-report.md` and `docs/20-RTM.md`: 52 requirements, every one with
  linked tests, none without. The Sprint 7 requirements verify on 11 (REQ-070),
  3 (REQ-071), 16 (REQ-072) and 8 (REQ-073) executed tests.
- **Gates, run once at the end:**

  | Gate | Result |
  |---|---|
  | Full Python suite | 1255 passed, 5 skipped, 0 failed |
  | Frontend typecheck (`tsc --noEmit`) | clean |
  | Frontend build (`next build`) | clean |
  | `scripts/check_clean.sh` | OK |
  | Recovery Collision + drill suite | 13 passed |
  | `reset.sh` then `run.sh` from empty | harness, API and cockpit all answering |
  | `scripts/demo.sh` against the live harness | corroborated P0, `declared: false` |
  | `python scripts/demo_check.py` | **12/12** |

  The first full pass was **not** green: four tests in `test_api_spec_docs.py`
  caught the new `audit` API group having no description and the OpenAPI export
  being stale, which is exactly what that suite is for. Fixed, regenerated, and
  the suite re-run in full rather than partially.

## Artifacts updated

| # | Path | State |
|---|---|---|
| 09 | `docs/09-deployment.md` | written this sprint |
| 16 | `docs/16-non-functional-tests.md` | generated by `make nfr` |
| 17 | `docs/17-performance-tests.md` | generated by `make nfr` |
| 19 | `docs/19-vulnerability-assessment.md` | written this sprint |
| 20 | `docs/20-RTM.md` | regenerated by `make rtm` |
| 21 | `docs/21-test-report.md` | regenerated by `make report` |
| 22 | `docs/22-release-package.md` | written this sprint |
| 23 | `docs/23-user-guide.md` | written this sprint |
| 16* | `docs/demo-script.md` | written this sprint (guide section 16) |
| 05 | `specs/requirements.yaml` | REQ-070..073 status `built` |

## Security notes

- **New surfaces:** two, both read-only and both behind `read_audit` -
  `GET /incidents/{id}/replay` and `GET|POST /incidents/{id}/pir`. The tenant
  comes from the principal and appears in no parameter, so a cross-tenant replay
  is not a check that could be got wrong. Covered by
  `test_another_tenants_incident_replays_as_nothing` and
  `test_another_tenant_cannot_generate_a_pir_for_this_incident`.
- **No new write surface at all.** The drill engine has no route; it is
  structurally unreachable from the API package.
- **Audit remains undisableable** and is now also *checkable*: tampering is
  detected, reported and blocks PIR generation.
- **Threat model:** no new rows. The two new routes sit inside the existing
  "auditor reads the sealed record" boundary; the drill engine sits outside the
  API trust boundary entirely, which is a reduction in surface rather than an
  addition.

## Known issues and technical debt

1. Five named role screens (above).
2. No screen for audit replay or the PIR (above).
3. `next` / `postcss` advisories, deferred deliberately (`docs/19`).
4. `pip-audit` unrunnable on this machine; no claim made about Python
   dependencies.
5. Single node, SQLite. Postgres-ready, Postgres-untested.
6. Performance numbers are in-process and single-threaded, and say so.
7. No Drill Author agent.
8. The twelve checks are derived and want confirming against the guide.

## Demoable checkpoint

```bash
bash reset.sh && bash run.sh              # clean machine to a running cockpit
bash scripts/demo.sh                      # live: onboarded, traffic, fault injected
python -m pytest signalforge/tests/drills # deterministic: the whole incident, no model
python scripts/demo_check.py              # the twelve checks, PASS/FAIL
make evidence REQ=REQ-062                 # a requirement, its tests, its live events
```

The judgeable milestone is `scripts/demo_check.py` printing twelve passes, and
`docs/demo-script.md` performing the same twelve in a browser.

## Coverage checklist ticks

Everything under "Drills, audit, learning" and "Engineering-guide deliverables
and quality" turned true this sprint, with two qualifications written into the
lines themselves (the Drill Author agent, and `pip-audit`). The Sprint 4
detection and war-room lines were also ticked: they were true at Sprint 4's exit
and had never been marked.

**One line remains unticked** - the five named role screens - and it is carried
into `docs/22` and `docs/23` as a known limitation, which is what the checklist's
own preamble requires.

## Entry criteria for next sprint

Not applicable: Sprint 7 is the final sprint. The release baseline is the
closure commit on `main`; `docs/22-release-package.md` is the release note and
`docs/23-user-guide.md` is what a user reads first.
