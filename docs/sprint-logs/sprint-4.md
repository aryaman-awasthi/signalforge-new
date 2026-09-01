# Sprint 4 log (2026-09-01, one day)
Goal: steady state to a declared P0 with progressive escalation, all human-gated.

## Built

- [S4-T01][REQ-040,041,042,046] Domain detection: steady state to a corroborated,
  human-gated severity proposal. Five-second metric rollups into a second database;
  rolling baselines held while a signal is breaching; anomaly = a threshold breach
  sustained across three evaluations; SLO burn where fast (~14.4x/5m) proposes and slow
  (~6x/30m) opens Watch; corroboration gated on two independent signal families with one
  customer-facing; a weighted health score (availability 30, errors 25, business 20,
  latency 15, saturation 10) renormalised over the weight actually covered, worst region
  winning; alert-storm clustering by time and topology with a most-depended-upon root and
  a visible suppressed count; and a severity proposal run through the application's own
  matrix that carries evidence refs and can never be constructed as a declaration.
  signalforge/domain/detection/ is pure - no I/O, no framework, no clock of its own -
  with rollups.db as its own DeclarativeBase, because a five-second write loop has no
  business sharing a transaction with the audit hash chain. Pollers and the probe runner
  are scheduled jobs in workers/pollers.py; three manage_apps routes under
  /apps/{app_id} where the read advances nothing and the evaluate is a human override of
  the cadence, audited, and neither declares anything.
  Verified: 63 REQ-tagged tests, 58 of them driving the real pipeline - an application
  registered through POST /apps, a connector bound through POST /connectors, and the
  shipped poller scraping an ASGI application serving only the frozen observe contract.
  Written failing-first for idempotency and tenant isolation.

- [S4-T02][REQ-043,044] The lifecycle state machine and the change window. Six states -
  steady, watch, proposed, active, recovering, closed - where the first three are
  detection's stances and the last three are an incident, and an incident exists because
  a person pressed Declare. domain/incidents/lifecycle.py holds TRANSITIONS and
  HUMAN_ONLY as literals with an actor argument, so "only humans declare, change severity
  and close" is a refusal every future caller meets - a use case, an agent, a drill, a
  script - rather than a property of which service methods happen to exist. The one move
  a machine may make inside an incident is recovering -> active, because escalation is
  the safe direction. Closure is terminal for people as well as machines, so there is no
  reopen anywhere in the API: a regression after closure is a new decision by a new
  person at a new time, and that is what the record should say. ITSM sync through the
  itsm-lite binding where a create that timed out is `uncertain` rather than `failed` and
  the retry calls `get` before it calls `create`. Change windows folded from real
  deployment hook calls by a bus consumer group, keyed on (tenant, app, change_id), so a
  re-triggered pipeline is one window rather than two.
  Verified: 43 REQ-tagged tests. Written failing-first: the human-only floor, declare
  idempotency, the one-open-incident-per-application guard, and incident tenant
  isolation. All seven of ground rule 6's failure paths covered.

- [S4-T03][REQ-045] The war room. Opened by the act of declaring rather than by a button,
  through a hook that fires after the declaration commits and cannot roll it back - a P0
  with a room that failed to assemble is bad; a P0 that could not be declared because the
  room failed to assemble is unforgivable. Membership is derived in three steps kept
  deliberately apart: a `BlastRadius` from the declaration snapshot and the topology, a
  pure `required_roles` table over it, then role bindings. The graph walk goes to
  *dependents* rather than dependencies, and an application with no matching node yields
  an empty radius rather than calling the whole estate a blast radius. A required role
  nobody holds is a stated `MembershipGap` - on the room, on the timeline, in the audit
  chain - because a P0 with an empty comms-lead seat is something to find out at minute
  two. Paging fans out by severity (a P0 rings a phone; a P3 does not, because a muted
  pager is how the next P0 is missed), every page stores its own `ack_deadline_at`, and
  the countdown is arithmetic against a passed-in clock rather than a timer - so it
  survives a restart, agrees between the board and the sweep, and runs at 100x on a
  drill's virtual clock with no drill-aware code. Escalation walks the rotation skipping
  who has already been paged, then the policy's next step, then another holder of the
  role; running out raises loudly with `needs_human` on the timeline. The join brief is
  template-sourced and says so, with a caveats section that refuses to read confidently
  from thin evidence. Bridge commitments become tasks with an owner and a due time,
  conservatively: a question, an option and a forecast about a service are none of them
  commitments, and an owner is never guessed.
  Verified: 66 REQ-tagged tests. All seven failure paths covered, including the two this
  task exists for: a page answered over the /ws socket is reconciled from the delivery
  receipt before the sweep escalates past it, and derived notification ids mean one page
  is one notification however many times the code runs.

- [S4-T04][REQ-040,042,043,044,045] The cockpit. Six screens, and one question running
  through all of them: is the reader looking at something the platform measured, proposed,
  or something a person decided? The same six words name the first and third, so every
  screen says which it is showing rather than leaving it to a colour. S5 app detail with
  golden tiles showing the worst region (not an average, which describes nothing that is
  happening), `unknown` never drawn as healthy, a change-window chip lit by the API's own
  field, and a health panel whose Grafana tab always states its fallback because a browser
  gives an iframe no usable error. S6 watch banner, quiet by requirement, naming the
  signals under observation. S7 proposal sheet whose corroboration checklist renders the
  rows that did *not* pass, because an uncorroborated proposal is exactly the one to look
  at carefully. S8 ack board with per-row timers and the reason each person was asked. S9
  war-room shell with the ten panel titles verbatim plus Who is in the room. S22 mobile
  pager: acknowledge or read the brief, nothing else.
  Verified: 16 REQ-tagged tests, two of them calibrated against a deliberate regression
  rather than assumed. `npm run build` green with 21 routes (15 before); `tsc --noEmit`
  clean.

## Built after the sprint: the S4 carry-over pass (2026-09-01)

Four gaps this log recorded honestly were closed in a follow-up session, plus one the
closure itself uncovered. Nothing below relaxed a control; one guard moved, deliberately
and with its reasoning recorded.

- **[S4-C01][REQ-033, REQ-045] The cockpit's socket, and the credential that made it
  possible.** DR-087 named the two ways out of the polling gap and rejected the cheap
  one; this is the other one. `POST /auth/ws-ticket` mints a 30-second, single-use JWT
  typed `ws_ticket`, bound to the caller's tenant, user *and session*, and the browser
  spends it as the `ticket, <value>` subprotocol on the `/ws` handshake - a header, never
  a query parameter. The session token stays in its httpOnly cookie and browser
  JavaScript still cannot read it, which was the whole point. The type is what makes it a
  credential rather than a second session: `JWTAccessTokenService.verify` refuses a
  ticket and `JWTSocketTicketService.redeem` refuses an access token, so the two are not
  interchangeable in either direction; redeeming consumes the `jti`, so a captured
  handshake replayed a second later is refused inside its own lifetime; and the ticket
  resolves through the *same* session lookup a bearer token does, so a logout closes the
  socket door as firmly as it closes the HTTP one (DR-091).
  What arrives is deliberately thin. A `cockpit-socket` consumer group on the existing
  bus turns `incident.*` and `warroom.*` into `{"type":"room.changed", incident_id,
  topic, seq}` for the sockets watching that incident - **a nudge, not data**. The screen
  re-reads through the authorised HTTP route it was already polling, so the socket never
  becomes a second read path with its own authorisation to get wrong (DR-092). The poll
  does not stop; it slows to thirty seconds while the socket is up, because the hub drops
  a frame on purpose when one browser falls behind, and a screen that stopped reading
  entirely would go stale without noticing. `LiveBadge` says which mode the screen is in,
  because a responder who believes a screen is live when it is not is how somebody
  concludes nobody is answering.
  Verified: 18 REQ-tagged tests written failing-first - the six credential properties
  (short-lived, single-use, typed both ways, session-bound, tenant-bound, authenticated
  to obtain), the nudge's shape, its tenant scope asserted by watching the *right*
  incident id from the *wrong* tenant, and the client half held by structural tests
  including one that fails if a second `new WebSocket(` appears anywhere outside the
  store.

- **[S4-C02][REQ-041, REQ-046] A corroborated P0, produced legitimately.** This log
  recorded that the live acceptance proposed P2/P3 rather than P0 because only one signal
  family was anomalous, and called that the floor working. It was - but the acceptance
  clause still went unmet, and the reason was the *drill*, not the gate. `drills/` now
  holds `s4-acceptance.yaml`: the fault moves from `FIB-7` (region-b's route only) to
  `CORE`, the one link both regions cross, and the second witness is SignalForge's own
  synthetic probe rather than a second metric from the same reporter - two measurements
  of two different things by two different parties, which is what "independent" has to
  mean. `MIN_INDEPENDENT_FAMILIES`, the customer-facing requirement, the severity matrix
  and the human-declaration floor are all untouched (DR-090).
  Verified: `tests/reference/test_reference_acceptance.py` drives the **real JVM
  harness** with its own traffic generator switched off, so the success ratio is
  arithmetic over the payments the test placed rather than a sample of a seeded stream.
  Twelve steady ticks (`steady`, health `healthy`, no proposal), the injection, then one
  tick to `watch` with corroboration explicitly false, then two more to `proposed` with
  families `{availability, business}`, P0, 2 of 2 regions, tier-1 journey, success rate
  under 80 percent, `requires_human` true and `declared` false - and `GET /incidents`
  still returning zero, because detection reached a P0 proposal and stopped. The org
  owner who onboarded the application is refused the declaration (403, no
  `declare_incident`); an incident commander makes it, and the record reads
  `severity_source: proposed`, `overrode_detection: false`, `declared_by_human: true`.

- **[S4-C03][REQ-014, REQ-032, REQ-045] The bridge's evidence excerpt is redacted.** The
  ingest floor is *reused*, not reimplemented: `domain/ingest/redaction.mask` is applied
  in `BridgeService` on the way out - so nothing unmasked reaches the collaboration tool
  either - and on the way in, so a line typed on the far side or a scripted drill line is
  masked before it can become a commitment's evidence. Two masking implementations drift,
  and the weaker one would eventually decide what a data-protection incident looks like
  (DR-093). The bridge event carries `redacted` and names the policy when something was
  removed, the way an ingest envelope does.
  Verified: written failing-first. A card number and an email address are typed onto a
  real bridge through the shipped teams-sim adapter, the commitment still becomes a task,
  and neither string appears in the war-room response, the transcript, the task board,
  the join brief, the event stream, the audit chain or the scrape.

- **[S4-C04] A browser drove the acceptance.** The qualification this log carried from S3
  is gone. `scripts/drill_run.py` reads the drill file and sets a live cockpit up to the
  moment before a declaration - onboard, invite, rotate, drive load, heal whatever the
  last run left applied, inject, announce a deployment over the frozen ingest path with a
  real HMAC - and then stops, because what happens next is a person pressing Declare.
  `web/e2e/cockpit.spec.ts` is the journey, one test, against the live stack, using the
  system browser (`channel: "msedge"`) so nothing downloads a browser bundle. It skips,
  loudly, when there is no stack, the way the reference suite skips without a jar.
  Recorded live: `/apps/payouts` steady; `/apps/checkout` showing the S6 watch banner
  ("AI is checking - Something is being observed. Nothing has been decided, and no
  incident exists.") naming the signals under observation; `/apps/payments` showing the
  P0 proposal sheet with all five corroboration rows ticked and the change-window chip
  lit; Declare pressed, landing in INC-000001's war room with its eleven panels, its
  membership gaps stated, per-row countdowns, and the **Live** badge - the socket
  connected, with two `POST /api/proxy/auth/ws-ticket` 201s in the network log; a second
  session as the SME finding their own page on `/my/pages` and acknowledging it; and that
  same session refused **403** on somebody else's page while holding no permissions at
  all.

- **[S4-C05][REQ-012, REQ-043] The clause the browser found: nobody could complete it.**
  The walkthrough's first act failed. The S7 proposal sheet lives on `/apps/[appId]`,
  which reads `/apps/{id}/lifecycle`, `/detection` and `/change-window` - all three
  guarded by `manage_apps`, which the frozen roles matrix gives to the org owner alone.
  The incident commander holds `declare_incident` and cannot open the screen; the org
  owner can open it and cannot declare. **No single role could satisfy "IC declares in
  the UI"**, which is one of this sprint's four acceptance clauses, and no API test could
  have caught it because each half worked.
  The three reads are now session-scoped tenant reads, which is what the incident reads
  already are and for the same reason - `GET /incidents/{id}` already published the same
  proposal snapshot, families, health score and evidence refs included, to any session in
  the tenant, because the SME who answers a page holds nothing at all. Every write under
  `/apps` still carries `manage_apps`: forcing an evaluation, the raw rollup series, the
  mapping, the topology, the SLOs, deleting the application. The narrowing genuinely
  given up is per-application on those three reads, and it is stated rather than glossed
  (DR-089). Written failing-first, pinned as a literal three-route list in two security
  files so a fourth cannot join quietly, and asserted from the responder's side: an
  incident commander reads all three and is refused all four writes; an SME reads the
  lifecycle and is refused the evaluate and the rollups.

## Not built / deferred

- **The four agent panels are placeholders.** Next Best Action, AI Copilot, Warning and
  Guidance and Team Chat render with their real titles and one sentence saying what fills
  them. Impact: four of the eleven S9 panels contain no data. Plan: S5 (REQ-050..REQ-055)
  owns all four; the layout is deliberately already in place so it does not move under an
  operator when they land (DR-088).

- **The join brief is a template, not a model.** By design for this sprint, and the
  ordering is the point: the version that exists when the model is down has to be
  genuinely useful. Impact: no generated prose. Plan: S5's REQ-051 swaps the writer behind
  the same `source` field, and a degraded model falls back to exactly this text.

- **No topology diagram in the war room.** `Affected Services` lists names. Impact: an
  operator reads a list rather than a graph. Plan: unchanged from S3 - the graph view
  component, S5's topology panel.

- **The commitment harvester is deterministic and deliberately conservative.** It misses
  commitments phrased indirectly ("Sam, can you check the pool?" is a request, not a
  promise, and is not harvested). Impact: some real commitments do not become tasks. Plan:
  S5's model reads the same transcript and the derived key makes the two paths compose
  without double-writing; the deterministic version stays as the fallback.

- **Escalation latency is bounded by the sweep cadence, not exact.** A page escalates at
  its deadline plus up to `escalation_interval_s` (15s default). Impact: a countdown that
  reads 0:00 may take a few more seconds to fire. Plan: none - this is the correct trade
  for a countdown measured in minutes, and tightening the cadence is one setting.

## Deviations

- No contract deviations, and none was needed. signalforge/contracts/ and specs/ were read
  throughout and never modified. OBSERVE_PATHS, INGEST_PATHS, the canonical models,
  AdapterKind, the SPI method names, ActorType, Plane, Authority, the error taxonomy and
  the roles matrix were all used as frozen in S0. The only edits to
  specs/requirements.yaml are status transitions: REQ-040, 041, 042, 043, 044, 045 and 046
  planned -> built. No id was added or renumbered (REQ-046 was appended at S4-T01 and is
  recorded there).
- **Four session writes now carry no permission**, which is the sprint's one significant
  security-shaped decision and is recorded as DR-077. Acknowledging a page, joining the
  room, speaking on the bridge and completing your own task are guarded by the entity
  refusing to act on anybody else's behalf rather than by a permission, because
  `ROLE_PERMISSIONS[Role.SME]` is empty and the SME is the person most likely to be paged.
  `SESSION_ONLY_WRITES` in the security suite grew from one entry to five. This is not a
  weakening: `declare_incident` would let *any* holder acknowledge *anybody's* page,
  whereas `Page.acknowledged` refuses everyone but the addressee. Both directions are
  asserted, and a test joins the exemption list to the self-scoping tests so an entry
  cannot be added to one without the other.
- The war room is wired to the lifecycle by **hooks filled in the composition root**
  rather than by an import, so the arrow keeps pointing inward: `application/incidents/`
  has no import of `application/warroom/`, and a node could run incidents with the room
  switched off without changing a line of the lifecycle.
- `BridgeService.post` harvests its own message rather than leaving it to the sweep
  (DR-084). The frozen collaboration SPI's `post(channel, text, thread_id)` carries no
  author, so the adapter writes the transcript under the connector's identity; the only
  place authorship is *known* rather than inferred is at post time. Re-parsing our own
  `"<name>: <text>"` prefix would have been inventing a protocol on top of a frozen one.
- Design decisions recorded as DR-060 through DR-088 (29 entries this sprint; DR-060..068
  at S4-T01, DR-069..076 at S4-T02, DR-077..085 at S4-T03, DR-086..088 at S4-T04).

## Tests

- added: 188 (63 S4-T01 / 43 S4-T02 / 66 S4-T03 / 16 S4-T04). Of those, 14 are security
  tests in two boundary files (tests/security/test_incident_boundary.py,
  test_warroom_boundary.py); 16 are cockpit structure and contract-pinning tests; the
  remaining 158 are functional, and almost all are integration tests rather than unit
  tests - every detection test drives a real poller against a live ASGI application
  serving the frozen observe contract, every lifecycle and war-room test goes signup ->
  POST /apps -> POST /connectors -> POST /incidents through the real API, and the bridge
  tests post through the shipped teams-sim adapter over the real ConnectorInvoker. The
  genuinely unit-level suites are the pure domain ones: the lifecycle table, the page
  state machine, the escalation ladder, the blast-radius role table, the topology walk and
  the commitment harvester.
- **written failing-first**, as CLAUDE.md requires for policy, idempotency, reconciliation,
  RBAC and tenant isolation: rollup idempotency and detection tenant isolation (S4-T01);
  test_lifecycle_floor.py, test_incident_idempotency.py and incident tenant isolation
  (S4-T02); the derived-notification-id guard, the self-scoped acknowledgement control and
  war-room tenant isolation (S4-T03).
- **calibrated rather than assumed** where the implementation preceded the test, which is
  the honest way to describe S4-T03's boundary suite and S4-T04's structural tests: the
  panel-title assertion was checked against a renamed panel and the countdown assertion
  against a browser-clock implementation, both in memory without modifying product code,
  and both caught the regression. One calibration was refused by tooling and not worked
  around: removing the `NotYourPageError` guard from `domain/warroom/paging.py` to confirm
  the 403 test fails was blocked as a security weakening, correctly, so the floor is
  pinned as a literal in the domain suite instead - the same technique S4-T02 used for
  `HUMAN_ONLY`.
- **failure design is covered by test at every task**, on all seven of ground rule 6's
  paths. The two most load-bearing: an ITSM create that timed out stays `uncertain` and
  the retry reconciles before it creates, and a page answered over the /ws socket is
  reconciled from the delivery receipt before the escalation sweep can escalate past it.
- **two real defects were found by the tests and fixed**, both worth recording because
  neither would have been caught by review: a join brief with an unavailable verdict
  rendered *no* "what we know" section at all rather than saying it knew nothing, and the
  frozen collaboration SPI's exclusive `since` made a bridge read as silent on a virtual
  clock - certain on a drill, not merely a race. A third was found by the live acceptance
  rather than by a test: the cockpit typed a `chip` field on the change window that the
  API never served, so the chip would never have lit; the pinning test now covers
  `ChangeWindowOut` and `ChangeWindowItemOut` as well.
- suite result at the end of the sprint: 874 passed, 0 failed, 0 skipped (792 at the end
  of S4-T02, 686 at the end of S3). Full breakdown by module and by requirement in
  docs/21-test-report.md; the requirement join is in docs/20-RTM.md.
- **the S4 carry-over pass added 25 tests** and changed four existing ones. New:
  `tests/security/test_socket_ticket.py` (14 - the socket credential, every property in
  the direction that would hurt), `tests/warroom/test_socket_push.py` (4 - the nudge, its
  shape and its tenant scope), `tests/reference/test_reference_acceptance.py` (1 - the
  corroborated P0 against the real JVM harness), one redaction regression in
  `tests/security/test_warroom_boundary.py`, four cockpit-socket structure tests in
  `test_web_warroom.py`, and one more guard test in
  `tests/security/test_detection_boundary.py`.
  **Written failing-first**, all of the security-shaped ones: the ticket suite before the
  ticket existed, the bridge redaction before the mask was applied, and the responder-read
  guard tests before the three routes moved - so the guard change was a decision taken in
  a test file rather than a permission quietly disappearing. Changed rather than added:
  `test_every_detection_route_is_behind_manage_apps` became
  `test_detection_writes_stay_operator_work_and_the_reads_are_responder_work`, the
  per-application narrowing assertion moved from the detection *read* to the rollups read
  and the evaluate write, `test_every_onboarding_route_is_manage_apps` now pins the three
  responder reads as a literal exemption list, and the SME's incident-boundary test now
  asserts they may read the lifecycle and still may not force an evaluation.
  **Two defects the live run found that the tests did not.** S4-C05 is the first: both
  halves of the authorisation worked, and only the journey through them was impossible.
  The second is smaller and more instructive - `signalforge_socket_nudges_total` was never
  declared in the registry, so every nudge the `cockpit-socket` consumer delivered *then*
  raised a `KeyError` and dead-lettered. Every assertion in `test_socket_push.py` still
  passed, because the frame is sent before the metric is counted; the DLQ on the running
  install is where it showed. The metric is declared, and the tests now read the
  dispatcher's own report and refuse a pass with a dead letter in it - calibrated by
  renaming the metric and watching the assertion fire.

## Artifacts updated

- 03 stories regenerated from specs (docs/03-stories.md, scripts/gen_stories.py)
- 04 functional spec: chapter 7 (detection), chapter 8 (the incident lifecycle),
  chapter 9 (the war room, with its failure catalogue), chapter 10 (the incident screens,
  including what the cockpit does not have yet and why)
- 05 specs (specs/requirements.yaml): REQ-040 through REQ-046 -> built
- 10 data design (docs/10-data-design.md, generated by scripts/gen_erd.py): the detection
  rollup store as a second database, the incident-lifecycle group (incidents,
  incident_transitions, change_windows) and the war-room group (war_rooms,
  war_room_members, incident_pages, incident_tasks)
- 11 api spec (docs/11-api-spec.md): the detection section, the incidents and
  change-window section, the war-room section, and twenty-one new event rows
- 11 api spec, machine-readable half (docs/11-openapi.json): 78 paths after the
  carry-over pass added `POST /auth/ws-ticket`, generated
- 14 functional tests: signalforge/tests/detection/, tests/incidents/, tests/warroom/,
  test_web_warroom.py; plus tests/warroom/test_socket_push.py and
  tests/reference/test_reference_acceptance.py from the carry-over pass
- 18 security tests: tests/security/test_incident_boundary.py, test_warroom_boundary.py;
  plus tests/security/test_socket_ticket.py from the carry-over pass
- 20 RTM regenerated: 52 requirements, all seven S4 requirements green with tests joined
- 21 test execution report regenerated, by module and by requirement
- decision-record.md: DR-060 through DR-088 in the sprint, DR-089 through DR-094 in the
  carry-over pass
- drills/: `s4-acceptance.yaml`, the first drill file - the acceptance scenario as data,
  read by the acceptance test and by `scripts/drill_run.py`. Deleting it leaves a working
  product; the test skips and says why (CLAUDE.md rule 7)
- web/: `e2e/cockpit.spec.ts` and `playwright.config.ts`, the browser journey; the system
  browser is used by channel so nothing downloads a browser bundle

## Security notes

- **New surfaces**: three detection routes under /apps/{app_id}, nine lifecycle routes,
  and eleven war-room routes. Every HTTP route that can name a tenant sits behind the RBAC
  dependency and is covered by the router-coverage walk, which reads the *permission* out
  of each route's own dependency closure - so a route guarded by the wrong permission
  fails the build, not only an unguarded one.
- **The carry-over pass added one route and moved three guards.**
  `POST /auth/ws-ticket` is the new surface: a session write with no permission, and the
  sixth entry in `SESSION_ONLY_WRITES`. It takes no parameter at all - the ticket is cut
  from the principal the request already resolved - so there is nothing in the request for
  a permission to protect, and a permission would disconnect the SME's pager for the same
  reason the war room's four writes carry none. Its response shape joins `SHOWN_ONCE`, and
  `ws_ticket` joins the credential-property census, so the API-surface test now refuses any
  *other* response model that returns one. The field is named `ws_ticket` rather than
  `ticket` on purpose: an incident carries an ITSM `ticket`, which is a reference and not a
  secret, and a census that cannot tell them apart is not a census.
  The three moved guards are `GET /apps/{id}/detection`, `/lifecycle` and `/change-window`
  (S4-C05, DR-089). This is the pass's one security-shaped decision and the only one worth
  arguing about, so it is stated plainly rather than folded into a feature note: three
  reads that were `manage_apps` are now session-scoped tenant reads. What that buys is the
  sprint's own acceptance clause - before it, no single role could declare in the UI. What
  it costs is per-application read narrowing inside a tenant: an operator bound to one
  application can now read another's stance, though not its telemetry, and nothing they can
  act on. What it does *not* cost is a new disclosure class, because `GET /incidents/{id}`
  already published the same proposal snapshot to any session in the tenant. Every write
  under `/apps` is unchanged, the exemption is pinned as a literal three-route list in two
  security files, and the guard tests were written failing-first.
- **The socket credential is a credential, and is treated as one.** A ticket is typed
  (`ws_ticket`), so the access-token verifier refuses it and it reaches no HTTP route;
  single-use, so a captured handshake replayed inside its own lifetime is refused;
  session-bound, so a logout kills it; thirty seconds long, and not configurable. It is
  presented as a subprotocol rather than a query parameter, so it stays out of access logs
  and proxy history - the same rule `/ws` already applied to bearer tokens. A refusal says
  nothing about which of the five ways it failed. The replay set is per node, which is the
  whole deployment today and is recorded as a known issue rather than as a property.
- **The socket carries nudges, not data** (DR-092), so it introduces no second read path
  and no second authorisation to get wrong. Watching an incident is not authorised because
  it discloses nothing: the re-read is, and a socket in the wrong tenant receives nothing
  at all - asserted by watching the *right* incident id from the *wrong* tenant.
- **The bridge now meets the ingest redaction floor** (S4-C03), on the way out as well as
  in. The residual risk this log recorded - a responder pasting a card number onto the
  bridge and finding it on the task board - is closed, and the customer's data no longer
  reaches the collaboration vendor either.
- **The one deliberate loosening, and why it is a tightening.** Four session writes carry
  no permission (DR-077). The frozen matrix gives `Role.SME` nothing, and the SME is the
  person a page exists to reach; a permission on the acknowledge path would have locked
  the war room out of exactly those people. The control that replaces it is self-scoping
  enforced in the *entity*: `Page.acknowledged` raises unless the caller is the addressee,
  and `/my/pages` has no recipient parameter for a caller to supply. That is strictly
  narrower than a permission would have been, and both directions are asserted - the
  addressee may, and a commander holding every incident permission may not.
- **`deps.require_incident` is a third place an application id comes from.** A route named
  by `{incident_id}` still has to evaluate a per-application role binding, and the
  application is not in the URL. The factory resolves the incident inside the caller's own
  tenant first, then authorizes against the application it names, so an incident commander
  scoped to one application cannot move another application's incident. The cost is that
  an incident id existing in the caller's own tenant is distinguishable from one that does
  not, before the permission check - bounded and deliberate, because the lookup is
  tenant-scoped and reading an incident of one's own tenant needs no permission anyway.
- **Tenant isolation extended to seven new tables**, with the predicate inside every
  repository method and a cross-tenant read indistinguishable from a read of nothing.
  Three deliberate cross-tenant methods exist, all of them work lists or counts for the
  one job that serves the install, all documented at their ports and by test:
  `needing_sync` (the ITSM retry), `overdue_across_tenants` (the escalation sweep) and
  `needing_harvest` (the commitment harvest). Every row they return carries its own
  tenant_id and everything the jobs write goes back through the tenant-scoped methods.
- **The audit floor holds for machine acts as well as human ones.** An escalation firing
  because nobody answered wakes a second person at 03:00; it is a thing the platform did
  *to* somebody, so `publish_system` audits by default. The actor is the system and says
  so - a platform that recorded an unanswered page as though a person had acted would be
  lying in the one record a post-incident review trusts.
- **/metrics gained twelve metric families this sprint and none can name anyone.** Every
  label is a closed vocabulary: a lifecycle state, a severity, a page state, a role from
  the frozen matrix, a sync outcome. The scrape can show that a P0 has an unanswered page
  without being able to say whose, asserted against a live scrape with the tenant id, the
  incident id, the reference, the app id, a user id and an email all absent.
- **Credentials still exist on exactly one path.** Paging decrypts a notification
  binding's credential on every send and the bridge decrypts a collaboration binding's on
  every post; a test binds real connectors with real secrets, declares a real incident,
  posts a real bridge message and then asserts none of it appears in the war-room
  response, the brief, the task board, the event stream, the audit chain or the scrape.
- **Threat model**: no new STRIDE rows were authored. The existing rows on tenant
  isolation and audit tampering now have more code behind them. The AI rows remain S5
  work, and the new residual risks below belong in docs/12 when it is revised alongside
  S6's governance surface.
- **Residual risks, accepted and stated**: an unassigned incident task is completable by
  anybody in the tenant, which is deliberate (somebody has to be able to close out work
  that was done) and bounded by the task being incident-scoped and every completion
  audited; the escalation sweep's cross-tenant work list means one slow tenant's pages
  can delay another's escalation on a shared node, bounded by the 100-row limit; and a
  cockpit whose socket is unavailable falls back to polling, so a page can be visible up
  to ten seconds after it was raised - the screen says which mode it is in rather than
  leaving a responder to assume. The bridge-redaction risk this list carried is closed
  (S4-C03). One risk is *new* and is the carry-over pass's own: three reads under
  `/apps/{id}` no longer narrow per application within a tenant (DR-089, S4-C05).

## Known issues and technical debt

- ~~The cockpit has no /ws client and therefore polls.~~ Closed by the carry-over pass
  (S4-C01): the cockpit connects with a socket ticket and re-reads on a nudge, and the
  poll remains underneath at a slower cadence as the degraded path.
- ~~The bridge's evidence excerpt is not redacted.~~ Closed by the carry-over pass
  (S4-C03): the ingest floor is applied in `BridgeService` on the way out and on the way
  in, reusing `domain/ingest/redaction.mask` rather than adding a second policy.
- **A consumer that raises after it has already had its effect passes its own tests.** The
  `cockpit-socket` metric defect above is the general shape: a handler whose side effect
  lands before its bookkeeping looks correct from the outside and dead-letters every
  event. The tests now assert `DispatchReport.dead_lettered == 0`, which is the only place
  the difference is visible, and every future consumer group should be tested the same
  way.
- **Adding a consumer group replays the log.** `cockpit-socket` starts at offset zero, so
  the first boot after this change walks every event already in the outbox - observed at
  9,820 events on the development database, drained in about thirty seconds with
  `/health` reading `degraded` (`lag`) until it caught up. Correct behaviour and
  self-healing, but it is what an operator will see once, and it is worth knowing before
  they see it.
- **The socket's replay guard is per node.** A redeemed ticket's `jti` is remembered in
  process. That is the whole deployment today (one machine, no Docker) and the ticket's
  thirty-second life bounds the window regardless, but a multi-node install needs a
  shared store before the single-use property is install-wide rather than node-wide.
- `_notification_bindings` and `_escalation_for` re-read every binding, rotation and
  policy on every page. Right at this scale and obviously bounded; a cache keyed on the
  binding's `updated_at` is the change if an install ever pages continuously.
- The escalation chain is walked in Python - ten keyed reads at worst, because the domain
  caps hops at ten. A recursive CTE would be one query but would need dialect-specific
  SQL between SQLite and Postgres, which is not a trade worth making at this bound.
- `PagingService.page_room` pages members serially. A room with twenty required people
  makes twenty sequential sends; a gather with a semaphore is the change, and it matters
  only above roughly ten.
- The war room reads the incident, the brief and the bridge in three round trips per poll.
  One composite read would be a third of the requests; left alone because each currently
  survives the others' failure and collapsing them would couple those failures.
- `signalforge_pages_overdue` is computed by running the sweep's own work-list query on
  every scrape. Correct and indexed, but it is the one gauge that costs a query
  proportional to how badly things are going.
- Carried over from earlier sprints: make still requires an elevated install, so every
  target was run by calling its recipe directly (verified for run, test, rtm, report, erd,
  openapi and clean-check); web port 3000 vs cockpit 3001; scripts/seed_bus_demo.py owns
  its own consumer group; lane count is fixed at 16; generated documents are written with
  the platform's line endings, which git normalises on commit;
  test_reference_onboarding.py skips when the jar is absent or java is not on PATH.

## Demoable checkpoint

The judgeable milestone is a fault injected on an application SignalForge has never heard
of, becoming a corroborated proposal with evidence refs, then a declared P0 with a war
room that pages the right people and escalates when nobody answers. **Steps 1 to 12 below
were driven live** against the harness on :8080 and the platform on :8899 during this
sprint; step 13 is the browser walkthrough nobody performed.

1. `bash scripts/check_clean.sh` - "core is app-agnostic".
2. `bash reference-app/*/run.sh` starts the harness on :8080; `.venv/Scripts/python.exe -m
   alembic upgrade head` then uvicorn on :8899. `/health` reads 200 with database,
   event-bus and worker (**10 jobs**) up.
3. Sign up, register `payments` against `http://localhost:8080`, bind five connectors
   (http-observe-metrics, http-observe-health, notify-in-app, page-sim, teams-sim), map
   `payment_success_ratio` as the business signal per region, invite an IC, a tech lead
   and two SMEs, and create an on-call rotation over the two SMEs.
4. `GET /apps/payments/lifecycle` - **state `steady`, source `detection`**. The screen
   says which of the two it is reading, which is the distinction this whole sprint rests
   on.
5. Inject a single-region fault with the drill credential:
   `POST localhost:8080/observe/chaos/fabric-link-loss/inject
   {"params":{"link":"FIB-7","loss_ratio":0.6},"scope":"drill:s4-acceptance",...}`.
   Region-a stays healthy beside it, which is the control the corroboration rule needs.
6. Poll `POST /apps/payments/detection/evaluate`. Observed live, in order:
   `steady health=None` → `steady health=58.3 band=unhealthy` → `steady 56.37` →
   **`watch` health=49.66, severity proposal P2**. The stance moved because a breach was
   sustained, not because a threshold was crossed once.
7. Keep evaluating. Once the breach reaches three sustained evaluations the signal turns
   `anomalous` (streak 118 by the end) and the proposal carries its evidence ref:
   `signal rollup:success/region-b family=business region=region-b customer_facing=True`,
   with `success/region-a` reading `healthy` beside it. `requires_human` is `true` and
   `declared` is `false` on every proposal - detection reached a proposal and stopped.
8. The IC declares: `POST /incidents?app_id=payments {"severity":"P0"}` → **INC-000001**.
   The war room assembles itself: blast radius `P0 on payments`, required roles
   `[incident_commander, tech_lead, sme, executive]`, **gap: `executive`** (nobody is
   bound to one, and the room says so rather than rendering a shorter list), members Ida
   Commander, Lee Lead, Sam SME and Sasha Second - each carrying the sentence saying why.
9. The SME is paged with a visible countdown: `state=sent countdown=300s
   channels=['in_app','sms','voice']`, and it appears on **their own** pager
   (`GET /my/pages` → count 1, recipient themselves). The second SME cannot acknowledge
   it: **403**. Neither can the incident commander, holding every incident permission:
   **403**. That is the self-scoping control, and it is tighter than a permission.
10. `POST /incidents/{id}/pages/{page}/escalate` - four pages ringing, shortest countdown
    300s. The page escalates to **the other rotation member**, hop 1, with its own fresh
    300s countdown; the original moves to `escalated`. The successor acknowledges: `acked`.
    Acknowledging again returns the same `acked_at` and the *first* note - one act.
11. `GET /incidents/{id}/brief` - source `template`, headline
    `INC-000001: P0 on payments, active, 0 minutes old. Payments failing in region-b`, and
    a caveat that says it plainly: *"The evidence is not corroborated - one signal family,
    not two - so the cause named above is a hypothesis."*
12. Say `I'll roll back region-b in 10 minutes` on the bridge (channel
    `incident-inc-000001`) - it becomes a task titled `roll back region-b`, owned by the
    speaker, due `in 10 minutes`, carrying what was said. Then say
    `should we drain the queue as well?` - **no task**, because a question is not a
    commitment.
13. The change window from a real deploy hook: issue an ingest key, run the harness's own
    config-directory `deploy.sh --region region-b` (this log does not name that directory,
    because check_clean.sh scans docs/ too - which is the rule itself working). Live
    output:
    `deployment.started announced: {"accepted":1}` → the harness applies v12 (config
    version 1 → 2) → `deployment.completed announced: {"accepted":1}`. Run it again:
    `{"accepted":0,"duplicates":1}`. Then announce a start that never completes, signed the
    same way, and `GET /apps/payments/change-window` reads **`open: true`**, one change in
    flight: *"deployment v14 on payments started 20s ago by pipeline and is still
    running"* - which is exactly the field the app-detail chip renders.
14. `bash scripts/check_clean.sh` green; `gen_rtm`, `gen_test_report`, `gen_stories`,
    `gen_erd` re-run and `gen_openapi.py --check` reporting "up to date";
    `.venv/Scripts/python.exe -m pytest -q` - 874 passed.
15. **The browser walkthrough, recorded (S4 carry-over).** `bash run.sh`, then
    `python scripts/drill_run.py drills/s4-acceptance.yaml`, then a browser. Observed
    live, in order: `/apps/payouts` reading **steady**, health `healthy`, score 100, no
    banner - and the proposal sheet saying plainly that the matrix is not proposing a
    severity while a person may still declare one. `/apps/checkout` at **watch** with the
    S6 banner - *"AI is checking. Something is being observed. Nothing has been decided,
    and no incident exists."* - naming what it is watching. `/apps/payments` at
    **proposed** with the S7 sheet: **P0**, rule *tier-1 journey below 80% in every
    region*, all five corroboration rows ticked (`availability, business`; customer-facing;
    2 of 2 regions; not a storm), four evidence refs, and the **Change window open** chip
    beside it. **Declare P0 as proposed** pressed, landing on `/incidents/{id}`:
    INC-000001, P0, active, the eleven S9 panels, two membership gaps stated
    (`comms_lead`, `executive`), four pages with per-row countdowns, an **Acknowledge**
    button on the commander's own row and none on anybody else's, and the **Live** badge -
    the socket connected, with `POST /api/proxy/auth/ws-ticket` returning 201 in the
    network log. Then a second session as the SME: `/my/pages` showing exactly one page -
    their own - which they acknowledged; and, from that same session, a direct attempt on
    somebody else's page refused **403** while holding no permissions at all.
    The walkthrough found one defect, and it is recorded as S4-C05: the incident commander
    could not open the screen the Declare button is on. That is what a browser run is for.

## Coverage checklist ticks

- S4 Rollups to rollups.db, rolling baselines, sustained-anomaly detection
- S4 SLO fast and slow burn; corroboration across independent families
- S4 Health score with the stated weights, worst region winning; alert-storm clustering
  with suppressed counts
- S4 Lifecycle Steady/Watch/Proposed/Active/Recovering/Closed, human-only declare, severity
  change and close; itsm-lite sync
- S4 Change-window tracking from `deployment.started` without `completed`
- S4 War room on declare; membership by blast radius; paging with acknowledgement;
  per-row countdowns escalating to next-in-rotation; join brief; bridge commitments as
  tasks with owner and due
- S4 Severity proposed from observed impact and never declared by a machine
- S4 Screens S5, S6, S7, S8, S9 (ten panels plus Who is in the room) and S22
- S4 carry-over: the cockpit notification socket now has a screen behind it - the war
  room and the pager both connect over /ws with a socket ticket and say which mode they
  are in. This was the one S3 capability with no screen; it is ticked.
- S4 carry-over: a browser session drove the acceptance journey end to end, and the
  journey is kept as `web/e2e/cockpit.spec.ts`.
- Not ticked: "All 23 artifacts present per 00-INDEX map" stays unticked for the same
  reasons as S3: 08 is a stub, 09/15/19/22/23 are scope stubs owned by S5/S7, and 16 and
  17 do not exist yet.

## Entry criteria for next sprint: met

- **Sprint 4's acceptance is met on all four clauses, without qualification, after the
  carry-over pass.** *Chaos on the harness produces Watch, then a corroborated P0 proposal
  with evidence refs*: the sprint itself reached Watch and then **P2/P3**, because its
  drill broke one link on region-b's route and only one signal family became anomalous -
  the corroboration gate correctly refusing to call one witness two, which was the floor
  working rather than a shortfall. The clause was still unmet, and the fix was the drill,
  not the gate (S4-C02, DR-090). With `drills/s4-acceptance.yaml` the same platform, the
  same matrix and the same gate reach **steady → watch → proposed P0**, corroborated
  across `availability` and `business`, 2 of 2 regions, tier-1 journey, worst success rate
  23.33%, carrying four evidence refs - `rollup:payments-success/region-a`,
  `rollup:payments-success/region-b`, `journey:upi/-` and
  `slo:payments-availability/fast/region-b` - with `requires_human: true` and
  `declared: false`, and `GET /incidents` still empty. Driven both ways: live against the
  running platform and the JVM harness, and deterministically in
  `tests/reference/test_reference_acceptance.py` against the real harness with its traffic
  generator switched off.
  *IC declares in the UI*: **clicked**, by an incident commander, on the S7 proposal sheet
  - after the walkthrough found that no single role could do it and the three reads behind
  that screen were corrected (S4-C05, DR-089).
  *A second browser session (SME) receives the page, non-ack escalates on the visible
  countdown*: driven live as a second authenticated session - the page appeared on that
  person's own pager, a third party and the commander were both refused with 403, and the
  unanswered page escalated to the next rotation member with a fresh countdown.
  *The change-window chip appears from a real deploy hook call*: announced over the frozen
  ingest path with a real HMAC - by the reference config repository's own deploy script
  during the sprint, and by `scripts/drill_run.py` in the carry-over pass -
  `GET /apps/payments/change-window` reading `open: true` with the change in flight, and
  the chip **Change window open** rendered on the app-detail screen in a browser.
- **Exit criteria** (CLAUDE.md's definition of done, applied to the sprint): code plus
  REQ-tagged tests passing - 874 passed, 0 failed, 0 skipped; artifacts touched - 03, 04,
  05, 10, 11 (both halves), 14, 18, 20 and 21, which covers all four the sprint named (04,
  14, 20, 21); the API boots and answers /health 200 with database, event-bus and worker
  (10 jobs) up; `alembic upgrade head` clean to c6b93d70e1af; check_clean.sh green;
  `gen_openapi.py --check` reports the export up to date; every task has an entry in this
  log's Built list. REQ-040 through REQ-046 are built and green in the RTM. Met.
- **Sprint 5's entry criterion is "Sprint 4 accepted", and what S5 needs is standing.**
  The agent suite has an incident to reason about with a stable evidence snapshot copied
  onto it; the four AI panels have their titles, their positions and their sentences
  already on screen, so wiring them changes contents rather than layout; the join brief
  has a `source` field and a caveats section that a model must fill at least as honestly
  as the template does, which is a standard rather than a blank page; the task board gives
  Next Best Action somewhere to put a proposal; kb-index is waiting for the one assignment
  in `deps.Container` that gives it embeddings; and the policy floor - high-risk actions
  can never auto-execute - is already enforced in the domain, so an agent cannot reach
  past it however it is prompted. **The gap S5 was going to inherit - the cockpit's socket
  - is closed** (S4-C01), so S5 starts with a live cockpit rather than a polled one, and
  with a socket protocol whose frames carry no data, which is the shape an agent panel
  wants: the panel asks the authorised route, the socket only says when to ask.
  What S5 still owns, unchanged: the model-written join brief, the four agent panels, the
  model-based commitment harvester, the Incident Brain, the Copilot, Warning and Guidance,
  and topology reasoning. None of them was touched by the carry-over pass, deliberately.
