# Sprint 10 log (2026-10-27, one evening)
Goal: a client can watch an application be onboarded, seen healthy, broken by a lapsed
license, proposed against, declared, paged, named and remediated - on 2026-10-28.

The sprint opened with an audit of the four flows the demo walks (onboard, detect/declare,
reason, page), recorded in `playbook/sprints/sprint-10.md`. Six findings drove the work:
the declare buttons rendered on every application page; the healthy view keyed on status
names the detector never emits; the wizard offered values the API refuses; a license expiry
could not be named by anything in the brain; paging silently failed without a per-app
notification binding; and nothing told anyone that a severity had been proposed.

## Built

- **[S10-T01][REQ-030, REQ-036, REQ-070] The reference harness, restored, with a lapsed-license
  fault.** `reference-app/` and its configuration repository back from 0cd3313 on the instruction of
  2026-10-27 ("we can onboard a java application which emits all the signals which a real
  world production app emits"); built offline with Maven 3.9.9 (`C:\Users\ASUS\tools`) against
  the warm local repository and JDK 17. New: `payments/ProcessorLicense.java`; the
  `license-expiry` chaos scenario (`dependency_down`, 900 s); every payment and probe refused
  with 403 `LICENSE_EXPIRED` while lapsed, the refusal sentence in the body, the ledger, the
  log and the span; a `payment-processor-license` health component; `processor_license_valid`
  / `_seconds_remaining` gauges and `payment_rejections_total`; a `payment-processor` topology
  node both aggregators depend on; the `renew-license-key` action (config_change, high, two
  approver roles). Both manifests validate against the frozen schemas.
  *Verified:* over the observe contract - inject -> probe 403 -> health down -> abort -> 200.

- **[S10-T02][REQ-042, REQ-046] The proposal sheet shows only for a proposal.**
  `web/features/incidents/components/ProposalSheet.tsx`: rendered for the `proposed` stance;
  for steady and watch it collapses to a "declare manually" link that only a holder of
  `declare_incident` sees; a severity the platform did not propose is armed, then confirmed.
  *Verified:* app page screenshot with an open incident shows no sheet; code path by reading.

- **[S10-T03][REQ-042] A severity rides only on a proposal.** `domain/detection/engine.py`:
  `severity` is null unless the stance is PROPOSED. Lifecycle's `proposed_severity` follows.

- **[S10-T04][REQ-040] Status-name parity.** `GoldenTiles.tsx`, `HealthPanel.tsx` key on
  `healthy/stale/breaching/anomalous/unknown` and `healthy/degraded/unhealthy/unknown`.
  *Verified:* screenshot - tiles read `healthy` / `anomalous`, score reads `45 unhealthy`.

- **[S10-T05][REQ-036] The wizard offers only what the API accepts, and exits to the
  application.** `tier_0` and risk `critical` removed; SLO indicator placeholder says "mapped
  signal key"; step 7 gets "Open the application". *Verified:* screenshot of step 7.

- **[S10-T06][REQ-042, REQ-073] Hygiene.** Default severity matrix grades by success rate,
  not region count (committed from the working copy); `.env.example` key blanked;
  `ACCESS_TTL_CAP_S` back to 900 (`/auth/refresh` already carries a long session); `run.sh`
  banner names the reference app only when it started one; `.venv` rebuilt on Python 3.12.

- **[S10-T07][REQ-070] `drill_run.py --scenario ID`** injects a named scenario in place of the
  drill's list, for a full run or `--refault`. *Verified:* the full walk below used it.

- **[S10-T08][REQ-040, REQ-052] A failing probe carries what the application answered.**
  `infrastructure/adapters/probes.py` keeps a bounded excerpt of the body (JSON error fields
  first); `agents/telemetry.py` writes one `probe_failure` evidence node per failing journey
  with it, and the journeys impact node (dead since it read an attribute that never existed).
  *Verified live:* `GET /apps/payments/detection` journeys carry `expected 200, got 403:
  LICENSE_EXPIRED - payment processor license expired at ...`; four `probe_failure` nodes on
  the graph.

- **[S10-T09][REQ-052, REQ-053, REQ-063] A hypothesis names its root cause; an action must
  address it.** Lands the causal-fit work (`hypothesis.py`, `nba.py`, `nba_service.py`) plus:
  a `license_expiry` signature with its own title, rationale and a subject pattern that reads
  the component out of the sentence; whole-word marker matching (`oom` no longer matches
  "war room"); `probe_failure` linked to the resource-named slot; a probe whose answer names a
  resource refutes the infrastructure slot. *Verified live:* "An expired payment processor
  license is refusing every request outright" 97%, deployment 69%, infrastructure 2%.

- **[S10-T10][REQ-053, REQ-063] The causal verdict reaches the screen.** `CandidateOut`
  carries the five causal fields; the NBA card shows "causally supported / not causally
  supported" with the reason; the hypothesis card shows root cause, mechanism and what would
  settle it. *Verified:* war room screenshot - `renew-license-key` is the first alternative
  and the only supported remediation; rollback and restart rank last, unsupported.

- **[S10-T11][REQ-042, REQ-045] A proposal reaches the people who can answer it.**
  `application/detection/alerts.py` (`ProposalAlertConsumer`, registered in `deps.py` on
  `detection.severity.proposed`) delivers an in-app notification to every holder of a
  declaring role through the pager's hub; `NotificationToasts.tsx` in the shell renders
  notification frames anywhere while signed in; `/apps` cards carry a `StanceChip`.
  *Verified:* consumer driven directly against the live database - two recipients (IC, Tech
  Lead), correct title and body; toasts and the `ACTIVE · P0` chip in screenshots. The
  proposal toast itself was not watched arrive in a browser (the fault was injected before
  the consumer was loaded); the frame shape is the pager's and the pager's toasts rendered.

- **[S10-T12][REQ-036, REQ-045] Paging guard rails.** The Connect step warns when no
  notification connector is bound; the ack board says above a room of FAILED rows what that
  means, and its empty state no longer blames a missing connector. *Verified:* typecheck and
  reading; the warning is correctly absent in the screenshot because the drill binds
  `notify-in-app`.

- **[S10-T15][REQ-036] The wizard prefills what it knows, and the Map step takes a paste.**
  `PullConnectorCard.tsx` merges the application's base URL and discovery's suggestions into
  empty declared fields by effect (the `useState` initializer never saw them); `MapStep.tsx`
  gains "Import mapping from JSON", which fills the form and leaves Save to a person.
  *Verified:* typecheck; the harness console's "copy mapping JSON" produces the document it
  reads.

- **[S10-T20][REQ-070] A console for the reference application.** `reference-app/.../static/
  index.html`, served at `/` on :8080 with no credential (the filter guards `/observe/*`
  only): live tiles (license, worst success ratio, worst p95, links down, unknown payments,
  health, active faults, pool), journeys by region, the health report, every published fault
  as a button with one honest default parameter set, the active injections with abort, the
  recent errors, and an onboarding kit - base URL, the three demo credentials, "copy mapping
  JSON", "copy topology JSON", the SLO values. Plus `GET /observe/chaos/injections` (drill
  scope) so the page can list what is applied. *Verified:* in Chrome - inject from the page,
  tiles turn, abort, tiles recover.

- **[S10-T08][REQ-040, REQ-052] A failing journey's detail is its last failure.** Found on
  the second live incident: once a probe passed after a run of 403s, `JourneyHealth.last_detail`
  became "ok", the license signature stopped matching, and the brain re-titled the slot
  generically and counted the probe *for* "infrastructure" again (node versions at 17:04 and
  17:16 in the walk-through database show the flip). `journey_health` now takes the detail
  from the most recent failed record while the row is failing.

- **Demo-day setting, not code: `LLM_INCIDENT_TOKEN_BUDGET=3000000` in `.env`.** On the Claude
  API, thinking is on by default and counts as output tokens, and the brain re-reasons on every
  incident event; the default 200,000-token budget per incident was spent in about ten minutes
  (`budget_exhausted` at 199,693), after which every AI surface degraded by design. The budget
  is an in-process ledger, so an API restart also resets it.

- **[S10-T21][REQ-010, REQ-012] An invited person can open the applications they were
  invited for.** Found on the invite walkthrough: `GET /apps` and `GET /apps/{id}` carried
  `manage_apps`, which only the Org Owner holds, so an Incident Commander who signed in from
  an invite got 403 on the only list of applications there is - and the sign-in form landed
  everybody on `/setup`, which the middleware reserves for the owner, so the first thing they
  saw was "denied" and a sign-up form. The two reads are now tenant-scoped and filtered by
  binding (`RBACDecisionService.visible_apps`: org-wide sees all, app-scoped sees its own);
  every write still carries the permission. Sign-in and invite acceptance land on `/apps`;
  the navigation shows owner pages only to a holder of `manage_org`/`manage_apps` and gains
  Incidents and Your pages; a refused owner page sends the person to `/apps?denied=` with a
  sentence rather than to the landing page. The access cookie lives fifteen minutes and a
  page opened after that bounced to sign-in: the middleware now sends a session with a
  refresh cookie through `/api/auth/refresh` and back (the refresh cookie's path is `/` so
  the middleware can see it; still httpOnly). *Verified live:* IC and Tech Lead read `/apps`
  and `/apps/payments` (200), `PUT .../mapping` still 403; screenshots of `/apps` and the
  denied notice as the IC.

- **[S10-T22][REQ-061, REQ-062] A settled attempt no longer blocks the same action being
  asked for again.** The idempotency key is derived from incident, action, arguments and
  attempt; `_claim_key` treated every earlier attempt that reached the target - including a
  *succeeded* one - as the row a new request duplicates, so the second renewal of a lapsed
  licence in one incident returned the first execution with `duplicate=True` and sent
  nothing, while the application kept refusing payments. `LedgerState.settled` (every
  terminal state) is stepped over; only an open attempt - pending, awaiting approval,
  running, uncertain, reconciling, partial, compensating - is a duplicate, which is the
  double-click, the retried call and the redelivered event the key exists for. The cockpit
  now says when a request was a duplicate instead of doing nothing. *Verified live:* inject
  -> renew (attempt 1 succeeded) -> inject again -> renew (attempt 2, its own key,
  succeeded); harness health reports the licence up after each.

- **[S10-T23][REQ-063] A remediation that removes an established mechanism outranks a
  diagnostic.** Known issue 3 of this log: the read-only diagnostic led the ranking on the
  licence incident because it carries no risk toll. `rank()` gains a third categorical tier
  between "causally supported" and the score: an eligible, causally supported non-diagnostic
  whose hypothesis is at or above the application's `confidence_threshold` (the profile
  field that was stored and read by nothing until now; 0.7 by default) ranks above anything
  that only measures the mechanism. The deterministic rationale says why when the tier
  promoted something over a higher score. *Verified live:* `renew-license-key` primary at
  97% with the sentence; `read-config` is the lower-risk fallback.

- **[S10-T24][REQ-045, REQ-053, REQ-063] The war room reads like the prototype.** The
  cockpit was the three-column grid with every panel fully open - evidence lists, the
  ledger, agent runs, four score bars, a form per alternative - and ran to four screens.
  Every panel in the grid is now its compact form and the full form is the deep view:
  the brain shows the counter strip (facts, assumptions, contradictions, evidence) over
  ranked hypothesis rows that open on click; Next Best Action is one headline, the
  rationale, the safety verdict, the state of the latest attempt (requested, waiting for
  whom, approved with **Send it now**, done, refused) and one button, with the arithmetic
  behind "Plan and risk detail" and the rest behind "Other options"; Health Score and
  Metrics is live from the detector - score, delta since declare, one row per golden-signal
  family with a sparkline of the readings this screen has watched (`useDetectionSeries`;
  the rollups route needs `manage_apps`, so the line is honestly "since you opened this");
  Affected Services is the CONFIRMED/PREDICTED list with the map in the deep view; Warning
  and Guidance is its three most relevant rows; the approval sheet is one strip whose
  decision form opens on request and which offers Send it once granted; plan and
  communications collapse when empty. The room renders the moment it is read instead of
  after the model-written brief (12-13 s), the bridge says "reading" rather than "no bridge"
  while it loads, and escalation hops on the paging board show names (known issue 1).
  *Verified:* typecheck, and full-page screenshots as the IC and the Tech Lead on the live
  licence incident.

## Not built / deferred

- **T13-T18 (P2).** Severity fallback to the application tier and a services-affected rule;
  matrix editing in the wizard; discovery prefill on mounted cards; key-first push binding;
  war room following a re-grade; a zero-variance threshold floor. None needed for the demo
  narrative; all in the plan with file references.
- **FAILED pages escalating immediately** (part of T12). A successor page with no connector
  fails too; without a tenant-wide fallback - `ConnectorBindingRow.app_id` is non-nullable -
  it would only produce a chain of failures. Left as the guard rails above.
- The gateway. `make llm-smoke` reports **403** from `genailab.tcs.in` on models, chat and
  embeddings with the key in `.env`; every AI surface therefore runs degraded. Not a code
  problem; a key or allowlist problem. Deterministic mode is a supported demo (check 5).

## Deviations

- The reference harness, removed earlier the same day on instruction, was restored on a
  later instruction (quoted in commit 3b92f68). No contract or spec changed.
- **[S10-T19][REQ-051] The Claude API joins the model-endpoint allowlist** on the instruction
  "for this i just for today i'll use claude api" (2026-10-27), while the GenAI Lab gateway
  answers 403 to the organisation's key. The rule "LLM only through the organizer endpoint"
  is deviated from by one constant in `infrastructure/llm/hosts.py` - still code, still not
  configuration. Its OpenAI-compatible endpoint takes `response_format` only as a strict
  `json_schema` (`additionalProperties: false` everywhere) and refuses `json_object` and any
  `temperature` - Anthropic's own page says "ignored"; the live endpoint says otherwise - so
  the provider's single retry became a ladder: json_schema -> json_object (the gateway's
  path, unchanged) -> strict json_schema -> no response_format, with a temperature dropped
  when named. Every rung is validated here against the declared schema. No `/embeddings`
  there, so embeddings degrade as designed.
- Commit `[S10-T09]` lands uncommitted work found in the tree (the causal-fit layer) rather
  than discarding it; it parsed, was wired end to end, and the sprint built on it.

## Tests

- The suite was removed in 4b4960d, on instruction, before this sprint. Verification was by
  import checks, `npm run typecheck`, `check_clean.sh`, schema validation of the manifests,
  three small direct checks (`_contains`, `body_excerpt`, the alert consumer), and one full
  live walk: reset -> run.sh -> drill with `--scenario license-expiry` -> proposed P0 ->
  declared as the IC -> brain -> actions, plus five screenshots from a headless Chrome.
- `git revert 4b4960d` restores the suite; it will need new tests for T02, T03, T08, T09,
  T11 before it is green again.

## Artifacts updated

- `docs/sprint-logs/sprint-10.md` (this file). `playbook/sprints/sprint-10.md` carries the
  audit, the plan, the run sheet and the morning checklist (gitignored by rule 9).
- Not regenerated: 20-RTM, 21-test-report (no suite to run), 11-openapi (`CandidateOut`
  gained five optional fields; regenerate with `make openapi` when convenient).

## Security notes

- `.env.example` in the working copy carried a real gateway key; blanked before any commit.
  The key was never committed. Rotate it anyway if the working copy was ever shared.
- `ACCESS_TTL_CAP_S` had been raised to 15 hours; reverted to the 15-minute REQ-010 cap.
- The proposal alert is tenant-scoped through the hub's own predicate and addressed to role
  holders resolved from the identity repository; it carries no incident id and grants no
  read the recipient did not already hold.
- Probe body excerpts are bounded (140 chars), whitespace-folded, and stored as detail text
  the same way adapter details already are.

## Known issues and technical debt

1. ~~Escalated pages on the ack board show the recipient's user id where no display name was
   resolved for a hop.~~ Resolved from the room's member list in S10-T24; the API row still
   carries no display name for a hop.
2. `.venv-broken-py312/` and three `*.pya` backups remain in the tree; delete them by hand.
3. ~~The NBA's arithmetic puts the diagnostic first on this incident.~~ S10-T23: the
   established-mechanism tier. The arithmetic itself is unchanged, so a remediation on a
   hypothesis *below* the threshold still ranks under a free diagnostic, by design.
4. The P2 list above.
5. The model-written join brief takes 12-13 s on the Claude endpoint; the room no longer
   waits for it, but the brief panel is empty for that long on first open.
6. The war room sparklines are per browser session: the detection route carries no history
   and the rollups route needs `manage_apps`. A tenant-readable rollup history would make
   them survive a reload.

## Demoable checkpoint

```bash
bash reset.sh && bash run.sh                       # harness :8080, api :8000, web :3000
python scripts/drill_run.py drills/demo-setup.yaml --scenario license-expiry
# org slug drill-co · owner@drill.test / ic@demo.test · password drill-strong-password
# /apps -> payments reads PROPOSED · P0 -> Open -> Declare P0 as proposed -> war room
```

## Coverage checklist ticks

None new; the sprint repaired lines already ticked.

## Entry criteria for next sprint: met

The demo flow runs end to end on a reset machine. Next: restore the suite and write the
tests the sprint owes; then the P2 list.
