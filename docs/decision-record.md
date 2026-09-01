# Decision record

Owner: engineering. Two parts: DR-xxx entries record decisions taken during the build,
newest first (format: id / date / decision / why / consequences); the "Imported memory
graph" section at the bottom is the authored pre-build decision record
(signalforge-memory-graph.md, provided and imported 2026-09-01) - every N-line there
is treated as already decided.

## DR-101 (2026-09-01, S5) A reasoning pass is incident command work, not a state-free write
`POST /incidents/{id}/brain/refresh` now carries `Permission.DECLARE_INCIDENT` via
`deps.require_incident`. It shipped carrying no permission, exempted from the API-surface census
under a category named `STATE_FREE_WRITES` on the reasoning that it changes no *incident* state - no
declaration, no severity, no closure. That reasoning does not survive being read carefully. A pass
writes graph nodes and new versions, edges, evidence, hypotheses, confidence-history rows, situation
snapshots, historical guidance and agent-run records, and spends the incident's token budget:
persistent brain state is state, and forming a belief about a live incident and recording it
permanently is exactly the authority the frozen matrix already calls `declare_incident`. Why that
permission rather than a new one: CLAUDE.md rule 3 freezes the roles matrix, `declare_incident` is
already the name for incident command authority (pages, escalations, task assignment carry it), and
minting a permission for one Sprint 5 route would have changed a frozen contract to say something
the matrix already said. Why `require_incident` rather than `require`: it resolves the incident
inside the caller's own tenant *before* evaluating the permission, so another tenant's incident is a
404 rather than a 403 that would confirm the id, and per-app role bindings apply. Consequence: the
reads stay open - `GET /brain`, `/topology`, `/guidance`, `/nodes/{key}` and `POST /brain/ask` carry
no permission, because `ROLE_PERMISSIONS[SME]` is empty and a brain the paged expert cannot open has
failed at its only job - while an SME can no longer cause the platform to reason authoritatively.
The guard runs before the pass, so a denied call writes nothing and reaches no gateway, asserted
against a credentialled provider over a mock socket rather than against the absence of one.
`STATE_FREE_WRITES` keeps its meaning by losing the member that did not fit it, and the cockpit
hides the button rather than offering one that can only answer 403.

## DR-100 (2026-09-01, S5-T03) The Incident Governor is written in-house rather than on LangGraph
`application/brain/governor.py` is a two-phase orchestrator of ~350 lines with an explicit agent
roster, an in-process wake set and a scheduled drain. The sprint plan and N11 both name LangGraph.
Why not: it is not in the virtualenv, and adding it pulls langchain-core and its tree into a
deliberately lean 47-package environment for a pattern this platform has to implement itself
anyway. Everything the governor actually needs - per-incident token budgets, query budgets,
provenance stamping, an execution trace on the tables S2 created for it, idempotent graph merges,
and a degradation contract in which a model failure is a *result* rather than an exception - is
SignalForge behaviour that no orchestration library provides. What would have been left for
LangGraph is a state dict and a call order. Consequence: a deviation from the sprint text, recorded
here and in the sprint log; a governor whose control flow is readable in one file and testable
without a framework; and no third-party code on the path between an incident's evidence and what
the platform believes about it. Revisit if a future sprint needs branching, cyclic or human-in-the-
loop graph execution, where a library would start earning its dependencies.

## DR-099 (2026-09-01, S5-T01) Advisory evidence is capped, so history cannot become causality
`domain/brain/confidence.py` splits net weight into firm and advisory, sums firm in full, and
clamps the advisory total to ±`ADVISORY_CAP` (0.35). Why: the low base weight on
`HISTORICAL_SIMILARITY` (0.45, the lowest in the table) was written to keep history advisory, and it
did not. The first eval case that offered four similar prior incidents scored the hypothesis at 88%
- the platform asserting a cause from history alone, which is the single failure Historical Learning
exists to avoid. A weight per item cannot express "no amount of this is enough"; a cap can.
Consequence: a hypothesis supported by nothing but history stays below even odds however many priors
agree, and cannot be ruled out by history either - the cap is symmetric. One real measurement
outweighs the whole advisory budget, which is the ordering this platform wants. Found by an eval
case, fixed in the policy rather than in the case.

## DR-098 (2026-09-01, S5-T03) The model may promote an unclassified line and may never overturn a classified one
`domain/brain/statements.MODEL_UPGRADABLE = {chatter, question}`. `reconcile()` accepts a model
classification only when the deterministic pass produced one of those two, and otherwise keeps the
rule's answer while recording that the model disagreed. Why: the two passes fail in opposite
directions. When the rules say "commitment" they have matched an explicit promise, and a model
talking them out of it loses a task somebody made on a bridge at 03:00. When they say "chatter" or
"question" they have merely failed to match a pattern - and that is exactly where *"Sam, can you
check the pool?"* lives, an indirect commitment no honest regular expression will ever catch.
Consequence: the model can only add, which makes its worst case a missed promotion rather than a
lost commitment; disagreements are recorded rather than discarded, so a disagreement rate is
measurable later; and the deterministic classifier remains the floor that runs with no key, no
budget and no gateway.

## DR-097 (2026-09-01, S5-T03) An agent returns proposals and holds nothing that can act
Every agent in `application/brain/agents/` is a function from gathered evidence to node and edge
*proposals*. None holds a repository, a unit of work, a bus, an invoker or an HTTP client; the
governor writes and the governor publishes. Why: the honest control against prompt injection is not
a filter. Fencing and a standing system instruction reduce the chance a model is persuaded; they do
not bound the damage if one is. What bounds it is that there is nothing on the other side of the
model call to do - no tool, no write path, no state change. Consequence: idempotency, versioning,
provenance stamping and the audit entry live in one place instead of once per agent; a security test
reads the source of every exported agent and fails if one grows a `self._invoker`; and the sentence
"a model fully persuaded by an injected instruction has nothing to do about it" is a structural
claim rather than a hope.

## DR-096 (2026-09-01, S5-T01) Confidence is arithmetic and the model writes only the sentence beside it
Hypothesis confidence comes from `score()` over authority-scaled weighted evidence; the model is
asked for a narrative whose JSON schema has no number field at all. Why: a model asked "how
confident are you?" answers 83% with identical fluency on three corroborating signals and on none,
and that number then travels onto a screen an incident commander is deciding from. Deriving it
instead makes it reproducible, order-independent after a redelivery, explainable from stored rows
six months later, and arguable - the weight table is a page somebody can disagree with. Consequence:
`ConfidenceEntry.explain()` is written from the stored numbers and says the same thing with or
without a model, so the "why did #2 move from 65% to 32%?" panel works in degraded mode; the
narrative is decoration, labelled as such, and its absence costs nothing.

## DR-095 (2026-09-01, S5-T03) The bus wakes the brain; a job runs it
`IncidentBrainConsumer` records `(tenant, incident)` in an in-process set and returns; a
`ScheduledJob` drains it. Why: a governor pass reads several stores, may call a model over the
network and writes a transaction of its own. Running that inside the dispatcher's transaction would
hold a SQLite write lock for seconds against a loop meant to take milliseconds, and opening a second
connection while the first is held is a deadlock waiting for a busy night. Consequence: waking is
free and redelivery is harmless (a wake is a key in a set); the pass is idempotent anyway, because a
graph write against an unchanged world writes nothing; tests drive `drain()` or `run_for()`
explicitly rather than waiting for a tick; and `brain.*` is deliberately absent from the trigger
patterns, because an agent reacting to its own output is a loop and a budgeted reasoning system must
not be able to feed itself.

## DR-094 (2026-09-01, S4 carry-over) A consumer group is tested through the dispatcher's report
`test_socket_push.py` asserts `DispatchReport.dead_lettered == 0` on every pass, not only that a
frame arrived. Why: found the hard way. `signalforge_socket_nudges_total` was never declared, so
the cockpit consumer delivered its nudge and *then* raised on the metric - the frame arrived, every
assertion passed, and every event dead-lettered. It showed on the running install's DLQ, not in the
suite. Any handler whose side effect lands before its bookkeeping has this shape, so the rule is
general rather than specific to this consumer: a test that only checks the effect cannot tell a
handler that succeeded from one that succeeded and then failed. Consequence: one more assertion per
dispatcher-driven test, calibrated by renaming the metric and watching it fire; and one metric
declared, whose only label is a topic - a closed vocabulary that names no tenant, incident or
person, like every other label on the scrape.

## DR-093 (2026-09-01, S4 carry-over) The bridge reuses the ingest redaction floor, not a second one
`BridgeService` applies `domain/ingest/redaction.mask` on the way out (before the text reaches the
collaboration tool) and on the way in (before a transcript line becomes a commitment's evidence
excerpt). Why: S4 recorded the gap honestly - a responder pasting a card number onto the bridge
would have it stored on a task's `evidence_ref`, in the event stream and in the audit chain,
because the ingest floor only applies to text arriving *through* ingest. The fix had two candidate
shapes and only one of them is safe: a redaction policy specific to the bridge would have been a
second implementation of the same idea, and two masking implementations drift, which means the
weaker one eventually decides what a data-protection incident looks like. Consequence: one policy
id (`ingest-default`), one pattern list, one place to strengthen it; the bridge event says
`redacted: true` and names the policy when something was removed, so evidence can cite it the way
an ingest envelope does. Masking on the way *out* also means the customer's data never reaches the
collaboration vendor, which is a stronger property than the gap report asked for.

## DR-092 (2026-09-01, S4 carry-over) A socket nudge carries no data, so the socket needs no authorisation
The `cockpit-socket` consumer group turns `incident.*` and `warroom.*` events into
`{"type":"room.changed", incident_id, topic, seq}` frames for sockets that asked to watch that
incident. The frame carries no payload; the screen re-reads through the same authorised HTTP route
it was already polling. Why: the alternative - pushing the room down the socket - would have made
the socket a second read path with its own authorisation to get wrong, in a product whose whole
security story is that every read is tenant-scoped at the repository. A nudge cannot leak anything,
so "may this session watch this incident id?" does not have to be answered: watching an incident
this session cannot read produces a 403 on the re-read. Consequence: one extra HTTP round trip per
change, which is the cost of never having two answers to "may I see this"; the fan-out is still
tenant-scoped, because a missing predicate in a fan-out is exactly how cross-tenant leaks happen,
and that is asserted by a test that watches the right id from the wrong tenant.

## DR-091 (2026-09-01, S4 carry-over) The cockpit's socket credential is a ticket, not the session token
`POST /auth/ws-ticket` mints a 30-second, single-use JWT typed `ws_ticket`, bound to the caller's
tenant, user and session; the browser spends it as the `ticket, <value>` subprotocol on the
handshake. Why: DR-087 named the two ways out of the polling gap and rejected the cheap one. The
session token lives in an httpOnly cookie precisely so an XSS cannot read it, and handing it to
JavaScript to buy a refresh interval would have traded a permanent control for a latency
improvement. A ticket is the other way, and every property is part of the credential rather than a
detail: typed, so `JWTAccessTokenService.verify` refuses it and it can reach no HTTP route;
single-use, so a captured handshake replayed a second later is refused; session-bound, so a logout
kills it; thirty seconds, because it exists to survive one handshake and not a session.
Consequence: the httpOnly control is untouched and a leaked ticket is worth almost nothing; the
cost is one authenticated request per connect, and the in-process replay set is per node - correct
for this deployment (one machine, no Docker), and stated rather than hidden, with the ticket's own
lifetime bounding the window even without it.

## DR-090 (2026-09-01, S4 carry-over) The acceptance drill was wrong, so the drill changed and the gate did not
S4's acceptance clause asks for a corroborated P0 proposal; the sprint's live run produced P2/P3
because its drill broke one link on region-b's route and only one signal family ever became
anomalous. `drills/s4-acceptance.yaml` moves the fault to `CORE` - the one link both regions cross
- and takes its second witness from SignalForge's own synthetic probe rather than a second metric
from the same reporter. Why: the alternative was to make the product produce a P0 from one witness,
which would have meant deleting the rule that makes a P0 proposal worth reading. A drill that
cannot legitimately produce the outcome it claims to demonstrate is a broken drill, not evidence of
a broken gate. Consequence: `MIN_INDEPENDENT_FAMILIES`, the customer-facing requirement, the
severity matrix and the human-declaration floor are all untouched; the drill produces P0 on its own
arithmetic - tier-1 journey, 2 of 2 regions, worst success rate under 80 percent - and a
deterministic test drives it against the real JVM harness with the harness's own traffic generator
switched off, so the success ratio is the payments the test placed rather than a sample.

## DR-089 (2026-09-01, S4 carry-over) The proposal sheet's three reads are responder work, not operator work
`GET /apps/{id}/detection`, `/lifecycle` and `/change-window` drop `manage_apps` and become
session-scoped tenant reads. Why: found by the S4 browser walkthrough, which is exactly the class of
defect an HTTP-driven acceptance cannot find because each half worked. The frozen roles matrix gives
`manage_apps` to the org owner alone; the only roles that may declare are incident commander and
tech lead, and neither holds it. So the guard was not narrowing *who may read a proposal* - it was
deciding that nobody who may declare can see one, while the only person who could see one was not
allowed to declare. No single role could complete "the IC declares in the UI", which is a sprint
acceptance clause. This is not a new disclosure: `GET /incidents/{id}` already publishes the same
proposal snapshot - families, health score, evidence refs - to any session in the tenant, because
the SME who answers a page holds nothing at all. Consequence: reading what the platform believes is
now like reading an incident, and every write under `/apps` still carries `manage_apps` - forcing an
evaluation, the raw rollup series, the mapping, the topology, the SLOs, deleting the application.
The narrowing that is genuinely lost is per-application: an operator bound to one application can
now read another's *stance* in the same tenant, though not its telemetry and nothing they can act
on. Pinned as a literal three-route list in two security tests, so a fourth cannot join them
quietly. The web middleware follows: `/apps` is matched exactly and the wizard by suffix, so the
app-detail screen is reachable by the people an incident exists to reach.

## DR-088 (2026-09-01, S4) The unbuilt agent panels ship with their titles and a reason
Next Best Action, AI Copilot, Warning and Guidance and Team Chat render in the S9 shell now,
each with one sentence saying what Sprint 5 will put in it. Why: the ten panel titles are a
vocabulary an incident commander learns, and a layout that rearranges itself when the agents land
would make that learning worthless at exactly the moment it is needed. And a panel that quietly
does not exist is indistinguishable from one that is broken - during an incident, that ambiguity
costs more than an empty panel does. Consequence: the shell publishes titles for behaviour it does
not have, which is only honest because each says so in its own body; a test asserts every pending
panel names what will fill it, so one cannot be left as a blank box.

## DR-087 (2026-09-01, S4) The cockpit polls, because the token is deliberately unreachable
The war room refreshes every five seconds and the pager every ten, through the typed cookie proxy;
there is still no WebSocket client. Why: the session token lives in an httpOnly cookie so browser
JavaScript cannot read it (S1), and a cross-origin WebSocket handshake has nothing else to
authenticate with. The two ways out are handing the token to JavaScript - which would undo the
control that keeps it out of XSS reach, for a refresh interval - or minting a short-lived socket
ticket, which is a credential design in its own right and not something to append to a screens
task. Consequence: the cockpit lags by at most one poll. That is survivable here only because
nothing on these screens is computed in the browser: the countdowns are the server's own numbers,
so the screen can be *stale* but never *wrong*.

## DR-086 (2026-09-01, S4) The ack board ticks the server's countdown, never the browser's clock
`useCountdown` takes `countdown_s` from the API, counts down locally between polls and re-anchors
on every one. `Date.now()`, `Date.parse` and `new Date(` are absent from the hook and from both
screens that use it, asserted by a test that strips comments first so the rule can be explained
where it is enforced. Why: a responder's laptop clock can be minutes off. A browser computing
`deadline - Date.now()` would show two minutes remaining while the escalation sweep - which reads
stored times on the server - was already waking a colleague. The reasonable conclusion from that
is that the pager is broken, and a pager people do not trust is worse than no pager. Consequence:
local drift is bounded by the poll interval and never cumulative, and the number on the screen is
the same number the thing that escalates is acting on.

## DR-085 (2026-09-01, S4) A commitment key is derived from the message, not from who said it
`_key(message_id, title)` deliberately excludes the author. Why: the same message is read by two
paths that legitimately disagree about who said it - the poster knows the session, and a later
sweep sees only the connector's own identity, because the frozen collaboration SPI carries no
author on `post`. Hashing the author in would give one sentence two keys and raise the same
commitment twice, which is the exact failure the key exists to prevent. A message id already
identifies one utterance uniquely, so the author bought no uniqueness in the first place.
Consequence: the two harvest paths compose through one idempotency guard rather than having to
know about each other, and either can be removed without the other double-writing.

## DR-084 (2026-09-01, S4) A commitment said through SignalForge is harvested where authorship is certain
`BridgeService.post` harvests its own message immediately; the background sweep harvests
everything else. Why: the frozen SPI's `post(channel, text, thread_id)` has no author parameter,
so the adapter writes the transcript under the *connector's* identity - correct, because that is
who the far side sees posting, but it means a sweep reading the transcript back cannot tell that
"I'll roll back region-b" was this session. Harvesting at post time is the only place the answer
is known rather than inferred; re-parsing our own `"<name>: <text>"` prefix would be inventing a
protocol on top of a frozen one. Consequence: a commitment becomes a task the moment it is said
rather than up to a sweep later, and what the sweep alone finds - scripted lines, anybody posting
from the far side - genuinely has no SignalForge author and stays unassigned, which is honest.

## DR-083 (2026-09-01, S4) The bridge window opens one second before the room does
`OPENING_SLACK_S = 1`. Why: the frozen collaboration SPI's `fetch(channel, since, limit)` is
exclusive (`sent_at > since`), so a message sent in the same instant the room opened is invisible
to a read starting exactly at `opened_at`. On wall-clock time that is a millisecond-wide race; on
a **virtual clock** it is a certainty - a drill can open a room and play its first scripted line
without the clock moving at all, and the entire transcript would read as silence. Fixed in the war
room rather than in the adapter, because the SPI's `since` semantics are frozen and already have
tests. Consequence: one second of slack, which cannot reach back before the incident existed, and
the boundary is inclusive on every clock.

## DR-082 (2026-09-01, S4) An owner is resolved only when it is unambiguous, and never guessed
A harvested commitment resolves to a user id in exactly three cases: the speaker committed
themselves and the bridge author is a user this tenant holds; the name matches exactly one display
name; the name matches exactly one email local part. Two matches is no match. Why: a room with a
Sam Patel and a Sam Okafor would otherwise get a coin toss, and the losing outcome is paging the
wrong person at 03:00 for work they never agreed to. An unassigned task carrying the name as a
label is fixable in one click; a wrongly-owned one is discovered at the review. The domain also
takes a `not_people` list from the caller - the application id and its topology's service names -
because it cannot know which capitalised words are systems and inventing that list would be the
same guess one level down. Consequence: the board shows unowned work, visibly, rather than work
owned by a guess.

## DR-081 (2026-09-01, S4) An executive is present for a P0 and is not paged for one
`RoleRequirement.paged` is a separate field from membership. Why: being in the room and being woken
are different acts, and a platform that conflates them teaches executives to ignore their
notifications - after which the one page that mattered is ignored too. Consequence: `required`,
`paged` and `present` are three columns on a member row rather than one status. "Four were
required, four were paged, two are here" is the sentence an incident commander needs at minute
ten, and a single status cannot say it.

## DR-080 (2026-09-01, S4) A required role nobody holds is published, not omitted
A `MembershipGap` is written onto the room, published as `warroom.membership.gap` and audited. Why:
a room that quietly rendered a shorter member list would look complete. The failure that prevents
is the expensive one - nobody notices the customer was never told until the post-incident review -
and it is cheap to prevent because the required roles are already computed. Consequence: a war room
can open in a visibly incomplete state, which is correct: it still runs, and somebody can act on
the gap while there is still time.

## DR-079 (2026-09-01, S4) Blast radius walks to dependents, and an unmapped application has none
`downstream_services` walks topology edges backwards - source-depends-on-target means dependents
are found by walking from the broken node to the things that named it. Why: walking forwards would
answer "what does this application need", a different and equally plausible-looking question, and
getting it silently backwards would put the wrong people in the room every time. An application
with no matching node yields an empty radius rather than seeding from every node, because calling
the whole estate a blast radius reads as a catastrophe on every incident and therefore on none.
Consequence: an unmapped application produces a room assembled from severity alone, and both the
room and the brief say so rather than implying the estate is unaffected.

## DR-078 (2026-09-01, S4) The countdown is stored arithmetic, never a timer
A page holds `paged_at` and `ack_deadline_at`; `countdown_s` is computed against a clock the
caller passes in, and the escalation sweep asks the repository for rows whose stored deadline has
passed. Why: a timer held in memory is lost on restart, is wrong on a node that was asleep, and
cannot be made to run at 100x for a drill without drill-aware code. Storing the deadline instead
means the ack board, the sweep and a phone opened ten minutes later all compute the same number
from the same two columns. Consequence: escalation latency is bounded by the sweep's cadence
(15s, configurable with a one-second floor) rather than being exact, which is the right trade for
a countdown measured in minutes.

## DR-077 (2026-09-01, S4) Four session writes carry no permission, because self-scoping is stronger than one
Acknowledging a page, joining the room, speaking on the bridge and completing your own task are
guarded by the entity refusing to act on anybody else's behalf, not by a permission. Why:
`ROLE_PERMISSIONS[Role.SME]` is empty in the frozen matrix, so the person most likely to be paged
holds nothing, and any permission on these paths would exclude exactly the people a war room
exists to reach - the same reasoning `/ws` records as DR-031. The control that replaces it is
strictly tighter: `declare_incident` would let *any* holder acknowledge *anybody's* page, whereas
`Page.acknowledged` refuses everyone but the addressee. Consequence: `SESSION_ONLY_WRITES` in the
security suite grows from one entry to five, and the two lists are joined by a test - a war-room
write added to the exemption without a self-scoping test beside it fails the build. Command work
(paging somebody, putting work on their board, reassigning it) keeps its permission, because those
are decisions about other people's time.

## DR-076 (2026-09-01, S4) A change window is a projection, not a scan of the timeline
`change_windows` is a table keyed on (tenant, app, change_id), filled by a bus consumer group
that folds every `deployment.*` and `change.*` event. Why: "is a change in flight right now" is
asked every time an application screen opens and every time an incident is declared, and the
`EventStore.read` port pages oldest-first from a seq floor - answering it by scanning the log
would make the answer depend on how much unrelated traffic happened to be in the way. Writing
through the dispatcher's own unit of work makes the projection, the idempotency-ledger row and
the consumer offset one commit. Consequence: a second consumer group in the install, and the
fold has to be idempotent and order-independent in its own right (it is: earliest start wins,
completion is sticky), because a projection that could disagree with the events it is derived
from would be worse than the scan it replaced.

## DR-075 (2026-09-01, S4) `recovering` maps to an in-progress ticket, not a resolved one
The ITSM mapping is active -> in_progress, recovering -> in_progress, closed -> closed. Why:
recovering means a mitigation is applied and is being *validated*; resolved is a claim about the
outcome, and the platform has no business making that claim on a person's behalf while the
validation it describes has not finished. It also removes the only backwards move the lifecycle
could produce - a regression from recovering to active leaves the ticket where it is.
Consequence: closing an incident is two ITSM updates rather than one, because an in-progress
ticket cannot jump to closed; `ticket_path` walks the canonical order one step at a time.

## DR-074 (2026-09-01, S4) An ITSM create that timed out is `uncertain`, and the retry reconciles
`TicketSync.state` has five values and the fifth is `uncertain`. A timeout never becomes
`failed`, and the next attempt calls `get` before it calls `create`. Why: a create whose answer
was lost may or may not have made a ticket; retrying it blindly is how an incident ends up with
two tickets and treating it as done is how it ends up with none. This is the actions SPI's
reconcile-before-retry (DR-005) applied to the one other mutating integration this sprint uses.
Consequence: the ITSM retry sweep is a scheduled job rather than an in-request retry, because
the request that declared the incident has already returned - and it must have, since a ticket
queue being down may not delay a P0.

## DR-073 (2026-09-01, S4) Declaring names its application in the query string
`POST /incidents?app_id=…` rather than `POST /apps/{app_id}/incidents`. Why: the security
suite pins every route under `/apps` to `manage_apps`, and declaring is `declare_incident` -
putting it in that tree would have meant weakening a control to accommodate new code. The
existing `deps.require` factory already reads `app_id` from the query string, so a
per-application role binding is still evaluated against the application being declared on.
Consequence: the whole incident surface lives under one prefix, and the two lifecycle *reads*
that are genuinely operator work (`/apps/{app_id}/lifecycle` and `/change-window`) stay in the
`/apps` tree behind `manage_apps` where that test can see them.

## DR-072 (2026-09-01, S4) `deps.require_incident`: a third place an application id comes from
A route named by `{incident_id}` still has to evaluate a per-application role binding, and the
application is not in the URL. The factory resolves the incident inside the caller's tenant
first, then authorizes against the application it names, and publishes
`x-signalforge-app-scope: incident`. Why: without it, an incident commander scoped to one
application could move another application's incident. Consequence: an incident id that exists
in the caller's own tenant is distinguishable from one that does not, before the permission
check - bounded and deliberate, because the lookup is tenant-scoped and reading an incident of
one's own tenant needs no permission anyway.

## DR-071 (2026-09-01, S4) Incident *reads* carry no permission; every write carries one
`GET /incidents` and `GET /incidents/{id}` are session-scoped to the caller's tenant and name no
permission. Why: this is the question DR-067 left open, and the frozen roles matrix answers it -
`ROLE_PERMISSIONS[Role.SME]` is empty, so the person most likely to be paged holds nothing. Any
permission on the read path would exclude exactly the group an incident exists to reach, which
is the same reasoning `/ws` was given in DR-031. Consequence: a member bound to one application
can read incidents of another in the same tenant; the compensating controls are that every write
is permissioned and per-application scoped, and that the security suite pins the read set at
exactly two routes with an empty permission set and every write to its matrix permission.

## DR-070 (2026-09-01, S4) Closure is terminal for people as well as machines
`assert_transition` refuses every move out of `closed`, whoever asks. Why: closure is the record
that a human signed off, and a system that can un-sign it is a system whose record means nothing;
a regression after closure is a new decision by a new person at a new time, and that is what the
record should say. Consequence: there is no "reopen" anywhere in the API. Declaring again on the
same application produces a second incident with its own reference, which is also what makes the
one-open-incident-per-application guard safe to enforce.

## DR-069 (2026-09-01, S4) The lifecycle state machine is domain code with an actor argument
`assert_transition(current, target, by=ActorType)` and `assert_severity_change(state, by=...)`
live in `domain/incidents/lifecycle.py` and raise rather than returning a verdict. Why: "only
humans declare, change severity and close" (REQ-043) had to be a refusal every future caller
meets - a use case, an agent, a drill, a script - rather than a property of which service methods
happen to exist. The one machine move allowed inside an incident is `recovering -> active`,
because escalation is the safe direction and S6's revalidation loop needs it. Consequence: the
`HUMAN_ONLY` set is asserted as a literal in the test suite, so adding a transition without
deciding who may make it fails the build instead of silently granting it to everyone.

## DR-068 (2026-09-01, S4) Removing an application clears detection state through a callback
`ApplicationService` holds a list of `on_removed` hooks that the composition root fills
with `DetectionService.forget`, and they run *after* the removal commits. Why: onboarding
must not import detection (the dependency would point outward), and the hooks write to a
different database, so a derived store that failed to clean up must not roll back the
removal an operator asked for. Consequence: an application that is removed stops being
evaluated on the next sweep, and a failed cleanup leaves orphan rows in `rollups.db` that
the retention sweep removes anyway.

## DR-067 (2026-09-01, S4) Detection reads are `manage_apps`, and the proposal is not yet readable by an IC
The three detection routes live under `/apps/{app_id}` behind `manage_apps`, like every
other route in that tree. Why: they read an application's telemetry and one of them can be
made to run a scrape, which is the same operator job onboarding is; and the boundary test
that pins every `/apps` route to `manage_apps` is a control worth keeping unbroken.
Consequence: the roles matrix gives `manage_apps` to the org owner alone, so an incident
commander cannot yet read the proposal that will carry a Declare button. S4-T02's incident
lifecycle is where a proposal becomes readable by the people who act on it, and it will
have to answer the same question `/ws` answered in DR-031 - SMEs hold no permissions at all.

## DR-066 (2026-09-01, S4) The severity proposal is a value object that cannot be constructed as a declaration
`SeverityProposal.requires_human` defaults to true and `__post_init__` raises if anything
sets it false. Why: "detection proposes, humans declare" was previously a property of which
functions existed; making it a construction-time refusal means it survives serialisation,
a future caller, and a well-meaning refactor. Consequence: the API publishes
`requires_human: true` and `declared: false` as literal fields so a client cannot render a
proposal as an incident, and the rule is held by a test that tries to build one the other
way.

## DR-065 (2026-09-01, S4) Traffic carries no weight in the health score
The weights are availability 30, errors 25, business 20, latency 15, saturation 10, and
traffic is deliberately absent. Why: traffic falling can be an outage or a Sunday, and a
score that could not tell those apart would drift every weekend and be quietly discounted
by everyone reading it. Consequence: traffic is still mapped, rolled up and usable as
corroborating evidence, but it does not score; and because only mapped, measured families
count and the score is renormalised over the weight actually covered, an application with
no saturation metric is not permanently capped at 90 for a signal it does not emit -
`covered_weight` is published beside the score so a 100 out of 45 cannot be misread.

## DR-064 (2026-09-01, S4) Level or rate is derived from the window, not configured
A series that never decreases and increases in at least half its buckets is treated as a
counter and evaluated as a per-second rate; anything else is a level. Why: the frozen
observe contract publishes Prometheus text exposition, which does not say whether a number
accumulates, and the onboarding profile records which *direction* is bad rather than which
shape the number has - so either the operator gains a field to get wrong or the detector
reads the shape. Consequence: `errors_total` rising forever is not an anomaly while
`errors_total` rising four times faster is; and the "half the buckets" half of the rule is
load-bearing, because a *gauge* that sits flat and then steps once is also non-decreasing
and also not flat, and reading that as a counter turns a fifty-fold jump in an error ratio
into a single unremarkable rate.

## DR-063 (2026-09-01, S4) A baseline is held while its signal is breaching
The reference window stops four buckets short of the bucket being judged, and once a breach
is under way the threshold that was in force when it began is reused rather than
recomputed. Why: an anomaly has to last three evaluations, so a rolling baseline that kept
learning would have absorbed the first two breaching samples by the third - widening its
standard deviation by exactly the size of the event it exists to notice - and a long
incident would eventually read as normal. Consequence: an anomaly ends when the signal
recovers, not when the detector gets used to it; `baseline_source` reads `frozen` while
that is happening, so a reader can see which threshold a verdict used.

## DR-062 (2026-09-01, S4) Detection publishes transitions, not a heartbeat
The evaluator runs every five seconds and writes state every time, but publishes only when
something changed: a stance, an anomaly opening or clearing, a signal going stale, a health
band, a burn verdict, a storm's membership, a new proposal. A degraded poll is announced
once per outage rather than once per tick. Why: a five-second loop that published a verdict
each time would put seventeen thousand events a day per application onto a timeline meant
to be read by a person. Consequence: "changed since when" has to survive a restart, which
is what `detection_snapshots` is for - one row per application holding the few fields a
transition is decided from, and losing it costs one duplicate announcement.

## DR-061 (2026-09-01, S4) A five-second bucket is one observation, so the write is insert-or-duplicate
`metric_rollups` is unique on (tenant, app, signal, region, bucket_start) and a second
write into an existing bucket is counted as a duplicate rather than merged. Why: the poller
is a loop on a timer, so an overlapping tick, a restart mid-sweep or an operator forcing an
evaluation can all land two polls in one bucket - and an upsert that averaged them would
let a *retry* move the baseline the detector is about to compare against, which is a
duplicate that hides an incident. Consequence: the poll report distinguishes `written` from
`duplicates` so the behaviour is observable rather than silent, and the uniqueness is
enforced twice (a lookup, then the constraint inside a savepoint) so a concurrent writer is
counted rather than raising.

## DR-060 (2026-09-01, S4) Rollups live in a second database, created from models rather than migrated
`rollups.db` has its own `DeclarativeBase`, its own engine (with `NullPool`) and its own
connection string, and its schema is created at startup by `create_rollup_schema` instead
of by an Alembic revision. Why: appending to the audit hash chain reads the previous tip
inside the same transaction, and putting a five-second telemetry loop on that transaction
would make the chain the contended resource in the system; the contents are derived and
rebuilt by letting the poller run, so pruning hourly is safe and a migration whose only
honest recovery is "drop and refill" would be ceremony rather than safety; and the two
stores have different lifetimes, which is how the wrong thing eventually gets deleted when
they share a file. Consequence: `scripts/gen_erd.py` reads both metadata objects and
docs/10 documents two databases; `NullPool` is used because an aiosqlite connection belongs
to the loop that opened it and this store is written from the worker group, from a request
handler and from tests.

## DR-059 (2026-09-01, S3) The route catalogue is spliced into docs/11, not kept beside it
`scripts/gen_openapi.py` rewrites the region between two markers inside
`docs/11-api-spec.md` rather than writing a second generated file the prose links to.
Why: the catalogue is only useful next to the conventions that explain it - which
credential, which permission, which of two places `app_id` is read from - and a reader
who has to open a second document to find out whether a route is guarded will not.
Consequence: the artifact is one file with an authored half and a generated half, and
the generator refuses to run if the markers are missing rather than appending a second
copy. Regeneration is byte-stable (no timestamp inside the block), so a diff in docs/11
means the API changed.

## DR-058 (2026-09-01, S3) The RBAC factories declare their permission as an attribute
`deps.require` and `deps.require_app` now set `required_permission` and `app_id_source`
on the dependency they return, in addition to closing over the permission. Why: the
security suite reads the permission out of the closure, which is right for a test - it
proves the guard itself - but product code that introspected `__closure__` to publish a
document would be reading an implementation detail of CPython. Consequence: one declared
attribute serves the published document, the closure still serves the tests, and the two
cannot disagree because they come from the same call. No behaviour changed: nothing reads
the attribute at request time.

## DR-057 (2026-09-01, S3) The published document names the permission each route enforces
`/openapi.json` is unauthenticated, and it now states, per operation, the credential and
the permission required. Why: the alternative is a document that describes shapes and
hides the access model, which pushes every integrator into trial and error against a
production API and makes an unguarded route invisible to review. The information is not
secret - the roles matrix is served at `/roles/matrix` to any member and is published in
docs - and knowing that `POST /apps` wants `manage_apps` does not help anybody obtain it.
Consequence: the set of routes reachable with no credential became a closed list a
security test pins (six), an ingest key is pinned to the five frozen paths, and a write
route shipped without a permission fails the build. The residual risk accepted: an
anonymous caller can enumerate the API's shape and its access model, which was already
true of the shapes.

## DR-056 (2026-09-01, S3) Artifact 11 is generated from the running application
`docs/11-openapi.json` is produced by `signalforge/api/openapi.py` from the live route
table and is the same document `GET /openapi.json` serves; the prose in
`docs/11-api-spec.md` is authored, and a test fails when the export is not what the
application would serve today. Why: an API specification maintained by hand is wrong the
first time somebody adds a route, and the fields that matter most to a caller - what
credential, what permission - are exactly the ones nobody remembers to update.
Consequence: `make openapi` joins `make rtm`, `make report` and `make erd` as a step after
any change to a route, a schema or a guard; `--check` fails without writing, for a hook.
The cost is one more generated artifact to regenerate; the benefit is that the document
cannot describe an API that is not being served.

## DR-055 (2026-09-01, S3) The reference harness announces deployments with a derived event id
`deploy.sh` computes `event_id` as a UUIDv5-shaped digest of (release, region, status)
rather than generating one. Why: pipelines are re-triggered constantly, and a
re-trigger that minted a new id would put a second deployment on the timeline that
nobody performed - which corrupts the one question changes are recorded to answer.
Consequence: a re-run is acknowledged as `duplicates: 1` and publishes nothing, and
announcing a genuinely new deployment requires a new release or region. The
application enforces the mirror rule from its own side: re-applying the same release
with the same settings answers `changed: false` instead of creating a version.

## DR-054 (2026-09-01, S3) A failure to announce never fails a deployment
If the platform is unreachable, refuses the credential, or has no credential
configured, `deploy.sh` warns on stderr, applies the change and exits 0 saying the
deployment went unannounced. Why: monitoring does not get to gate shipping, and a
pipeline that stops on a telemetry outage teaches application teams to remove the
telemetry. Consequence: the platform can miss a deployment it was never told about,
which is visible as a gap rather than as a wrong answer. The one reversal is a failed
*apply*, announced as `deployment.failed` before the script exits non-zero, because an
incident opened shortly afterwards is very likely about it.

## DR-053 (2026-09-01, S3) The harness's manifests are documents, and drift is a startup failure
The action and chaos manifests are JSON resources the harness serves verbatim, and the
application refuses to start when a published id has no implementation or an
implementation is not published. Why: the architecture sketched an annotation scan, but
a generated manifest cannot be wrong, which means it also cannot be reviewed - and the
manifest is exactly the document a change board should read before an action becomes
executable. Consequence: adding an action is a two-file change by construction, and the
suite asserts the same agreement by parsing both sides so drift fails a test rather
than a demo.

## DR-052 (2026-09-01, S3) The harness renders its own Prometheus exposition
No Micrometer, despite the architecture's stack table naming it. Why: the platform's
parser refuses a malformed exposition line rather than dropping it, so the harness has
to control its output format exactly rather than inherit a library's defaults; and a
registry that requires every metric to declare its label names is what keeps a payment
id out of a metric label - the same rule the platform applies to its own scrape.
Consequence: one fewer dependency and about 150 lines of harness code, and the harness's
metric names and label sets are readable in one file.

## DR-051 (2026-09-01, S3) A payment lost at the settlement rail is UNKNOWN, not FAILED
The harness's ledger has five payment states and the fifth is `UNKNOWN`. Why: a request
that reached the point where money moves and lost its answer has not failed - calling it
failed is a guess, and a caller told "failed" retries and pays twice. Consequence: the
harness can demonstrate uncertain external state, reconcile-before-retry and duplicate
prevention against a real condition rather than a simulated one, and its reconciliation
action has something real to resolve. A fault at the rail affects both regions by
construction, because a payments business cannot route around the money.

## DR-050 (2026-09-01, S3) The harness holds four credentials, not one
Read-only, remediation, drill and deploy, each refused on any path that does not need
it: telemetry is readable by all four, actions need remediation, chaos needs drill and
the deploy endpoint needs deploy. Why: the platform's credential-scope floor is only a
control if the far end enforces it too; with one shared key the floor would be a policy
this platform applies to itself. Consequence: a read-only binding attempting a rollback
is refused twice, and the platform is never given a way to deploy while the pipeline is
never given a way to read telemetry.

## DR-049 (2026-09-01, S3) run.sh, reset.sh and .gitignore find harnesses by glob
The launcher iterates `reference-app/*/run.sh` and the ignore rules use
`reference-app/*/target/`. Why: those three files are core, ground rule 2 forbids the
reference application's name in core, and `check_clean.sh` caught the first version that
named it. Consequence: adding a second harness needs no change to any of the three, and
deleting `reference-app/` leaves all three working - which is the same property rule 7
asks of demo assets.

## DR-048 (2026-09-01, S3) The cockpit's types are pinned to the API's response models
Three tests read `ConnectorHealthOut`, `IngestKeyOut` and `ReadinessOut` field by
field and require each name in the corresponding `api.ts`. Why: the S2 timeline did
this against the frozen envelope and it caught real drift; the wizard consumes far
more of the API than the timeline did, and a field the platform starts returning
that no screen decodes is invisible rather than broken. Consequence: adding a field
to one of those three models is a two-file change by construction. The pinning is
deliberately one-directional - the cockpit may hold view-only fields the API does
not have (a formatted label), it may not silently ignore one the API does.

## DR-047 (2026-09-01, S3) Ingest keys with no binding get their own rows on screen 23
`GET /connectors/health` joins only the keys that name a binding. The integration
health screen additionally reads `GET /ingest-keys` and renders the unbound ones as
rows of their own. Why: DR-034 made `binding_id` optional because the frozen SPI
defines `normalize` only on alerts, changes and topology - collaboration and itsm
can never have a push adapter - so for two of the five frozen streams the key *is*
the whole integration. Joining them onto a binding was impossible and dropping them
would leave the one screen meant to show every integration silently missing two
kinds. Consequence: the screen makes two reads rather than one, and de-duplicates by
`binding_id` so a bound key is not listed twice.

## DR-046 (2026-09-01, S3) A wizard step re-reads the application rather than patching local state
Every step writes its own section through its own endpoint and then reloads the
whole application, profile and readiness. Why: the domain can refuse or adjust what
a step sent - the policy floor rejects an edit, a topology upload turns out to be
`unchanged`, a re-import raises a risk above an operator's override - so the truth
after a write is what the platform now holds, not what the form believed it sent. A
wizard that patched its own state optimistically would show a policy value the floor
had refused. Consequence: a step costs four reads after each save, which is right at
wizard cadence and is the obvious thing to narrow if a step ever writes on keystroke.

## DR-045 (2026-09-01, S3) The push side of a health row is joined from the ingest keys
`GET /connectors/health` gained `last_event_at`, `event_freshness_s`, `ingest_keys`,
`ingest_paths`, three counters and `ingest_last_error`, read from the live keys whose
`binding_id` names the binding. Why: S3-T02, S3-T03 and S3-T04 each closed with the
same open item - a push binding cannot be measured by pinging, because SignalForge
is the server and is reachable whether or not the sender died a week ago, so the row
read UP for a silent integration. The number that answers the question lives on the
key. Consequence: revoked keys are deliberately excluded, so revoking a credential
takes the integration off the row on the next read rather than leaving a stale green
arrival behind it; and the endpoint makes two queries instead of one, grouped in
memory, so the screen costs two reads however many integrations a tenant holds.

## DR-044 (2026-09-01, S3) A connection test writes its verdict onto the binding
The wizard's explicit test updates the binding's health columns - status, detail,
last_ok_at, consecutive_failures - exactly as the 30-second health loop does. Why:
S3-T01 recorded that a binding created between two polls reads `unknown` with no
detail until the next one, and an operator who has just pressed "Test" and seen a
green result should not then look at the integration health screen and see grey.
Consequence: the two writers share the domain's transition methods, so a manual
test and a poll cannot disagree about what DEGRADED means; three failed manual
tests in a row read DOWN, which is correct and was not designed for separately.

## DR-043 (2026-09-01, S3) Readiness is thirteen fixed weights and five blocking checks
The score sums to exactly 100, asserted by a test, and `ready` requires every
blocking check to pass as well as the score clearing 80. Why: a percentage that
drifts as checks are added is not comparable between two applications, and a
score alone would let an application read 85% "ready" with its only metrics
connector failing. Consequence: adding a check means re-weighting the others -
deliberately friction - and `connect.healthy` is blocking, so an application whose
integrations are not answering is never ready however complete the wizard is.

## DR-042 (2026-09-01, S3) Auto-discovery matches adapters by kind and vendor, never by id
Observe endpoints are probed from the frozen `OBSERVE_PATHS` and matched to
adapters by `kind`; the two well-known local services are matched by the `vendor`
their manifests declare. Why: `infrastructure/adapters/builtin.py` is the only
file in the platform that names an adapter, and discovery living in the
application layer must not become the second. Consequence: a deployment that
installs a different Prometheus adapter gets it suggested with no code change; the
cost is that a vendor string is now load-bearing, which is documented in the
adapters README. A 401 or 403 counts as *found*, because discovery is
unauthenticated and that is the correct answer from a protected observe surface.

## DR-041 (2026-09-01, S3) A push connector's test reads freshness, not reachability
Testing a push binding calls nothing. It reports whether an ingest key exists and
when something last arrived, and reads PENDING - not green, not red - until real
traffic does. Why: SignalForge is the server for a push binding, so an outbound
test would always pass even if the sender died a week ago. Consequence: the wizard
has a fourth outcome (PENDING) beside passed/failed/skipped, and the test page's
"all green" excludes pending, so an application is not "ready" on the strength of
a push integration nobody has ever posted to.

## DR-040 (2026-09-01, S3) An imported action keeps the manifest's declaration beside the override
`MappedAction` stores `manifest_risk` and `risk_override` separately and forever,
and a re-import refreshes the manifest side while preserving the operator's
decision. Why: "the application said this was low risk and we raised it" is a fact
an audit will want, and overwriting the override on every manifest redeploy would
silently undo a risk decision. Consequence: an action reclassified by the
application into a high-risk class loses an existing `auto` setting rather than
carrying it in, and an override that would now *lower* the declared risk is
dropped rather than applied.

## DR-039 (2026-09-01, S3) Topology versions by content, and a re-upload is a no-op
An uploaded map's version is its own when declared and a content fingerprint
otherwise; a version that already exists produces no row, no event and no profile
change. Why: an operator who is unsure the upload worked will upload again, and a
wizard that answered "v2 published" to identical content would make "has the map
changed since the incident started" unanswerable - the question blast radius
depends on. Consequence: capture time and node order deliberately do not
participate in the fingerprint, so re-capturing an unchanged estate is not a
change; and a document that fails to normalise is the *caller's* 422 rather than a
502, because `normalize` touches no integrated system.

## DR-038 (2026-09-01, S3) A second RBAC factory for path-named applications
`deps.require_app` reads `app_id` from the request's path parameters;
`deps.require` keeps reading it from the query string. Why: the wizard names the
application in the path, and FastAPI resolves a dependency parameter declared as a
query parameter as a query parameter even when the route has a path parameter of
that name - so one factory could not serve both without dropping the documented
query parameter from twenty existing routes. Consequence: an operator whose
manage_apps binding is scoped to one application is evaluated against that
application on the wizard routes instead of org-wide, which grants access that was
previously (wrongly) denied and denies nothing that was previously allowed; both
directions are covered by tests.

## DR-037 (2026-09-01, S3) A batch is one transaction, and a rejected item does not consume its id
Receipts, the events they became, the counters on the key and any audit entry
commit together; per-item schema failures are reported in the 202 rather than
failing the batch. Why: a receipt written without its event would silently
swallow the sender's retry, which is the exact failure idempotency exists to
prevent; and one malformed alert in five hundred must not lose the other four
hundred and ninety-nine. Consequence: a rejected item writes no receipt, so the
sender fixes the payload and resends with the same `event_id` and that is an
acceptance, not a duplicate - and a duplicate is checked *before* validation, so
a retry of a batch we already recorded gets the same answer every time even if
the item would also fail validation. Duplicates and item rejections are audited
once per batch, capped at twenty named details, because the chain is permanent
and a retrying sender would otherwise write its whole batch into it repeatedly.

## DR-036 (2026-09-01, S3) The ingest redaction floor lives in the domain, not in a manifest
Free text arriving on the `messages` and `tickets` streams is masked at the
boundary by `domain/ingest/redaction.py` under policy id `ingest-default`. Why:
S3-T02 established that redaction happens at write time so a later reader of the
row cannot bypass it, but the frozen SPI defines `normalize` only on alerts,
changes and topology - so collaboration and itsm can never have a push adapter,
and there is no manifest to read a policy from on precisely the two streams that
carry the most human prose. Consequence: the floor is small and deterministic
(email addresses, long digit runs) and applies to message text, ticket title and
description, and alert annotations; `redaction.applied` on the envelope is set
only when something was actually removed, so the flag means a removal rather than
the existence of a policy.

## DR-035 (2026-09-01, S3) Ingest keys are per stream, and rotation issues a successor
An ingest key is bound to one tenant, one app_id and one stream, and
`POST /ingest-keys/{id}/rotate` creates a new key rather than replacing the
credential on the existing one. Why: a token that leaks from a deploy script must
not be usable to inject alerts; and a sender cannot be switched atomically -
changing it is a configuration change in somebody else's system - so overwriting
would drop every request in flight between two deploys. Consequence: two keys
with two secrets are live during a handover, which is what the frozen contract's
optional `X-SignalForge-Key-Id` header is for: it names which key the sender
believes it signed with, and a mismatch is refused rather than quietly verified
against the other secret. Revoking destroys the key's secret, so a revoked key
cannot produce a signature that would verify.

## DR-034 (2026-09-01, S3) The ingest endpoint is canonical first; the adapter is an optional translator
`/api/v1/ingest/*` validates against `contracts/canonical.py`, and only when that
fails does the key's push connector binding get a chance to `normalize` a
vendor-shaped payload. Why: the frozen contract types `IngestItem.payload` as a
canonical model, so an application implementing the public contracts is
ingestible with no platform code written for it - and requiring a push binding
would make two of the five frozen paths unreachable, because the frozen SPI
defines `normalize` only on alerts, changes and topology and the manifest floor
requires a push adapter to declare it. Consequence: `binding_id` on an ingest key
is optional; when it is set it is validated at issue time (right kind, push mode,
declares `normalize`) so the operator finds a mis-wiring while wiring it; the
adapter is resolved once per batch, not once per item; and a disabled binding or
a refused payload rejects only the items that needed translating.

## DR-033 (2026-09-01, S3) Ingest authenticates a machine, and the sender is told nothing
The five ingest routes resolve an ingest key - bearer token plus HMAC over the
body inside the replay window - and carry no Permission, and every refusal
answers a bare 401. Why: the roles matrix describes people, and a deploy script
is not one, so giving these routes a permission would be a fiction; and a caller
that could distinguish "unknown token" from "bad signature" from "clock skew" has
an oracle. Consequence: the security suite's router walk was widened again, this
time to accept a dependency marked `resolves_ingest_credential`, so the rule
"no handler runs before a real, revocable credential is resolved" still holds
across the whole surface. The reason for a refusal is written to the key row and
readable on `GET /ingest-keys` behind manage_apps, and lands in that tenant's
audit chain and timeline; a refusal against an *unknown* token audits nothing,
because it names no tenant and letting an anonymous caller choose whose chain to
write to would be the leak rather than the fix. Rate limiting sits before the
signature check and is deliberately not audited, so a flood cannot write the disk
it was refused from touching.

## DR-032 (2026-09-01, S3) The durable receipt, not the socket, is the record of a notification
Every notification adapter writes a `notification_deliveries` row before it
touches a transport, and "nobody is connected" is QUEUED rather than failed. Why:
a page whose delivery succeeded and whose record did not is a page nobody can
prove happened, and an escalation timer built on the transport would silence
itself on a lost frame. Consequence: `/ws` hands a connecting session everything
still awaiting acknowledgement, so a notification produced at 03:00 with nobody
looking is early rather than lost; a subscriber whose queue is full is not
counted as delivered either, so back pressure on one browser cannot be mistaken
for a page.

## DR-031 (2026-09-01, S3) /ws authenticates but takes no permission
The notification socket is the only product route with no permission check. Why:
the people who most need to be paged - SMEs - hold no permissions in the frozen
roles matrix, so any permission would exclude exactly the wrong group. The
compensating control is structural rather than policy: the principal comes from
the session, the subscription is keyed on that principal's tenant and user id,
and catch-up reads that recipient's own rows, so there is no parameter through
which a caller could ask for somebody else's. Consequence: the security suite's
router walk was widened to accept `get_ws_principal` alongside `get_principal`,
and two tenant-isolation tests pin the socket's behaviour; the token may arrive
as the `bearer, <token>` subprotocol (a browser cannot set a header on a
handshake) but never as a query parameter, which would put a live session token
into every access log on the path.

## DR-030 (2026-09-01, S3) The invoker's timeout is a watchdog, not the call limit
`ConnectorInvoker` bounds a capability at `3 x timeout_s + 1s` while the HTTP
transport bounds each call at the manifest's `timeout_s`. Why: one capability is
not one call - `execute` asks whether the idempotency key already ran before it
sends anything - so a watchdog equal to a single call's timeout would fire during
the second call every time, and worse, would fire before the adapter could turn
its own timeout into an UNCERTAIN outcome, reporting a timed-out remediation as a
platform error and inviting a retry. Consequence: the per-call limit is where the
real bound lives; the watchdog only catches a runtime that hangs with no timeout
of its own, and a caller may tighten it but never widen it.

## DR-029 (2026-09-01, S3) A timed-out action execution is UNCERTAIN, not failed
`http-observe-actions.execute` returns `ExecutionState.UNCERTAIN` with a derived,
stable execution id when the call times out or the target becomes unreachable,
and raises only when the target answered and refused (auth, 4xx). Why: after a
timeout the change may already have happened, and "failed" is a claim the adapter
cannot support. Consequence: `get_execution` is the reconcile-before-retry
primitive S6 builds on, and duplicate suppression is a lookup before the send
rather than a lock.

## DR-028 (2026-09-01, S3) The collaboration script is materialised, not streamed
teams-sim writes a whole scripted conversation as rows at future `sent_at` when a
channel is first touched, and reads window by time. Why: three properties fall
out for free that a timer-driven player has to build - it follows whatever clock
the platform holds, so a drill at 100x plays the conversation at 100x with no
drill-aware code in the adapter; it is idempotent, because message ids derive
from the channel and the line's position; and it survives a restart, because a
schedule made of rows has nothing to lose. Consequence: `sim_messages.sent_at`
may be in the future, and the read's upper bound is what hides a line until it is
due; a script should start at `after_s: 0`, since the channel's epoch is its
earliest message.

## DR-027 (2026-09-01, S3) A topology document imports whole or not at all
`topo-import` rejects a document whose edges name undeclared nodes, naming the
offending edges, rather than dropping them. Why: topology is what blast radius is
computed from, and a graph missing some edges looks complete while understating
impact - which is worse than having no graph. Consequence: an over-large graph is
refused rather than truncated for the same reason, and a document with no
declared version gets a content fingerprint so re-importing an unchanged map is
not a change.

## DR-026 (2026-09-01, S3) kb-index ranks lexically first and semantically second
BM25 always runs; embeddings are blended only when a provider is installed and
reachable, and a provider failure at index time or query time degrades ranking
quality without failing the search. Why: the same discipline REQ-051 puts on the
LLM provider - the deterministic path is the product and the model is the
improvement - applied one layer down. Consequence: documents indexed while a
provider was down carry no vector and are still found; `use_embeddings: false`
pins ranking for a drill; redaction is applied at index time, because a policy
applied on read could be bypassed by a later reader of the row.

## DR-025 (2026-09-01, S3) The shipped adapters get their own unit of work
`IntegrationUnitOfWork` (tickets, messages, deliveries, knowledge) is separate
from `IdentityUnitOfWork`. Why: those four tables hold the *integrated system's*
data rather than SignalForge's own state, and an adapter call is not a use case -
it must not be able to commit a transaction that also contains role bindings.
Consequence: `notification_deliveries` is reachable from both, because
acknowledging a page is a human act whose receipt, audit entry and event have to
be one commit; the adapters write receipts through their own unit of work and the
inbox acknowledges through the identity one.

## DR-024 (2026-09-01, S3) The capability matrix is a declaration view, not a runtime view
Coverage per kind is computed from the manifests' `availability`, so an
interface-only adapter is listed and never counted, while a `built_in` manifest
whose runtime is missing still counts. Why: the matrix answers "what can this
installation's adapter set do", which is a property of the adapters, and keeping
it a pure function of manifests keeps it in the domain with no knowledge of what
a particular node happens to have loaded. The runtime question is answered
separately and per node by the registry listing's `runnable`/`status`.
Consequence: a packaging fault (manifest without runtime) shows as "runtime
missing" in the listing rather than as a coverage gap, and the two views are
documented as different questions.

## DR-023 (2026-09-01, S3) Connector health is polled through a provider, not a fixed list
ConnectorHealthService gained register_provider: an async callable re-asked on
every poll. Static registration was enough for the database, the bus and the
worker, which exist for the process's lifetime; bindings do not. Why: a binding
created, disabled or deleted while the process runs has to join or leave the
loop without a restart, and "an operator turned it off" must mean it stops being
polled now. Consequence: one extra database read per poll cycle, and a provider
that raises is logged and skipped rather than costing the platform its own
verdict.

## DR-022 (2026-09-01, S3) A HealthSource knows which tenant it belongs to
HealthSource gained tenant_id, app_id and metric_label; a null tenant means the
platform itself. Why: one poll loop now produces both this node's readiness and
every customer's connector health, and three different rules have to follow from
one fact. Readiness (/health) counts platform sources only, so a customer's dead
integration never takes a node out of rotation. Visibility (/platform/status)
filters to the asking tenant. Metrics key on metric_label - the adapter id, not
the binding id - because /metrics has no session, and several bindings of one
adapter collapse to their worst verdict. Consequence: platform_report() was
split from report(), and signalforge_connector_bindings{state} was added so the
scrape can count bindings without naming one.

## DR-021 (2026-09-01, S3) A binding with no runtime is UNKNOWN, never UP or DOWN
An adapter whose manifest is installed but whose runtime is not - the honest
state of every enterprise connector in this deployment - reports UNKNOWN with
the reason, and the integration health screen shows that rather than a green
tick or a red cross. Why: nothing was measured, and both a tick and a cross
claim a measurement. Overall status already treats UNKNOWN as degraded rather
than healthy (S2), so an unmeasurable connector cannot be mistaken for a
working one. Consequence: the health table has three failure shades - terminal
(auth rejected, orphaned binding, missing credential) is DOWN at once, transient
(timeout, unreachable, ping false) is DEGRADED until the third consecutive
failure, and unmeasurable is UNKNOWN.

## DR-020 (2026-09-01, S3) Credential scope is part of the binding's identity
Bindings are unique on (tenant, app, adapter, credential_scope), so read-only
and remediation for the same adapter are two rows with two secrets. Why the
architecture already said separate keys: they have different blast radii, and a
single row with a scope field would have made rotating or revoking one touch the
other. Consequence: the wizard creates one binding per scope, the floor
"read-only can never drive remediation" is structural rather than a check, and
deleting a binding deletes exactly its own secrets because the binding id is in
the secret name.

## DR-019 (2026-09-01, S3) The capability catalogue is read off the SPI, not written down
A manifest's `capabilities` are validated against the abstract methods of that
kind's SPI class, reflected at import time. Why: the alternative is a second
list beside contracts/spi.py that drifts the first time a kind gains a method,
and a manifest promising a capability with no SPI name fails at 3am with an
AttributeError instead of at load with a sentence. Consequence: adding an SPI
method makes it declarable on the same commit; `ping` is excluded because every
adapter must answer it and the health loop depends on that being unconditional.

## DR-018 (2026-09-01, S2) Requeue is a state transition, not a dispatcher call
Requeueing a dead letter marks it for redelivery and audits the operator's
intent; whichever dispatcher owns that consumer group drains it. The first
implementation put requeue on Dispatcher and refused when the group was not
registered there. Why changed: the architecture allows splitting api and worker
into two processes, and on an api-only node the API owns no consumer groups at
all, so requeue would have been impossible in a deployment shape we ship for.
Consequence: DeadLetterService owns list and requeue; the API reports
drained_here so an operator is told when no local worker will pick it up, and
the UI surfaces that instead of pretending it moved.

## DR-017 (2026-09-01, S2) /health and /metrics are unauthenticated, and cannot name a tenant
A liveness probe has no session and a Prometheus scrape has no token, so both
stay public on the loopback bind. The compensating control is capability, not
policy: /health returns component statuses only, and the metrics registry
rejects tenant, incident, user and org label names at declaration time, so a
metric that could identify a customer cannot be declared. Both are asserted in
the security suite alongside the router-coverage walk. Consequence: the public
route list in test_authz_matrix grew by two entries, each justified there;
anything an operator needs that does name a tenant lives on /platform/status
behind manage_apps.

## DR-016 (2026-09-01, S2) Platform-scoped events use a reserved _platform tenant
Events about the platform itself - connector health, and later adapter
lifecycle - are published under tenant id "_platform" rather than duplicated per
customer or given a nullable tenant. Why: the envelope contract requires a
tenant, replay is tenant-scoped, and a reserved id means no customer can read
platform internals through their own timeline without a single special case in
the query path. Consequence: operators read them through /platform/status and
/metrics; if a per-tenant view of adapter health is wanted in S3, adapters
publish under the real tenant because they are bound to one.

## DR-015 (2026-09-01, S2) Sealing is close_incident work; replay is read_audit
The frozen roles matrix makes Auditor strictly read-only, so sealing - which
writes a permanent record - cannot hang off read_audit. It hangs off
close_incident, which matches when a seal is actually taken: at the end of an
incident. Replay, the audit surface, hangs off read_audit. Why: keeping the
frozen matrix honest beats inventing a permission. Consequence: an Auditor can
verify any seal but never create one; the S4 incident lifecycle calls the same
seal service when an incident closes.

## DR-014 (2026-09-01, S2) The hash chain is per (tenant, plane), anchored on prune
Signal-plane events age out; incident-plane events are kept. One chain across
both would be broken by the first retention prune, so chains are per (tenant,
plane) and a prune records the payload hash of the last event it removed.
Verification of the survivors starts from that anchor. Why: without it a prune
and a deletion are indistinguishable, which would make the whole chain
unfalsifiable. Consequence: retention_watermarks is part of the audit evidence,
not bookkeeping; seals bind (seq, event_id, payload_hash, prev_hash) so position
and identity are covered, not only content.

## DR-013 (2026-09-01, S2) Two clocks in the container, wall time for anything durable
wall_clock drives token expiry, refresh windows, rate limits, recorded_at,
retention cutoffs and offsets; clock is the switchable platform clock a drill can
point at scenario time, and only occurred_at follows it. Why: a drill running at
100000x would otherwise expire every session and delete a week of real telemetry,
both of which are now asserted by test. Consequence: an injected clock (tests,
drills) is used for both, which is what lets a frozen clock still prove an expiry;
S7's drill engine switches only the platform clock.

## DR-012 (2026-09-01, S2) Lane count is a stored on-disk constant, not config
Lanes come from blake2b(partition_key) % 16, computed at write time and stored on
the row. Why: the mapping must be identical across processes and restarts, and
Python's hash() is salted per process - two workers disagreeing about a lane is
silent reordering rather than a visible error. Consequence: changing LANE_COUNT
re-shards the bus and requires a migration that recomputes every lane value; the
constant lives in the domain, not in Settings.

## DR-011 (2026-09-01, S1) Escalation test-fire is the IC-only demo endpoint
S1's acceptance needs an Executive denied an "IC-only endpoint", but incident
endpoints only land in S4. The simulated escalation test-fire (POST
/escalation/policies/{id}/test) is gated on run_drills - held by incident
commander and org owner, never Executive - and is a genuine S1 feature (verify
paging order and timers before a real incident, nobody actually paged, audit
event emitted). Why: an honest permission-gated endpoint beats a placeholder;
no stubs in product code. Consequence: the S4 incident routers add
declare/severity/close gates on top; this endpoint stays.

## DR-010 (2026-09-01, S1) Dev key fallback under var/keys/, env always wins
SIGNALFORGE_JWT_SECRET and SIGNALFORGE_FERNET_KEY come from the environment;
on the single dev machine, absent vars are generated once and persisted under
var/keys/ (gitignored) so restarts keep sessions and secrets decryptable. Why:
run.sh must boot with zero setup, and an ephemeral key would silently invalidate
every session and stored secret on restart. Consequence: production posture
requires the env vars; recorded as a residual risk in docs/12 section 4.

## DR-009 (2026-09-01) Imported docs genericize the reference app's name
The authored architecture and memory graph were imported into docs/06 and this file
with the reference app's name replaced by generic phrasing. Why: CLAUDE.md rule 2
(enforced by check_clean.sh) allows that string only in reference-app/, drills/,
seeds/ and tests; a verbatim import would fail the pre-commit guard. Consequence:
content is otherwise faithful; the originals remain in playbook/ (ungoverned by the
guard) for exact reference.

## DR-008 (2026-09-01) Stories and RTM are generated views
docs/03 and docs/20 are rendered by scripts from specs/requirements.yaml plus recorded
test results. Why: one source of truth; handwritten copies drift. Consequence: editing
those docs by hand is prohibited; regenerate instead.

## DR-007 (2026-09-01) 51 requirements instead of ~40
Granularity chosen so each sprint's tests tag precisely (e.g. UNCERTAIN handling
REQ-062 separate from gateway decisions REQ-061). Ids are stable and never renumbered.

## DR-006 (2026-09-01) Every prohibited control is floor
specs/controls.yaml marks all prohibited entries floor: true and a test enforces it.
Why: a "configurable prohibition" is an oxymoron; the floor is the demo's security story.

## DR-005 (2026-09-01) Reconcile-before-retry lives in the actions SPI
ActionsAdapter.get_execution(idempotency_key) is part of the frozen contract, not an
infrastructure afterthought. Why: "API succeeded but response lost" must be answerable
by every actions integration or safe retries are impossible (guide section 06).

## DR-004 (2026-09-01) Chaos inject requires an explicit scope argument
No default value, enforced by test. Why: chaos outside an authorized drill scope is
prohibited at the floor; the SPI shape should make the unsafe call unwritable.

## DR-003 (2026-09-01) Push ingest signs timestamp.body with HMAC-SHA256
Headers X-SignalForge-Signature/-Timestamp(/-Key-Id), 300 s replay window, event_id as
the idempotency unit, 202 async. Why: replay resistance plus at-least-once semantics
without synchronous processing on the ingest path.

## DR-002 (2026-09-01) Envelope carries dual time and store-assigned fields
occurred_at (may follow a drill virtual clock) vs recorded_at (wall, store-assigned);
seq, payload_hash, prev_hash also store-assigned. Why: drills replay realistically
(S2/S7) while the audit chain stays strictly in recording order.

## DR-001 (2026-09-01) check_clean assembles its own pattern from halves
scripts/check_clean.sh greps for the reference-app name without containing it, so the
guard cannot trip itself and needs no self-exclusion. Consequence: the sprint log and
docs must reference "the reference app" generically outside allowed paths.

---

# Imported memory graph (pre-build decisions, authored before Sprint 0)

Imported 2026-09-01 from signalforge-memory-graph.md (kept verbatim in playbook/);
the reference app's name is genericized per DR-009. Where an N-line conflicts with a
frozen contract or the playbook, the later artifact governs (noted inline).

## N1. Event
- TCS AI Friday Season 2 National Finals, Problem Statement 08, ARE-AI-01 "Autonomous
  Major Incident Intelligence Command Centre". Window 29 Aug to 4 Sep 2026, demo 4 Sep.
  Judges want a full product, not a demo.
- Judging musts: live evolving incident with bridge and chat, at least 3 hypotheses
  with visible confidence movement, historical mistake prevention, one human-approved
  multi-step recovery, partial-execution reconciliation, role-specific communication,
  layered recovery validation, audit replay. Humans own: declaration, severity,
  high-risk approvals, external comms, closure.

## N2. Environment
- Single machine, no Docker. Binary downloads allowed: Prometheus and Grafana are
  running. LLM only through TCS GenAI Lab LiteLLM proxy (OpenAI-compatible) via
  LangChain and LangGraph. No real regions, Teams, or telephony: regions, bridge,
  chat, SMS and voice are simulated and labelled SIMULATED.

## N3. Product = SignalForge (command centre) + the reference payments app (first onboarded)
- SignalForge: multi-tenant SaaS-style platform. Sign-up creates org, first user is
  Org Owner, invites team, assigns roles per app, onboards apps through a wizard,
  shows steady-state health, escalates Watch to Proposed to Active war room, validates
  recovery, closes with PIR and replay. Drills (game days) are a product feature.
- Reference app: Java 17 Spring Boot modular monolith, H2, Micrometer. Payments app
  with journeys UPI Pay, Card Pay, Wallet Top-up, Refund. Region is a label (A Mumbai,
  B Chennai) on requests, metrics, logs, health, config. Internal fabric simulator
  with links GW-A, GW-B, AGG-A, AGG-B, FIB-7, CORE, RAIL; per-region routing; failover
  reroutes B via GW-A. Chaos flags make real failures (fault is injected, consequences
  are real).
- Roles: Org Owner, Incident Commander, Tech Lead, SMEs (App, DB, Network, Payments),
  Comms Lead, Executive, Auditor. On-call order and escalation timers per role.
  RBAC per app.

## N4. Positioning (vs market)
- Competitors do "AI SRE that investigates" (Datadog Bits, PagerDuty agents,
  incident.io, BigPanda + ServiceNow, Resolve AI). SignalForge's whitespace, straight
  from the brief: versioned facts vs assumptions with provenance and preserved
  superseded versions, targeted re-planning not reset, reconcile-before-retry of
  uncertain executions, correction of already-issued comms, historical relevance
  downgrade on topology mismatch, four-layer recovery verdict with stability window.

## N5. Integration model (API-first, plug and play)
- Pull: SignalForge calls the app's `/observe/*` endpoints: metrics (Prometheus text,
  includes business counters like payments_success_total, payments_unknown_total,
  settlement_queue_depth), logs (search, aggregate, sample), traces (recent, by id),
  health (per component and region), actions (manifest, execute, executions by
  idempotency key), chaos (manifest, inject or clear; drill scope only). [Exact path
  shapes: signalforge/contracts/observe.py, frozen S0-T02.]
- Push: app or pipeline calls `/api/v1/ingest/{changes|alerts|topology|messages|tickets}`
  with per-app token + HMAC, idempotent by event_id, 202 async. Deploy script sends
  deployment.started before and deployment.completed after every change ("change window").
- Business KPI connector was removed as a kind; business signals ride on metrics plus
  synthetic probes per journey and region (probes run by SignalForge with a synthetic
  header, excluded from KPIs).
- Adapter framework: one fixed SPI and canonical model per kind (metrics, logs,
  traces, health+probes, alerts, changes, topology, collaboration, itsm, actions,
  notifications, knowledge, chaos). Adapters are folders with manifest.yaml +
  adapter.py + config.schema.json, discovered by a registry, bound per app with
  encrypted credentials. Read-only and remediation scopes are separate bindings.
  Enterprise adapters (Datadog, Splunk, ServiceNow, Jenkins, GitHub, Grafana)
  implement the same SPIs; in the demo they exist as interface + mock, shown
  "Available" in the registry.
- Manifests rule: schema is hardcoded in SignalForge; content is declared by the app
  (app kit annotations serve action and chaos manifests). Agents may propose (PR) and
  compose (Drill Author agent drafts drill timelines from manifests + topology, human
  saves) but never invent faults or actions.
- Grafana wiring: Prometheus scrapes the app's `/observe/metrics`; SignalForge's
  prometheus adapter uses query_range; Grafana Alerting posts to ingest alerts;
  optional embedded Grafana panel in Health panels with built-in charts as default and
  fallback. Provisioned by YAML, started by run.sh.

## N6. Event bus and storage
- Append-only `events` table in SQLite WAL is the source of truth (transactional
  outbox). In-process dispatcher, consumer groups with stored offsets, at-least-once +
  idempotent handlers, ordering per partition_key (incident_id else app_id), retries
  then dead_letters, replay endpoint, hash chain sealed at close. Backend is itself an
  adapter (swap to Redis Streams or Kafka later). Postgres-ready via SQLAlchemy.
- Bus carries facts ("something happened"), never telemetry. Metrics go to 5 s
  rollups; logs and traces are fetched on demand into evidence objects (summary +
  reference), progressive broad to aggregate to narrow, with query and size budgets.
- Envelope: event_id, seq, topic, tenant_id, app_id, incident_id, occurred_at (drill
  virtual clock) vs recorded_at, producer, actor, source {connector, authority,
  freshness, simulated, ref}, confidence, correlation_id, causation_id, supersedes,
  idempotency_key, payload. [Frozen shape: signalforge/contracts/envelope.py.]
- Topic families: signal.*, change.*, topology.version.published, human.* (utterance,
  statement.extracted with types fact/assumption/question/decision/commitment/blocker/
  contradiction), incident.*, warroom.*, evidence.* (incl contradiction.detected),
  graph.* (node.asserted/invalidated, blastradius.updated), hypothesis.*, history.*
  (match, warning, relevance.updated), nba.*, plan.revised (kept/paused/added),
  action.* (policy.evaluated, approval.*, execution.started/checkpoint/completed/
  timeout/uncertain/reconciled, duplicate.prevented, compensated, takeover), comm.*
  (draft/approved/issued/contradicted/corrected), validation.* (layer.evaluated,
  window.*, verdict.issued), notification.*, learning.*, drill.*, system.*
  (connector.health, agent.run, llm.degraded, audit.sealed).

## N7. Agents (LangGraph, Incident Governor supervisor, event-triggered)
- Eleven agents per the brief. LLM only for language: Conversation Understanding,
  narrative parts of Hypothesis and NBA, Communication, join briefs, PIR prose, IC
  Copilot. Deterministic code: telemetry correlation and storm clustering, topology
  blast radius (NetworkX, confirmed vs predicted), change linking, historical
  topology-fingerprint distance, hypothesis confidence scoring (explicit evidence
  weights, full history), NBA scoring (urgency x impact x information gain minus
  risk), policy, Tool Gateway, validation.
- LLM hygiene: structured outputs, cache by input hash (warmed in rehearsal), token
  budget, 20 s timeout, deterministic fallback per agent, degraded-mode badge. Demo
  never depends on a live LLM call.

## N8. Governance and remediation
- Policy engine: user-editable per app (risk overrides, allow/approve/block, approver
  roles, approval expiry, confidence thresholds, retries, stability window, severity
  matrix, paging) with a hardcoded floor: high-risk classes (rollback, restart,
  failover, traffic shift, config change, customer-impacting) can never auto-execute;
  read-only can never mutate; expiry capped; audit cannot be disabled. Policy edits
  are versioned, on the timeline, dry-run preview, mid-incident edits require
  confirmation.
- Tool Gateway: validate, authn, authz, policy, approval with expiry, idempotency key,
  checkpoints, timeout leads to UNCERTAIN which blocks retry until a read-only
  reconcile determines COMPLETED_LATE / NOT_APPLIED / PARTIAL, then mark, retry,
  compensate or cancel; duplicate prevention; human takeover at any step.
- Validation: independent reads of the app (not the graph) on four layers: technical
  (error rate, p95, saturation), service (probes per journey and region), customer
  (no UNKNOWN or stuck transactions, SLO success), business (settlement queue, TPM).
  All pass, then stability window (15 min, resets on regression), then closure
  recommended; IC closes. Revalidation failure: verdict NOT RECOVERED with failing
  checks, back to Active, failure becomes hypothesis evidence (incomplete fix vs wrong
  cause), NBA proposes follow-up or declared rollback via approval, escalate after N
  cycles. No silent retry loops.

## N9. Detection (steady to declared)
- Golden signals: traffic, errors, latency, saturation, availability, business.
  Baselines on rolling windows, anomaly = beyond threshold sustained 3 evals. SLO
  burn: fast burn (~14x/5m) proposes an incident, slow burn (~6x/30m) opens Watch.
  Corroboration: propose only when 2+ independent families agree and one is
  customer-facing. Health score weighted (availability 30, errors 25, business 20,
  latency 15, saturation 10), worst region wins. Severity from impact (P0 Tier-1
  journey below ~80 percent everywhere or loss rate; P1 journey below SLO in one
  region or sustained 3x latency with errors or UNKNOWN accumulating; P2 no customer
  impact). Alert storms clustered by time + topology, children suppressed with
  visible count. Declaration is always human.

## N10. Demo scenario "Recovery Collision" (drill YAML drives it; all consequences real)
- Steady 2 percent errors, p95 180 ms. Config v12 (gateway timeout 3000 to 800 ms)
  deployed 09:51. Onset 10:03, Watch 10:04, Proposed 10:06, IC declares P0 10:07
  (INC-2026-0917). Hypotheses: H1 config 0.62, H2 DB pool 0.25, H3 network 0.13;
  discriminating test raises H1 to 0.72. Rollback v12 to v11 approved 10:14; region A
  completes; region B times out (injected server-side delay, apply lands late) so
  state UNCERTAIN; SRE says "rollback done in both regions" which becomes a flagged
  contradiction; retry blocked "external state unknown". FIB-7 loss 0.3 injected; A
  rolled back but errors persist. Topology v2 (both regions share FIB-7) published
  10:21: blast radius recomputed, H3 to 0.58, H1 to 0.35; failover B via GW-A 10:23
  (topology v3, plan diff kept/paused/added). INC-2025-0142 relevance 0.81 to 0.34 on
  topology mismatch; its "restart prolonged outage" warning stays and blocks restart.
  Exec update v1 (config-only cause, issued 10:15) marked CONTRADICTED; v2 redline
  approved 10:29. Reconcile finds v11 in B: COMPLETED_LATE, duplicate retry
  prevented. 312 payments UNKNOWN blocks the customer layer; reconciliation job
  (approved) resolves them; stability window; closed 10:52; audit sealed; PIR drafts
  with verified lessons only.

## N11. Stack and build
- SignalForge backend: Python 3.11, FastAPI, SQLAlchemy (SQLite WAL, Postgres-ready),
  LangGraph, api + worker in one process for demo. Cockpit: dark ops theme, fixed
  badge vocabulary (SIMULATED, UNCERTAIN, RECONCILED, DUPLICATE PREVENTED,
  CONTRADICTED, CORRECTED, CONFIRMED vs PREDICTED, READ-ONLY, REMEDIATION, PULL,
  PUSH). [Cockpit stack superseded to Next.js 14 + Tailwind by the architecture
  document, delta 1.] Reference app role unchanged. run.sh starts everything;
  reset.sh wipes to a seeded demoable state (org NovaPay Technologies; people: Priya
  Nair IC, Arjun Mehta SRE Lead, Kavya Rao DB SME, Rohit Sen Network SME, Meera Iyer
  Comms, Sanjay Kulkarni Exec, Anita Desai Auditor).
- Security baseline: argon2 + JWT + refresh, invites, RBAC matrix per app, tenant_id
  everywhere with enforced filters, Fernet-encrypted connector secrets, ingest token +
  HMAC, redaction list on evidence samples, platform /health and /metrics, dead-letter
  queue visible.
- Claude Code method: CLAUDE.md with design rules; contracts frozen first (envelope,
  SPIs, manifests, API shapes); one small task per session with acceptance tests;
  tests-first for policy, idempotency, reconciliation, RBAC. [Authored sprint split
  0-5 superseded by the playbook's S0-S7.]

## N12. UI state
- Claude Design prototype exists: 23 screens S1 to S23 (landing, org setup, 7-step
  wizard with Integrations cards showing adapter dropdown + PULL/PUSH and scope
  badges, discover and map with region-label selector and manifest-imported actions,
  topology, SLOs and policies, baseline and test, go live with readiness score;
  steady; watch; proposed; war room forming with ack board; war room cockpit;
  incident brain; NBA and approval; execution ledger; blast radius with topology
  versions; warning and guidance; communications with redlines; layered verdict; role
  views; copilot; drill console with chaos manifest and Generate a drill; closed with
  replay and PIR; settings with adapter registry and ingest; mobile paging;
  integration health).
- Pending UI fixes: per-adapter config fields under each Integrations dropdown
  (Prometheus needs base URL etc.; observe-* prefill from App base URL in Basics;
  push cards show copyable ingest URL + token instead of inputs; direction-correct
  test results, include one failure state), change-window chip, reference-UI panel
  names verbatim (Affected Services etc.), plan diff triad, policy editor with locked
  high-risk rows, per-row escalation timers, visible virtual clock in drills.

## N13. Open items
- Write the Recovery Collision drill YAML (timeline of human, machine, inject,
  checkpoint events with exact offsets and the endpoints each calls).
- Optional upgrade: run the reference app as two region processes sharing H2 (only if
  everything else is done).
- Build per sprint plan; rehearse twice from reset before the demo.
