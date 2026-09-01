# Sprint 6 log (2026-09-01, closed)
Goal: safe remediation end to end, with the control boundary demonstrable.

Sprint 6 is the consequential-action boundary. Everything before it observes, reasons
and proposes; this is the sprint in which SignalForge changes somebody's production
estate, and every decision in it follows from one sentence: **AI may recommend, rank,
explain and draft; it may never execute, approve, close or send.**

## Built

- [S6-T01][REQ-060] **The policy engine, with a floor that is code.**
  `domain/governance/policy.py` holds the platform safety floor as constants and
  constructor checks - not as defaults with a validator in front of them. A
  `PolicyDocument` that would contradict it **cannot be constructed**, so there is no
  seed, migration, drill, agent or direct repository write that produces a policy
  object a reader would then honour.

  Seven floor rules, each with an id that travels on every decision and every audit
  entry: `FLOOR-HIGH-RISK-NO-AUTO`, `FLOOR-HIGH-RISK-APPROVER`,
  `FLOOR-READ-ONLY-NO-MUTATE`, `FLOOR-RISK-NEVER-LOWERED`, `FLOOR-APPROVAL-TTL-CAP`,
  `FLOOR-AUDIT-IMMUTABLE`, `FLOOR-DISABLED-ACTION`. `classify()` returns `HIGH_RISK`
  for the six high-risk classes whatever the application declared or an operator
  overrode, and `PolicyEngine.decide` has no branch that returns ALLOW for one.

  Per-application policy is versioned YAML above that floor
  (`infrastructure/governance/policy_yaml.py`, behind a `PolicyCodec` port so the
  *format* stays infrastructure and the *floor* stays domain). It can only tighten:
  block an action, demand approval the floor would not have, shorten a TTL, narrow the
  approver list. An unknown key is refused rather than ignored - an operator who writes
  `auto_approve: true` and gets silence will believe it worked - and a key that so much
  as *names* audit is refused, because ignoring `audit: false` would leave somebody
  believing audit was off.

  The editor is `application/governance/policy_service.py`: versioned, authored,
  append-only, with a dry run that evaluates a candidate through the same engine the
  gateway runs, so "what would this change do" and "what does this do" cannot diverge.
  A refused document is written to the audit chain as `policy.floor.rejected` **before**
  the 422 goes back, because the error response is seen only by the person who tried.
  A mid-incident edit is refused with 409 until confirmed, and a confirmed one is
  audited as `policy.edit.mid_incident` naming the incidents it affects.

  Verified: `tests/governance/test_policy_floor.py` (22 pure-domain cases, including
  every high-risk class refusing auto-execution) and
  `tests/governance/test_policy_editor.py` (10 cases against the real API).

- [S6-T02][REQ-061, REQ-062] **The Tool Gateway and the execution ledger.**
  `application/governance/gateway.py` is the one door. Eleven steps in order on every
  consequential call - validate arguments against the application's own published
  schema, authenticate, authorize, decide, record the decision with its `rules_hit`,
  validate the approval, claim the idempotency key, invoke through the invoker, record
  the outcome, reconcile before any retry, audit all of it - and no action-specific code
  path may reach an adapter around it.

  **A timeout is not a failure.** `UNCERTAIN` sets `retry_blocked` in
  `ExecutionRecord.__post_init__` rather than by policy, the state machine has no
  `uncertain -> running` edge at all, and the only move out is `reconciling`, which asks
  the target system through `ActionsAdapter.get_execution` - the reconcile-before-retry
  primitive the frozen SPI reserved in Sprint 0. `COMPLETED_LATE` and `PARTIAL` keep the
  block; only `NOT_APPLIED` clears it.

  **Duplicates are prevented at the database.** The idempotency key is derived from
  tenant, incident, action, argument digest and attempt, and the control is the unique
  index on `(tenant_id, idempotency_key)` rather than a read-then-write - two concurrent
  submits would otherwise both find nothing and both execute.

  **Approvals are records of decisions, not capability tokens.** Bound to a fingerprint
  over the action, its arguments, its risk class and the policy version; re-checked at
  execution for expiry, spend, revocation, fingerprint and *whether the approver still
  holds the permission*. `Approval.granted` refuses an approval by the person who
  requested it whatever permissions they hold.

  Sixteen ledger states, compensation through the action's own declared reverse action
  (itself a full execution through the same gate), cancellation, and human takeover with
  a required note. Secrets never reach the ledger: `redact_args` masks values whose keys
  look like credentials before anything is stored, asserted against the audit chain.

  Verified: `tests/governance/test_tool_gateway.py` (15 cases) and
  `tests/security/test_governance_boundary.py` (8 boundary cases).

- [S6-T03][REQ-063] **Next best action, and plans that are revised rather than replaced.**
  `domain/governance/nba.py` scores `urgency x impact x information gain - risk` from
  stored facts: the severity a person set, whether the blast radius is customer-facing,
  the action class's mitigation power scaled by the confidence of the hypothesis it
  addresses, and the risk class halved for a reversible action. The score is computed
  and stored **before** any model is called; the model writes one sentence about numbers
  that already exist and cannot change the ordering, add or remove a candidate, or make
  an ineligible action eligible.

  Three eligibility gates run before ranking and a failed candidate is returned with its
  reason rather than hidden: policy said BLOCK, nobody holds a role that could approve
  it, or a checkable precondition is unmet. An impossible action can never be primary.
  Each candidate carries its rollback strategy, its expected validation time and the
  hypothesis it addresses; the recommendation carries alternatives and a strictly
  lower-risk fallback.

  Plan revision writes version n+1 marking every action KEPT, PAUSED or ADDED against
  version n, with the assumption that changed. A paused action stays on the screen.

  Verified: `tests/governance/test_next_best_action.py` (8 cases, all with **no model
  configured** - the suite's ranking, eligibility verdicts and rationales are the
  deterministic ones).

- [S6-T04][REQ-064] **Governed communications.** `domain/governance/comms.py`:
  DRAFT -> APPROVED -> ISSUED, with CONTRADICTED and CORRECTED. There is no `send()` on
  the entity, no auto-issue transition, no scheduler and no configuration that could add
  one. The audience decides the approval permission and it is checked in the service
  where the audience is known rather than on the route, which has not read it yet.

  Every draft cites incident-graph node ids and one citing nothing is refused. A
  customer-facing body never quotes untrusted content - a typed bridge message is
  evidence for responders and is not something to publish on a status page.

  `CONTRADICTED` is the one automatic move here and it is a *warning*: when a grounded
  node is superseded or retracted the issued statement is flagged and **the published
  text is left exactly as it was**. A correction is a new communication carrying a
  computed line-level redline and needing its own approval.

  Verified: `tests/governance/test_communications.py` (6 cases).

- [S6-T05][REQ-065] **Layered validation and the closure gate.**
  `application/governance/validation_service.py` reads the application through its own
  adapters and rollups across four layers, and `LayerCheck.__post_init__` **refuses to
  be constructed** with a source that names the Incident Brain - so the independence
  rule is a property of the objects rather than a docstring. The arrow points one way:
  a failed cycle *writes* an OBSERVED evidence node into the graph and never reads one.

  A layer is as good as its worst check; there is no averaging. `UNKNOWN` does not pass.
  Customer gates closure, business does not. The stability window requires a duration
  *and* consecutive samples, and any regression resets it to zero rather than
  decrementing it.

  A failed cycle stores its checks with their `result_ref`s, writes the evidence node,
  returns the incident to `active` if it was `recovering` (the one machine transition
  the lifecycle permits, and the safe direction), increments the failed-cycle count and
  escalates after the cycles the application's policy allows. The gate is wired to the
  lifecycle as a hook (`IncidentService.closure_guards`), so the incident service still
  has no import of validation and a node without it closes exactly as Sprint 4 did.

  Verified: `tests/governance/test_layered_validation.py` (10 cases, the customer
  failure produced by a **real probe** against a path the application does not serve).

- [S6-T06][REQ-060..REQ-065] **The cockpit.** `web/features/governance/` fills the Next
  Best Action placeholder Sprint 4 reserved and adds Execution Ledger, Recovery
  Validation, Communications and Response Plan beside the grid, plus S21 as its own
  route (`/apps/[appId]/policy`). Everything shares the cockpit's existing socket -
  there is still exactly one WebSocket store, and the server's consumer now subscribes
  to `execution.*`, `policy.*`, `comms.*`, `validation.*`, `nba.*` and `plan.*`.

  Three absences are the design and each is asserted by a test that checks the construct
  does *not* appear: **no auto-execute control on any action card at any risk class**,
  **no Retry beside an uncertain execution** (Reconcile in its place, with the sentence
  that explains why), and **no send on a communication**. The approval sheet's typed
  confirmation asks for the action id rather than "YES", because typing
  `rollback-checkout` is a moment in which a person reads what they are approving. The
  policy editor's padlocks are rendered from the server's `locked` field, and the screen
  says out loud that the floor is enforced in the domain rather than by the browser.

  Verified: `tests/test_web_governance.py` (15 structural and safety cases),
  `tsc --noEmit` clean, `npm run build` clean.

## Not built / deferred

- **Team Chat (S9 panel).** Reserved by Sprint 4 and not filled. The panel now says so
  on the screen - "Not built, and not scheduled" - with the reason: the bridge is the
  record of what was said, and a second channel outside that record is where decisions
  go missing. It stays visible rather than removed so the layout an incident commander
  learned does not move. Impact: none on any Sprint 6 control. Plan: a decision for
  Sprint 7 or later about whether it should exist at all, not a build task.
- **S17 role views (IC, SME, Comms, Executive, Auditor) as named screens.** The
  *permissions* behind them are enforced end to end and tested - a Comms Lead can
  approve a customer statement and an Executive cannot, an SME can draft and cannot
  approve, an Auditor reads policy history and cannot edit - but the five per-role
  landing screens the reference UI shows were not built. Impact: every role can do its
  job through the war room and the app screens; nobody is blocked. Recorded as an
  unticked item in `playbook/coverage-checklist.md`.
- **A general audit-read API.** The chain is written and verifiable and the policy
  version history is the first permissioned read of it (`read_audit`), but there is no
  browsable audit surface. Planned for S7 alongside the replay scrubber.

## Deviations

- **`MappedAction` gained three fields** - `params_schema`, `verification` and
  `preconditions` - all read from the application's own action manifest, which already
  carried them in the frozen schema (`contracts/schemas/action-manifest.schema.json`).
  No contract changed; the profile simply stopped discarding what the manifest said.
  The gateway validates arguments against the first, layered validation counts the
  second into expected validation time, and next-best-action checks the third. All three
  default empty, so a profile stored before Sprint 6 still loads.
- **`DetectionService` gained two read methods** (`latest_probe_results`,
  `latest_against_baseline`). Additive, no behaviour changed, and they exist so layered
  validation reads the same instant the detection screen is showing rather than firing
  its own probes and producing a second answer to "is the journey working".
- **`IncidentService` gained a `closure_guards` hook list.** A hook rather than an
  import, exactly like `on_declared` and `on_closed`, so the lifecycle still knows
  nothing about validation. Unlike the other hooks, a guard's exception is deliberately
  *not* swallowed: refusing the close is the whole point of it.
- No frozen contract was modified. `signalforge/contracts/` and `specs/` are unchanged
  except for the six REQ status flips from `planned` to `built`.

## Tests

- added: 81 governance tests (`signalforge/tests/governance/`, 22 pure-domain policy
  floor + 10 policy editor + 15 Tool Gateway + 8 NBA + 6 communications + 10 layered
  validation + 2 Sprint 6 acceptance, plus 8 in
  `tests/security/test_governance_boundary.py`), 15 web structural tests
  (`tests/test_web_governance.py`). Two existing web tests were updated where Sprint 6
  filled the placeholders they guarded.
- The failing-first security cases CLAUDE.md requires for policy, idempotency,
  reconciliation, RBAC and tenant isolation are all present and all named after the
  thing they refuse: the floor cannot be weakened, a high-risk action cannot
  auto-execute, an unauthorized approval is rejected, an expired approval is rejected,
  a permission revoked after approval blocks execution, cross-tenant access is a 404,
  a duplicate submits nothing, UNCERTAIN blocks retry, reconcile comes before retry,
  secrets are redacted from arguments and audit, customer validation blocks closure, and
  a communication cannot auto-send.
- suite result: see `docs/21-test-report.md`.

## Artifacts updated

- `docs/04-functional-spec.md` - section 12, governance and safe execution.
- `docs/08-agent-architecture.md` - sections 12 and 13, the consequential-action
  boundary and the S6 metrics.
- `docs/10-data-design.md` - regenerated (`make erd`); six new tables.
- `docs/11-api-spec.md` + `docs/11-openapi.json` - regenerated; the governance group and
  its prose section.
- `docs/13-controls-matrix.md` - **v2, enforced.** Twelve controls moved from planned or
  partial to enforced, each naming the module and the test.
- artifact 14 (functional test suite) and artifact 18 (security suite) - extended as
  above.
- `docs/20-RTM.md`, `docs/21-test-report.md` - regenerated.
- `docs/decision-record.md` - DR-106 through DR-110.
- `specs/requirements.yaml` - REQ-060..REQ-065 `built`.
- `playbook/coverage-checklist.md` - five S6 items ticked, one left honest.

## Security notes

- **New surfaces.** 26 governance routes. Reads carry no permission (the same reasoning
  the war room's and the brain's reads carry none: a paged responder who cannot see the
  approval countdown will ask in chat); every write carries one, except drafting a
  communication, which is deliberate and documented.
- **The floor is not a permission.** An org owner holding every permission is refused a
  `rollback: auto_execute: true` exactly as firmly as anybody else, and there is a test
  that says so. That is the difference between a policy floor and an authorization rule.
- **Separation of duties, twice.** The requester of a high-risk action cannot approve it
  (`Approval.granted`, 422 regardless of permissions), and an externally visible
  communication cannot be approved by its author.
- **Four writes are exempt from the permission census, and the exemption is written
  down.** `tests/security/test_api_surface.py` refuses to let a session write ship
  without a permission unless it is on a closed, justified list, and the four
  communications writes are the first Sprint 6 additions to it. They needed **two new
  categories**, both defined in `tests/security/test_governance_boundary.py` beside the
  tests that hold them to their claim:
  - `INERT_DRAFT_WRITES` (draft, correction) - the artifact **cannot reach anybody**.
    `TRANSITIONS` admits ISSUED only from APPROVED and only `issue()` performs it, so a
    draft requires a second permissioned act by a second person to have any effect. That
    is what lets the SME - who holds nothing in the frozen matrix and is who knows the
    customer-facing symptom - write one down. Obligation asserted: an SME can draft and
    can neither approve nor issue.
  - `AUDIENCE_SCOPED_WRITES` (approve, issue) - these **do** enforce a permission; it is
    simply not static. Which one applies is decided by the communication's audience,
    which the route has not read yet, so it is enforced in the service where the audience
    is known. A route-level guard would have to pick one audience's permission and be
    wrong for the other three. Obligation asserted in both directions: an Executive is
    refused a customer statement, a Comms Lead is allowed one.

  The existing `SELF_SCOPED_WRITES` and `STATE_FREE_WRITES` categories gained no members,
  and `test_warroom_boundary.py` joins all four lists so none can drift from the census.
- **The `/apps` census also gained a pinned exemption.** `test_onboarding_boundary.py`
  required every route under `/apps` to carry `manage_apps`; the policy routes carry
  `edit_policies`, `read_audit` and (for the read) none. The frozen matrix names
  `edit_policies` separately from `manage_apps` because pointing the platform at an
  application and deciding what it may do to that application are different jobs. Both
  sit with the org owner today, so nothing widens - and pinning the four routes with
  their expected permissions means an org that later splits the roles gets the split it
  asked for rather than a silent re-merge.
- **No new metric label can name an incident, a tenant or an argument.** Sixteen new
  series, every label a closed vocabulary; `app` appears on the two policy counters only.
- **Threat-model rows.** No new attacker capability was introduced: the gateway reduces
  the reachable surface by making the invoker unreachable except through it. The
  interesting new *refusals* are catalogued in `docs/04` §12.12.

## Known issues and technical debt

- **Compensation is best-effort by design.** It runs the application's own declared
  reverse action through the same policy decision and approval gate. An action that
  declares no reverse cannot be compensated by the platform; the ledger says so and
  offers human takeover rather than inventing an undo.
- **The uncertain sweep logs rather than reconciles.** Reconciliation queries a
  production system and its verdict decides whether a retry is permitted; that is an
  operator's action taken while they are looking at the incident, not something a
  background loop should do at 04:00 on behalf of nobody. The job makes an unresolved
  consequential action visible (`signalforge_executions_unreconciled`) and stops there.
- **`_claim_key` walks attempts linearly** (bounded at 12). Correct and cheap for an
  incident's handful of attempts; a pathological history would want an indexed
  max-attempt lookup.
- **Checkable preconditions are a fixed vocabulary** of four
  (`change_window_open`, `incident_active`, `topology_known`, `actions_connector`).
  Anything else an application declares is shown to the operator and not evaluated -
  inventing a verdict for a string we cannot check would be worse than admitting we
  cannot.
- **Business validation tolerance is a constant** (half of baseline), not per-app
  policy. Right for now; a real deployment would want it configurable.

## Demoable checkpoint

The Sprint 6 acceptance, as one scenario
(`tests/governance/test_sprint6_acceptance.py`, and reproducible by hand):

1. Declare a P0 on `checkout`; announce a deployment through the signed ingest path.
2. Open the war room. **Next Best Action** ranks the rollback, showing
   `urgency x impact x information gain - risk` as four bars and the score beside them.
3. The card says `approval_required` with `FLOOR-HIGH-RISK-NO-AUTO` as a padlocked chip,
   names `incident_commander` as the approver, and offers **Request approval** - there
   is no auto-execute control anywhere on it.
4. `POST /incidents/{id}/executions` with `intent: auto` returns `block`, a ledger row in
   `blocked` and `FLOOR-AUTO-EXECUTE-REFUSED`. Nothing reached the application.
5. Request it properly; a **second** commander approves with a live countdown.
6. Run it against an application that does not answer. The ledger row is **UNCERTAIN**,
   bordered in danger, with **no Retry control** - only Reconcile.
7. Retry via the API: 409, audited as `execution.retry.blocked`.
8. Reconcile: the platform asks the application under the same idempotency key and
   resolves **COMPLETED_LATE**. Repeating it stays refused.
9. Submit the identical request again: `duplicate: true`, nothing sent.
10. Run **Validate recovery**. Technical passes, service passes, **customer fails** on a
    real journey probe. Closure is locked with the reason on the screen, `POST .../close`
    answers 409, and the failure appears on the incident brain as OBSERVED evidence.
11. Separately: `POST /apps/checkout/policy` with `rollback-checkout: auto_execute: true`
    answers 422 naming `FLOOR-HIGH-RISK-NO-AUTO`, writes `policy.floor.rejected` to the
    audit chain with the attempted document, and leaves the policy at version 1. This is
    the guide's "a control actively preventing an unsafe action".

## Coverage checklist ticks

- S6 policy YAML above a hardcoded floor, with versioning, dry run, mid-incident
  confirmation and visibly locked high-risk rows.
- S6 Tool Gateway with allow/approve/block, `rules_hit`, approvals with expiry,
  idempotency, timeout -> UNCERTAIN, reconcile-before-retry, duplicate prevention,
  compensation, human takeover and the ledger UI states.
- S6 NBA scoring with alternatives and fallback; plan revision kept/paused/added with
  changed assumptions.
- S6 comms lifecycle with redlines, per-audience approvers and no auto-send.
- S6 layered validation independent of the graph, stability window with reset, closure
  gate, revalidation failure feeding hypotheses, escalation after N cycles.
- Left unticked: S6 role views as named screens (permissions enforced; screens not
  built).

## Entry criteria for next sprint: met

Sprint 7 needs a governed execution path to drive a drill through, an audit chain
covering every consequential act to replay, and a closure gate for the PIR to sit
behind. All three exist. The drill engine's chaos scope (CTL-PRO-04) is still enforced
only at the SPI interface, which is S7's own first task.
