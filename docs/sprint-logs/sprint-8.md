# Sprint 8 log (2026-09-02, one session)
Goal: the cockpit looks and flows like the design, every screen is filled by live
data from any onboarded application, and a clean package exists.

Sprint 7 shipped a complete product with an invented palette. The design export
arrived after the cockpit was built - `web/design-system/tokens.css` said so in
its own header - and the demo drill was the smallest world in which the
corroboration rule could be proved rather than the smallest world in which the
screens are not empty. This sprint fixed both, and fixed the seven defects that
became visible once the screens were honest enough to show them - including two
that only the final walkthrough could find: the primary action button had never
worked, and the audit chain could fork under the platform's own load.

## Built

- **[S8-T01][REQ-051] The model path actually loads.**
  `run.sh`, `reset.sh`, `scripts/demo.sh` and the `llm-smoke` target each load
  `$ROOT/.env` before doing anything.

  Nothing had been reading it. `config.py` reads `os.environ` and no launcher
  ever sourced the file, so a machine with a perfectly good gateway key ran every
  AI surface in degraded mode and said so honestly on screen. Two further reasons
  the gateway could not answer, both configuration and neither a code change:
  this machine intercepts TLS, so the chain terminates in a corporate root
  `certifi` does not carry (solved through the `LLM_CA_BUNDLE` knob the product
  already had - verification stays on, and there is still no variable that turns
  it off); and the configured model ids were gpt-5.x, which on this gateway
  reject `temperature=0` outright. SignalForge asks for deterministic,
  schema-constrained answers on purpose, so those ids degrade every AI surface
  permanently rather than merely varying it.

  *Verified:* `make llm-smoke` - OK, chat and embeddings both served by the
  approved gateway, every configured model id in its catalogue.

- **[S8-T02][REQ-036, REQ-038, REQ-071] A demonstration world with no empty
  panels.** `drills/demo-setup.yaml`, now the default for `scripts/demo.sh`;
  `scripts/drill_run.py` extended.

  The list was built backwards from the screens: for each panel on S5 and S9,
  which route does it call and what does that route need before it answers with
  something rather than nothing. Fifteen connectors, all six golden signals on
  six different series the application really publishes, four journeys with a
  probe per region, the topology pulled through `topo-import`, two SLOs, one
  person per role with a rotation and a three-step escalation policy, a signed
  deployment, alerts that name topology nodes, and two injections.

  The runner learned per-connector config, push connectors whose HMAC secret it
  asks the platform for rather than inventing, action import with the floor
  re-asserted, topology capture, people by role, alerts, `--join` for a second
  application and `--refault` for a walkthrough that outlives a scenario's
  declared maximum duration.

  *Verified:* in the browser after `bash scripts/demo.sh` - six golden tiles with
  values, two regions, 100 of 100 weight covered, four journeys, the deployment
  in Recent Changes; and after a person presses Declare, an ITSM ticket, the
  change window, the bridge transcript playing, all five paged roles with their
  channels, and a blast radius with CONFIRMED nodes named by alerts and PREDICTED
  nodes hanging off them. `python scripts/demo_check.py` 12/12.

- **[S8-T02] Four defects the honest screens exposed**, each with a test.
  1. A signal kept its bootstrap "no region" row for ever
     (`domain/detection/engine.py`). Between being mapped and first being
     scraped, a signal has one unlabelled row reading UNKNOWN, which is right;
     each evaluation then unioned it forward, and a region reporting nothing
     outranks a region reporting well, so four of six golden tiles read "no data"
     on a healthy application for the rest of its life. The stored states are now
     the last verdict rather than a union of every verdict there has ever been.
  2. The join brief was degraded on every call
     (`application/brain/agents/communication.py`). Its schema carried `maxItems`
     and `maxLength`; structured-output modes do not enforce those, and the
     provider treats any schema failure as no answer - so a good brief one line
     too long was thrown away and the screen wore a DEGRADED badge it had not
     earned. Shape is a correctness question and belongs to the schema; length is
     a layout question and belongs to the reader, which already clamps it.
  3. "No topology is mapped for this application" fired when the *blast radius*
     named no services, which is a different statement, and read as a flat
     contradiction beside a panel showing nine of them.
  4. A model rewrite kept the template's provenance line, so a model-written
     brief said in one caveat that no model wrote it.

  A degraded LLM call now logs its detail and the model, which is what turned the
  first of those from "the AI is degraded" into a fifteen-minute fix.

- **[S8-T03, S8-T04, S8-T06][REQ-007] The real design.**
  `web/design-system/tokens.css` rewritten from the export's own token file;
  `web/design-system/README.md` names the prototype role every token came from;
  `web/tailwind.config.ts` exposes them by name and holds no hex.

  Three things worth knowing about this design: the raised surface is *darker*
  than the page, so cards sit in the ground rather than on it and need almost no
  shadow; every radius is 0, all three steps; and there is exactly one red, so
  severity gets no traffic light of its own. The `prefers-color-scheme: dark`
  block is gone - the export ships one `:root`, so a dark scheme here would have
  been a second design nobody drew.

  Archivo is self-hosted from `web/public/fonts/`, loaded with `next/font/local`.
  One variable file carries the whole 100-900 axis, which is what Google serves
  for all three weights anyway, and a machine with no outbound network at build
  time still builds.

  `AppShell` matches the prototype's nav and carries two load-bearing facts: the
  organisation, because a command centre that does not say whose estate you are
  looking at is one wrong tab away from an expensive mistake; and the signed-in
  person's role, because every refusal in this product is a role refusing.

  `web/lib/ui/primitives.tsx` and `patterns.tsx` are the component sheet. The
  badge vocabulary is a closed set, and four grammars carry the meaning: solid =
  the platform asserts this, outline = somebody claimed it, dashed = it was
  inferred, 45-degree stripes = two sources disagree.

- **[S8-T05][REQ-045, REQ-050, REQ-055, REQ-063] The war room becomes a cockpit.**
  `features/warroom/components/IncidentShell.tsx`, `IncidentHeader.tsx`,
  `WarRoomShell.tsx` rewritten, `Panel.tsx` restyled.

  The stack became the prototype's grid, and the grid is the reading order of a
  war room: `minmax(300px,350px) 1fr minmax(280px,320px)` is testimony, decision
  and measurement. A commander scanning left to right goes from "what are people
  telling me" through "what should I do" to "what is actually true".

  S10 to S18 are not ten screens. Each is this screen with one panel expanded
  into a document, opened from a link at the foot of the panel that owns it, and
  addressable as `?view=ledger` - so an incident commander can send somebody a
  link to the thing they are talking about.

  The ten panel titles and `title="Who is in the room"` are unchanged.

- **[S8-T07][REQ-010, REQ-036] The screens people arrive through.**
  Every radius in this design is 0 and 39 files still had rounded corners; that
  pass was mechanical. Then S1 - which was still the Sprint 0 scaffold describing
  an authless shell - became the prototype's landing with three rules that are
  this build's own; S4 became the card grid; S5 became the prototype's split with
  a 3x2 tile grid and journeys as a table.

  `drills/second-app.yaml` plays the acceptance clause rather than asserting it:
  a second application in the same organisation, deliberately shaped differently
  - four signals not six, two journeys not four, one region not two, no chaos and
  no topology, two families left unmapped because most applications have gaps. It
  onboards through the same public contracts and the screens fill from its data.

- **[S8-T08][REQ-072, REQ-016] S20: the sealed record, on a screen.**
  `web/app/incidents/[incidentId]/replay/page.tsx`,
  `features/audit/{api.ts,hooks/useReplay.ts,components/ReplayScreen.tsx}`.

  The integrity banner first and never a footnote; the seq scrubber as a *server*
  parameter, so a scrubbed view is a byte-prefix of the whole record rather than
  a subset the screen chose; the record's six columns, with an em dash where the
  record has nothing, because "we did not record that" and "nothing changed" are
  different findings; and the review's refusal rendered rather than hidden.

- **[S8-T08][REQ-072] A transient lock could break the audit chain.**
  `infrastructure/persistence/db.py`. The screen reported it on its first run:
  the live chain did not verify from seq 4451, five records affected, and the
  cause was not tampering. SQLite serialises writers and the default five-second
  busy timeout was being exceeded - the detection loop, the probe runner, the
  escalation sweep and a request all wanting the write lock inside one second -
  and a writer that gives up after its hash was computed leaves the next entry
  pointing at a row that never landed. `PRAGMA busy_timeout` is now 30s.

- **[S8-T10][REQ-062, REQ-063] The Run button never worked.**
  The walkthrough's job is to find what a test cannot see, and it found this: the
  Next Best Action panel posted `args: {}` at every action, the reference
  application's actions declare required parameters, so the primary control on
  the most-looked-at panel answered 422 for every action at every risk class.
  Nobody had clicked it - Sprint 6 built it, Sprint 7's suite drove the API with
  arguments, and the gap was exactly the shape a test does not reach.

  Nothing published the schema. The domain has kept `params_schema` from each
  application's manifest since S6-T02, because the Tool Gateway validates against
  it before anything is authorised, but no API response carried it. It is now on
  the NBA candidate, and the panel renders the *required* properties from the
  application's own schema - an enum becomes a select, everything else a field.
  Values are typed on the way out, because a gateway that accepted `"1"` for an
  integer would be a validator that had stopped checking. The platform does not
  invent an argument: a screen that defaulted a region would be choosing which
  half of an estate to act on.

- **[S8-T10][REQ-016, REQ-072] The audit chain could fork under its own load.**
  The new replay screen reported a broken chain on a freshly reset machine -
  three records affected, nothing tampered with. `append` read the head and then
  inserted: two statements. The detection loop, the probe runner and the
  escalation sweep all append, two read the same head, both stored it as
  `prev_hash`, and the chain forked. The busy-timeout change earlier in this
  sprint was treating a symptom and made it *more* likely, because it stopped one
  of the racing writers failing.

  The link is now chosen inside the insert, by a scalar subquery: one statement
  is atomic under the write lock. Negative-checked - the new test fails against
  the old append and passes against the new one - and the screenshot in
  `docs/screens/S20-replay-and-pir.png` is the chain verifying over 91 entries.

- **[S8-T10][REQ-073] The shipped `reset.sh` refused to run inside the shipped
  product.** It guards itself with three markers so a script whose job is `rm -rf`
  cannot run in the wrong place, and one of them was `CLAUDE.md`, which the
  package deliberately excludes. So unzipping the archive and running `reset.sh`
  - the first thing `docs/22` tells an operator to do - refused. Found by doing
  it. The markers are now three files a release actually contains.

- **[S8-T09][REQ-012, REQ-045] S17: role views, as a map.**
  `features/warroom/roleLayouts.ts` plus one thin route. Presence, not position:
  a panel that stays is where the full cockpit puts it, so somebody who learns
  where the bridge is as an SME finds it there the day they are made commander.
  `readOnly` removes controls and keeps information - an executive pressing "run
  this action" during a P0 is a failure mode, but a view that hid the evidence to
  justify hiding the button would be a different product.

  This closes the last unticked line in `playbook/coverage-checklist.md`, carried
  since Sprint 6.

## Not built / deferred

- **A sparkline on the golden tiles.** The prototype has one and these do not.
  The detection route carries a reading per region, its baseline and its
  threshold - not a history - so the only line the component could draw is one
  point per *region*, which renders as a trend and is not one. A chart that reads
  "this metric is rising" when it means "region-b is higher than region-a" is
  worse than no chart. *Plan:* `/apps/{id}/rollups` already serves the history;
  this earns a sparkline when the tile reads it.

- **A second topology version in the demo drill.** The sprint asked for one after
  the deploy so Affected Services can show a delta. The application derives its
  edges from its live routing table, so its map changes when a *route* changes,
  and no fault changes a route - what does is the `failover-route` action, which
  is high risk, cannot auto-execute, and is therefore something a person does
  during the incident. The drill re-reads the map anyway and the second read is
  expected to be identical, which demonstrates content-versioning instead.
  Uploading a hand-edited document would have produced a prettier screen and a
  false one.

- **S19, the drill console.** Deliberately not a screen, and recorded as such in
  the coverage checklist. The Sprint 7 boundary tests assert that no published
  route names a drill, chaos or injection and that the API package does not
  import the drill engine at all. The console is `scripts/demo.sh` and
  `scripts/drill_run.py`; a route would trade a structural guarantee for a page.

## Deviations

- **No contract or spec was changed.** `signalforge/contracts/` and `specs/` are
  untouched.
- **Model ids changed in configuration**, which the environment section of
  CLAUDE.md explicitly permits: general is `azure/genailab-maas-gpt-4.1` and
  reasoning is `genailab-maas-sonnet-4.6`, because the gpt-5.x ids on this
  gateway refuse `temperature=0`.
- **Four product files were changed outside the sprint's named list**
  (`domain/detection/engine.py`, `application/detection/service.py`,
  `domain/warroom/brief.py`, `application/brain/agents/communication.py`, plus
  `infrastructure/persistence/db.py` and one log line in
  `infrastructure/llm/provider.py`). Each is a defect the restyle made visible
  rather than a refactor, each is small, and each has a test. They are listed
  under Built with what they were.
- **`reset.sh` was not run between the mid-sprint clusters.** It was unavailable
  in this session, so the demo world was rebuilt into a fresh organisation each
  time instead. That is tenant-isolated and correct, but it left the reference
  harness carrying stacked chaos injections from ten runs, whose staggered expiry
  reverted faults unpredictably. Restarting the harness clears them - it closes
  injections left active by a previous process - and the drill now heals and
  flushes the application's own rolling windows *before* onboarding, so a run
  that follows a run does not teach the detector that broken is normal.

## Tests

- **Added this sprint:** 3, all regression tests for defects the screens exposed
  - the detector's bootstrap region row, the SQLite busy timeout, and concurrent
  appends forking the audit chain. The third was negative-checked against the old
  implementation; the first was seen to fail before its fix.
- **Changed:** 3 assertions moved with the code they describe - the live badge is
  now rendered from `IncidentHeader`, the blast-radius caveat was reworded, and a
  model rewrite must now *drop* the template's provenance line rather than keep
  it.
- **Full suite: 1257 passed, 5 skipped, 0 failed** (23m41s), run on the clean
  machine at the start of T10. Two defects were fixed *after* that run - the
  action arguments and the chain fork - and the suites covering them were run
  again rather than the whole tree: governance 73, audit 18, security 172, web +
  cockpit-surface 89, api-spec docs 10, detection 8. The whole tree was not
  re-run after those two commits, and this log says so rather than implying it
  was.

  | Gate | Result |
  |---|---|
  | Full Python suite | 1257 passed, 5 skipped, 0 failed |
  | `make llm-smoke` | OK - chat and embeddings served |
  | Frontend typecheck | clean |
  | Frontend build (`next build`) | clean, 19 routes |
  | `scripts/check_clean.sh` | OK |
  | `reset.sh` then `run.sh` from empty | harness, API and cockpit all answering |
  | `bash scripts/demo.sh` | corroborated P0, `declared: false` |
  | `python scripts/demo_check.py` | **12/12** |
  | Package unzipped and `reset.sh` run from it | refused, fixed, re-run, accepted |

## Artifacts updated

| # | Path | State |
|---|---|---|
| 09 | `docs/09-deployment.md` | `.env` loading, the CA bundle knob, and why a model must accept `temperature=0` |
| 20 | `docs/20-RTM.md` | regenerated |
| 21 | `docs/21-test-report.md` | regenerated |
| 23 | `docs/23-user-guide.md` | the twenty-three screens, captured from the running product |
| — | `web/design-system/README.md` | new: every token and the prototype role it came from |
| — | `docs/screens/` | new: S1 to S23 as built, 27 captures |
| 11 | `docs/11-openapi.json`, `docs/11-api-spec.md` | regenerated after the NBA schema gained `params_schema` |
| — | `dist/signalforge-<sha>.zip` | the package, verified against the exclusion list |

## Security notes

- **No new write surface.** The two new routes are the replay screen and the role
  views, and both are reads. The replay is behind `read_audit`, which is where it
  already was.
- **Role filtering is presentation, not authorisation.** `ROLE_LAYOUTS` decides
  what is worth a person's attention; the API decides what they may see, and it
  is unchanged. A role view can only hide something a role did not need.
- **TLS verification was not weakened** to reach the model gateway. The corporate
  root is presented through the `LLM_CA_BUNDLE` knob the product already had, and
  there is still no configuration that disables verification.
- **The audit chain got stronger**, not weaker: a write that used to be abandoned
  under lock contention now waits, which removes a way the chain could be broken
  by nothing worse than load.

## Known issues and technical debt

1. The golden tiles have no sparkline (above).
2. The demo world self-heals: every chaos scenario carries a `max_duration_s` the
   application enforces itself, so a walkthrough longer than ten minutes finds a
   healthy application. `--refault` re-arms it without touching the incident.
3. **The archive was not booted end to end.** `dist/signalforge-<sha>.zip` was
   unzipped into an empty directory, its contents checked against the exclusion
   list (no `.env`, `var/`, `node_modules`, `.next`, `.venv`, `.git`, `target/`,
   `playbook/`, `CLAUDE.md`), its entry points confirmed and its `reset.sh` run
   successfully from inside it. A full `run.sh` needs `npm ci` and a Maven build
   of the harness, which `docs/22` documents and which did not fit in the time
   left. What is verified is stated; what is not is this line.
4. **The gateway key still needs rotating by a human.** A previous release zip
   carried `.env` with a live key. This one does not - asserted against the
   archive - but the key that leaked is still valid until somebody rotates it in
   the GenAI Lab console. That is not something this repository can do.
5. Carried from Sprint 7: `next`/`postcss` advisories deferred; `pip-audit`
   unrunnable on this machine; single node, SQLite, Postgres-untested;
   performance numbers in-process and single-threaded.

## Demoable checkpoint

```bash
bash reset.sh && bash run.sh              # clean machine to a running cockpit
bash scripts/demo.sh                      # the demonstration world, up to the proposal
python scripts/demo_check.py              # the twelve checks, PASS/FAIL
make llm-smoke                            # the model path, end to end
```

Then, in the browser: `/apps/payments` shows six golden tiles with values and a
corroborated P0 proposal that only a person can declare. Press Declare, and the
war room fills - bridge, paging, ITSM ticket, blast radius with CONFIRMED and
PREDICTED nodes. `?view=ledger` addresses any deep panel; `/as/executive` is the
same room with the controls removed; `/replay` is the sealed record with its
integrity banner.

## Coverage checklist ticks

Every line is now ticked. The two that turned true this sprint are the five named
role screens, carried unbuilt since Sprint 6, and the audit replay and PIR
screen, carried since Sprint 7. S19 is recorded as deliberately not a screen.

## Entry criteria for next sprint

Not applicable: Sprint 8 is the final sprint.
