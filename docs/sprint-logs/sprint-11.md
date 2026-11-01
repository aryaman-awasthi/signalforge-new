# Sprint 11 log (2026-10-28)
Goal: find and fix the defects the sprint-10 demo build left behind, with the test suite
back as the thing that proves each one.

The sprint opened by restoring the test suite (removed before the sprint-10 demo work) and
reading five subsystems for defects: governance and war room, incidents and detection, API
security and persistence, the brain and its agents, and the cockpit. Every finding below was
confirmed against the code before it was fixed, and every fix carries a test that failed
first. The findings the review surfaced but which encode decisions sprint 10 took on purpose,
or features it deferred, are listed at the end rather than "fixed".

## Built

- **[S11-T01][REQ-006] The test suite is back and reads the code as it is today.** 1219 of
  1241 tests passed unchanged; the rest encoded sprint-10 decisions (the Claude API host on
  the LLM allowlist, `GET /apps` as a binding-scoped read, the wizard guarded by suffix) and
  were updated. Seven remaining failures each named a defect fixed below.

- **[S11-T02][REQ-005] The route catalogue finds the one route spelled with a path
  converter.** `GET /incidents/{id}/brain/nodes/{derived_key:path}` was skipped by the
  OpenAPI export, so the published document under-reported the surface.

- **[S11-T03][REQ-023] The cookie proxy forwards repeated query parameters.** `set` kept
  only the last `topics` value; multi-topic timeline filters passed one topic.

- **[S11-T04][REQ-012, REQ-013] A role bound to one application stays bound to it.**
  `require()` read `?app_id=` from the caller and let a binding scoped to *that* application
  satisfy a check whose handler acted on something else: an `org_owner` scoped to one
  application could write itself an org-wide binding; `manage_apps` on one application could
  rotate another's ingest key. Routes whose resource belongs to the tenant are decided
  org-wide; a per-application binding counts only where the handler acts on that `app_id`.

- **[S11-T05][REQ-061, REQ-062, REQ-063] An approved action runs with the arguments it was
  approved for.** `run`, retry and compensate sent the adapter `{}`; a settled attempt was
  never a duplicate, so a double-click ran a fast action twice; a lapsed approval stranded
  its execution as APPROVED and made every new request its duplicate; an approver's role
  bound to another application counted; compensation moved the source to COMPENSATING and
  never on. All five repaired in the gateway, with the REQ-061 double-submit test and the
  recovery-collision drill passing again.

- **[S11-T06][REQ-000] Hygiene.** A broken Windows virtualenv (3,760 files), three `.pya`
  editor backups and the TypeScript build cache had been committed. Removed and ignored.

- **[S11-T07][REQ-012, REQ-013, REQ-033, REQ-045] A task, a page and a notification are
  reached only through their own incident and by their own person.** The task and page
  routes decided the permission against the incident in the URL and then acted on whichever
  id followed it, anywhere in the tenant; the socket's `ack` frame acknowledged any
  notification in the tenant, so one cockpit could silence another person's page and the
  escalation behind it; and the war room's topology loader read an attribute the stored
  version does not have, so every room opened with no downstream services.

- **[S11-T08][REQ-038, REQ-046] Two tests read sprint 10's decisions.** The observe contract
  names `GET /observe/chaos/injections` (S10-T20); the single-region detection test expects
  the success-rate matrix (S10-T06).

- **[S11-T09][REQ-043, REQ-040] The ticket sweep writes the ticket link and nothing else.**
  It wrote the whole stale incident row back, reopening an incident closed while the ITSM
  call was in flight.

- **[S11-T10][REQ-042] A storm names the node its members depend on, and reads one row per
  alert.** Root by in-degree rather than undirected degree; one alert is its latest timeline
  row, so a resolved alert leaves the storm; a producer's naive timestamp no longer fails the
  evaluation of every signal.

- **[S11-T11][REQ-040] `?state=bogus` is a 422, not a 409.**

- **[S11-T12][REQ-032, REQ-064] A corrected statement is corrected once; the correction can
  be.** A second correction of the same text drafted and could never issue.

- **[S11-T13][REQ-063] A plan is ordered under the same confidence threshold as the
  recommendation beside it.**

- **[S11-T14][REQ-010, REQ-012, REQ-017] A role named twice on an invite is one role
  (was a 500 for the invitee); a signature that is not a hex digest is refused rather than
  crashing `compare_digest` (was a 500 from attacker input).**

- **[S11-T15][REQ-036, REQ-045] A time typed without its zone is refused where it is
  typed.** A naive task `due_at` made the task board a 500 on every read; a naive topology
  `captured_at` made the application list unreadable long after the upload answered 200.

- **[S11-T16][REQ-053] A measurement of the suspected component raises its hypothesis.**
  The sprint-5 acceptance walk failed: naming the pool on the bridge sharpened the saturation
  slot's subject and the linker then dropped the topology evidence the slot had held, so the
  confidence fell on the strongest evidence of the incident.

- **[S11-T17][REQ-010, REQ-021, REQ-033, REQ-040, REQ-061, REQ-062] Nine cockpit defects.**
  A lapsed POST to the proxy was redirected into a GET (405); the wizard guard matched
  `/onboard` as a substring; the WebSocket store opened one socket per racing subscriber;
  the sign-in form honoured `//host` and `/\host` as a destination; the timeline window never
  attached its scroll listener; a 422 rendered as `[object Object]`; cancelling the take-over
  prompt recorded a take-over; the timeline read a `kind` the API never sent; the approval
  countdown ran off the laptop's clock.

- **[S11-T19][REQ-000, REQ-006] The documented install works on a fresh machine.**
  `pip install -e ".[dev]"` refused the checkout: pyproject.toml named no build backend, so
  setuptools saw `web/`, `specs/`, `drills/` and `var/` beside `signalforge/` and stopped;
  fastapi, uvicorn and websockets were never declared. `make nfr` was advertised with no
  recipe. `scripts/demo.sh` dropped every argument after the drill path, so
  `--scenario license-expiry` only worked through the PowerShell twin.

- **[S11-T20][REQ-006] A README and a demo script.** The repository had no root README.
  `README.md` says what the platform does, how to install and run it, where the model is
  used and where it stops, and what it does not do yet. `docs/demo-script-ai-lead.md` is a
  thirty-minute run sheet for an AI leader, with the exact words on screen, the questions
  such an audience asks, and the claims a presenter must not make. Both were read against
  the code by six independent reviewers and corrected until every claim pointed at a line.
  Both launchers now start the cockpit with `NEXT_TELEMETRY_DISABLED=1`, so the dev server
  phones nobody either.

## Tests

1,317 tests (8 skipped); the full suite passes on Python 3.13 in a clean Linux container with no model
configured. `make clean-check` passes. The web app typechecks (`tsc --noEmit`).

## Noted, not fixed

Findings the review raised that are design decisions or deferred work rather than defects,
left for the backlog: metric-only evidence grades P3 because `journey_tier` comes only from
probes (sprint-10 T13); the `series_basis` heuristic; a declare race between two commanders;
cancelling after an uncertain attempt; the login audit volume; the drill clock for sessions;
a socket outliving its logout; lost-update on ingest-key counters; the audit replay link
shown without `read_audit`; swallowed errors in org setup; UTC times unlabelled on screen.

## Entry criteria for next sprint

`docs/20-RTM.md` and `docs/21-test-report.md` should be regenerated from a recorded run
(`make rtm`, `make report`) before the next release package.
