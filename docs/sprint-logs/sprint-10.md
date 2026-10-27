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

1. Escalated pages on the ack board show the recipient's user id where no display name was
   resolved for a hop (cosmetic; visible in the war room screenshot).
2. `.venv-broken-py312/` and three `*.pya` backups remain in the tree; delete them by hand.
3. The NBA's arithmetic puts the diagnostic first on this incident (+0.11) and the renewal
   second (-0.31) because a high-risk action's risk term outweighs its impact; the causal
   chips make the point, but a client may ask why "read config" leads.
4. The P2 list above.

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
