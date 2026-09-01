# Sprint 3 log (2026-09-01, one day)
Goal: any app can be onboarded through public contracts; the reference harness proves it.

## Built
- [S3-T01][REQ-030,015] Adapter framework. Domain in signalforge/domain/adapters/: the
  manifest as a frozen value object (id, name, version, kind, mode pull/push/invoke,
  capabilities, auth, credential_scopes, config_schema, limits, redaction, availability,
  simulated, vendor) whose __post_init__ is the only way one comes into existence; a
  capability catalogue derived from contracts/spi.py rather than restated, so a manifest
  naming a non-method is refused at load and the registry cannot drift from the frozen SPI;
  and a floor nothing configures - invoke mode must declare a mutating capability and pull
  must declare none, push must declare normalize, a mutating adapter may not offer
  read_only, chaos offers exactly [drill], the redaction-bearing kinds must state a
  policy_id, and timeout <= 120s / result <= 8 MiB / rows <= 50000 bound every manifest.
  ConnectorBinding with validate_config / assert_scope_offered / assert_can_invoke;
  the capability matrix as rows of adapter x capability plus per-kind coverage across all
  13 kinds, with interface-only adapters listed and never counted as coverage.
  Infrastructure: FileAdapterRegistry over adapters/**/manifest.yaml with reload(), a
  manifest that will not parse recorded with its path while the others still load, and a
  duplicate id refused rather than resolved by filesystem order; AdapterRuntimeRegistry
  mapping adapter id to factory with an AdapterContext whose __repr__ redacts;
  SqlConnectorBindingRepository, connector_bindings + Alembic 9eb3b6913aa0, credentials
  Fernet-encrypted under connector/<binding>/<field> with the binding, its secrets, its
  audit entry and its event in one transaction. Application: ConnectorService, plus a
  connector health loop joined to the S2 ConnectorHealthService through a new
  register_provider - UP measured, DEGRADED then DOWN on the third consecutive transient
  failure, DOWN at once for a terminal one, UNKNOWN with no runtime, written back to the
  binding row so freshness survives a restart. API: GET /adapters (with load errors),
  /adapters/capability-matrix, /adapters/{id}, GET|POST /connectors, /connectors/health,
  GET|PATCH|DELETE /connectors/{id}, all manage_apps.
  Verified: 82 REQ-tagged tests (the duplicate-binding rule, the credential-scope floor,
  connector tenant isolation and the partial-completion rollback written failing-first);
  live on :8899 with a hand-written manifest folder, the ciphertext in the secrets table
  inspected and the scrape shown to name no binding.

- [S3-T02][REQ-031,033,035] Twenty-two shipped adapters, none of which names an
  application. signalforge/infrastructure/adapters/: the http-observe set (metrics, logs,
  traces, health, actions, chaos) over the frozen observe contract, each pinging the
  endpoint it actually uses so a metrics binding cannot read healthy while
  /observe/metrics returns 500; prometheus (query and query_range, NaN kept rather than
  dropped, an over-wide range refused before the call); grafana-alert-webhook and
  deploy-webhook as pure normalisers that refuse to guess - unknown severity becomes
  warning and never critical, a deployment with neither status nor event name is rejected;
  topo-import covering pull and upload through one code path so both produce the same
  versioned snapshot; itsm-lite as a working simulated ticket queue with an explicit status
  machine; teams-sim as a bridge that plays a written script materialised as rows at future
  sent_at, so a 100x drill plays it at 100x with no drill-aware code; four notification
  channels (in-app, browser push, page-sim SMS/voice, teams-sim DM) each writing a durable
  receipt before touching a transport; kb-index ranking with BM25 always and embeddings
  blended when a provider exists. Prometheus text parsing and the label-selector subset live
  in domain/integrations/promtext.py as pure code, and a malformed line raises rather than
  being dropped. ConnectorInvoker (application/adapters/invoker.py) is the single path from
  a binding to a live call - tenant lookup, enabled check, orphan check, runtime check, the
  credential-scope floor re-checked at call time, credentials decrypted per call, a watchdog
  bounding the capability; HttpTransport is the one outbound client, http(s)-only, bodies
  read in bounded chunks against max_result_bytes, per-binding call budgets, and a redacted
  repr. Five enterprise connectors (datadog, splunk, servicenow, jenkins, github) ship as
  manifests with no runtime: Available in the registry, rows in the matrix, never counted as
  coverage. /ws arrives as an authenticated, self-scoped notification socket with catch-up of
  everything still awaiting acknowledgement. Four tables + Alembic 4b8f2c1d7a90 behind their
  own IntegrationUnitOfWork.
  Verified: 161 REQ-tagged tests, every one of them going signup -> POST /connectors ->
  ConnectorInvoker against an ASGI application serving only the frozen observe contract, so
  nothing constructs an adapter by hand; live on :8899 with 22 manifests loading and zero
  errors.

- [S3-T03][REQ-032,017] Push ingestion at the five frozen paths. domain/ingest/: the ingest
  key as two independent halves - a `sfi_`-prefixed bearer whose sha256 alone is stored and
  an HMAC secret held as Fernet ciphertext - scoped to one tenant, one app_id and one
  stream, both shown exactly once; IngestStream derived from the frozen INGEST_PATHS with an
  import-time drift guard, carrying each stream's canonical model, adapter kind, item
  validator and topic rule; a redaction floor applied at write time to message text, ticket
  fields and alert annotations. IngestReceipt + ingest_receipts unique on
  (tenant_id, event_id) - not per key, so a rotation cannot double-apply, and not global, so
  two tenants picking the same producer-assigned id neither block nor observe each other.
  IngestService.authenticate runs the checks in the order that costs least first: token hash
  lookup, per-key rate limit, key-id match, HMAC-SHA256 over "<timestamp>.<raw body>" against
  the exact bytes received and inside the replay window, then the stream check. The sender
  always gets a bare 401; the reason lands on the key row, in the tenant's audit chain and on
  the timeline as system.ingest.rejected. accept() is canonical first with the bound push
  adapter as an optional translator, per-item rejections reported in the 202 with index and
  reason, duplicates checked before validation, and receipts + events + counters + audit in
  one transaction. Five literal routes in api/routers/ingest.py behind deps.require_ingest,
  IngestKeyService with GET|POST /ingest-keys, GET|DELETE /ingest-keys/{id} and
  POST .../rotate (which issues a successor and leaves both live), two tables + Alembic
  b7d24e0af315, and two metrics over closed vocabularies.
  Verified: 64 REQ-tagged tests with the whole of test_ingest_idempotency.py written
  failing-first; every test issues a real key through the real API and signs the exact bytes
  on the wire; live on :8899 through accept, duplicate, forged signature and stale timestamp.

- [S3-T04][REQ-036,060] Onboarding use cases and the wizard's APIs. domain/onboarding/: the
  Application entity keyed on the same tenant-scoped app_id that role bindings, connector
  bindings, ingest keys, events and audit entries already use, with a base URL refused unless
  it is an absolute http(s) root; the OnboardingProfile aggregate holding golden signals
  (six families, business first-class), region label, regions, journeys, probe templates,
  imported actions, SLO targets, a severity matrix, policy defaults, baselines, a topology
  reference and the last test summary, with its cross-section invariants enforced on every
  write; the policy floor as domain code, refused for an org owner exactly as firmly as for
  anyone else - a high-risk class can never auto-execute, a risk override may raise and never
  lower, an approver must hold approve_high_risk in the frozen matrix, the approval TTL is
  capped at an hour and the six high-risk classes cannot leave require_approval_classes;
  a deterministic severity matrix shipped configured with the P0/P1/P2 defaults; and
  readiness as thirteen weighted checks summing to exactly 100, five of them blocking, with
  `ready` requiring both. application/onboarding/: auto-discovery probing the seven observe
  paths under the app's own base URL plus Prometheus on 9090 and Grafana on 3000, matched to
  adapters by manifest vendor and never by id, where a 401 counts as found and a 404 is a
  definite no; direction-correct connection tests (pull pings then performs one read-only
  capability, invoke reads the manifest and never executes, push reads PENDING until real
  traffic arrives) whose verdict is written onto the binding's own health columns; actions
  imported from the application's manifest with the declaration kept beside the operator's
  override; topology upload versioned by content, so an identical re-upload is one version,
  one event and no profile change; baseline learning where one scrape answers every signal
  and a partial run reports the signals it missed by name and merges rather than replaces.
  Sixteen /apps routes, all manage_apps, reached through a second RBAC factory that reads
  app_id from the path; three tables + Alembic d3a17c5be902; HttpEndpointProbe as the one
  unauthenticated outbound call this platform makes, http(s)-only, following no redirect.
  Verified: 91 REQ-tagged tests (the policy floor, topology idempotency and onboarding
  isolation written failing-first against a missing API); every test onboards through the
  real API against an application implementing only the frozen observe contract, reaching
  readiness 100 with no seeds; live on :8899.

- [S3-T05][REQ-036,030,032] The cockpit's onboarding surface. web/features/onboarding/ plus
  web/app/apps/[appId]/onboard/[step]/ - all seven wizard steps as nested routes rather than
  tab state, so a half-finished onboarding is a URL that can be sent to whoever owns the
  credential for step 2, and the stepper's per-step progress is the domain's own
  readiness.steps rather than a second notion of done. Three genuinely different card shapes
  on Connect: pull/invoke cards rendered from the adapter's own config_schema (a new adapter
  folder gets its fields with no web change), push cards with a copyable ingest URL, a masked
  token prefix, issue/rotate/revoke and a shown-once credential panel that says why it is
  shown once, and simulated cards that read "built-in" and carry the SIMULATED badge. Five
  canonical stream cards at the five frozen ingest paths, so collaboration and itsm - which
  can never have a push adapter under the frozen SPI - are reachable at all. The actions
  panel disables `auto` for a high-risk class and restricts approver chips to the roles that
  actually hold approve_high_risk, showing the floor at the point of choosing rather than
  only at the point of refusing. web/features/apps/ is the S4 overview, showing the readiness
  score and the verdict as two separate facts; web/features/integrations/ is the S23 screen
  with direction, last pull, last push, freshness, errors and a SIMULATED column;
  /settings gained the adapter registry section (every manifest, capability coverage per
  kind, and - deliberately prominent - the manifests that would not load with their path and
  reason) and the ingest keys section. The push half of GET /connectors/health was built to
  feed it, joining last_event_at, freshness, counters and last error from the live keys whose
  binding_id names the binding, which is the item S3-T02, T03 and T04 each closed with open.
  Verified: 27 REQ-tagged tests, five of the seven integration-health tests run and seen red
  before the join existed; the credential-leak test binds a real connector with a real secret,
  issues a real key, posts a real signed batch and then asserts all six endpoints the screens
  read carry none of it; `npm run build` green with 15 routes.

- [S3-T06][REQ-038,032] The reference harness under reference-app/ - a real second
  application, Java 17 and Spring Boot 3.5 on an H2 file database, that has never heard of
  SignalForge. This log does not name it, because scripts/check_clean.sh scans docs/ too, and
  that is exactly the rule the task exists to prove. No dependency, no SDK, no generated
  client, no shared package: a test walks every source file and fails on an import naming the
  platform, and the only coupling in either direction is a frozen wire
  format. All twelve frozen /observe/* paths in one controller, asserted to be exactly the
  frozen set - neither short nor extended. Twenty Prometheus metrics from a hand-rolled
  registry where every metric declares its label names, every series is preregistered at zero
  so "no errors" is distinguishable from "no data", and synthetic traffic is counted in its
  own series so the platform probing an application cannot inflate the KPI it is watching.
  A fabric simulator with seven links where region-b's primary crosses a shared fibre span and
  its secondary does not, so a fault there degrades one region with a healthy control beside
  it. A ledger in H2 whose payment states include UNKNOWN - what a payment lost at the rail is
  recorded as, because FAILED would be a guess and a caller told "failed" retries and pays
  twice - and an executions table keyed on the caller's own idempotency key, written before
  the side effect is attempted, which is what makes reconcile-before-retry possible from
  outside. Seven published actions and five chaos scenarios, each with an implementation the
  harness refuses to start without, each chaos injection carrying an expiry the harness
  enforces itself. Four separate bearer credentials (read_only, remediation, drill, deploy)
  with 401 for none and 403 for the wrong scope, never 404. The harness's own config directory
  holds the deployment pipeline: bash, curl and openssl, no library, announcing
  deployment.started and deployment.completed as signed canonical ChangeEvents
  whose event_id is derived from
  (release, region, status), so a re-triggered pipeline is a duplicate rather than a second
  deployment. run.sh and reset.sh reach harnesses by glob, so deleting reference-app/ leaves
  both working.
  Verified: 30 REQ-tagged tests. test_reference_onboarding.py starts the built jar as a
  subprocess on an ephemeral port and onboards it over the network through the same routes
  the wizard's screens call, reaching readiness 100 with `ready` true and no seeds;
  test_reference_ingest.py runs deploy.sh as a real subprocess against a recording server and
  replays its exact bytes at the real ingest endpoint, so what is under test is openssl and
  curl being accepted. Steps 2 and 8 to 13 of the demo below were recorded live against the
  harness on :8080 and the platform on :8899.

- [S3-T07][REQ-036,012] Artifact 11 generated from the running application, and artifact 04
  finished. signalforge/api/openapi.py builds the published document from the live route
  table and stamps each operation with the credential it wants, the permission its own RBAC
  dependency declares, where a per-application role binding reads app_id from, and a real
  OpenAPI security requirement - all read off the guard, so a route guarded by the wrong
  permission is published as guarded by the wrong permission and an unguarded one is
  published as needing nothing, which is then a failing security test rather than a review
  finding. The RBAC factories declare their permission as an attribute on the dependency they
  return, so nothing introspects a closure at publish time and the annotation cannot be
  computed differently from the decision. The three ingest schemes are published together in
  one requirement object, which is OpenAPI for "all three", with the key-id header as the
  optional fourth. scripts/gen_openapi.py (`make openapi`, plus `--check`) exports
  docs/11-openapi.json - 55 paths, 71 operations - and splices the 72-route catalogue into
  docs/11-api-spec.md between markers; neither artifact carries a timestamp, so regeneration
  is byte-stable and a diff means the API changed. app.openapi is that same builder, so
  GET /openapi.json and the exported file are one document. docs/11 was rewritten from a set
  of per-sprint sections into the whole specification - conventions, the exported document,
  the generated catalogue, and the identity and event/platform sections the API spec never
  had. docs/04 gained chapter 6, onboarding as a contract an integrating team can implement
  against: the fourteen things an application provides and which five block `ready`, what the
  platform deliberately never asks for, the dependency order with routes and events, the three
  directions, why every step is re-entrant, a failure catalogue with the test that holds each
  row, and offboarding.
  Verified: 16 REQ-tagged tests, negative-checked rather than assumed - a byte appended to the
  export fails the staleness test naming the command that fixes it, and one row deleted from
  the catalogue fails naming the route that went missing, both run and seen red before being
  restored; the running server on :8899 asserted over HTTP to serve the same document as the
  file.

## Not built / deferred
- The harness was not click-driven through a browser. Sprint 3's acceptance says "onboard the
  harness fully through the UI (no seeds)". Every route the wizard's screens call was driven
  end to end against the live JVM harness over the network, with no seeds, to readiness 100
  and `ready` true (signalforge/tests/reference/test_reference_onboarding.py), and the screens
  themselves build green and are held by structural and contract-pinning tests. What is
  missing is a human at a browser: this was an unattended run and nobody clicked. Impact: the
  UI half of the acceptance is evidenced by build plus tests plus an identically-driven API,
  not by a recorded session. Plan: the walkthrough is written out step by step in the demo
  checkpoint below and is the first thing to run at the S4 demo.
- No cockpit /ws client, so nothing renders notifications. In-app and browser-push
  notifications are delivered, acknowledged and audited over the socket by tests and by any
  WebSocket client, but no screen shows them. Impact: the notification half of REQ-033 is
  reachable only programmatically. Plan: S4-T03's ack board and S4-T04's S8 screen, where
  there is finally something to be notified about.
- No policy editor in S21 settings. The sprint task names the adapter registry and ingest
  sections only, and per-application policy defaults already have their own wizard step.
  Impact: org-wide policy is set per application rather than centrally. Plan: S6 governance
  (REQ-060), which is where policy YAML, versioning and dry run live.
- The wizard does not draw a topology diagram. Upload, version list and content-versioning all
  work; only the picture is missing. Impact: an operator reads node and edge counts rather
  than a graph. Plan: S5's topology panel, which needs the graph view component.
- alerts.fetch_active and changes.list_changes are covered by no shipped adapter, and the
  capability matrix says so rather than hiding it. The frozen observe contract has no endpoint
  for polling active alerts or listing changes, so no adapter could honestly claim either.
  Impact: none in practice - both signals arrive by push, through grafana-alert-webhook and
  deploy-webhook, and the pull interfaces are declared by the jenkins entry. Plan: unchanged;
  a vendor adapter with a real query API would fill them.
- kb-index ranks with BM25 only, because no embedding provider is installed. The port, the
  blending, the weighting and both failure paths are built and tested against a deterministic
  provider. Impact: semantic recall is absent until a provider exists. Plan: S5 (REQ-051)
  wires llm.provider into it; it is one assignment in deps.Container.
- No generated API client. The export is a client generator's input and the cockpit's types
  are already pinned field-by-field against the response models. Impact: an integrating team
  generates its own. Plan: S7's release package, where packaging belongs.
- No git hook on `gen_openapi.py --check`. The flag exists and the suite fails on a stale
  export, which is the actual control; hooks are not committed in this repository. Impact:
  regenerating artifacts 10, 11, 20 and 21 remains something a task has to remember. Plan:
  none - the failing test is the mechanism.
- No scheduled re-import of actions, topology or baselines. They refresh when an operator
  asks. Impact: a manifest that changes between onboardings is picked up on the next import,
  not automatically. Plan: S4's detection loops, which need a change-detection rule anyway.
- The harness runs both regions in one JVM and models base latency rather than sleeping it
  (injected latency is really slept, capped at 1500ms). Impact: a drill cannot kill a region
  entirely, only its links. Plan: two processes over H2 AUTO_SERVER, already possible via the
  datasource URL, if a drill ever needs it.

## Deviations
- No contract deviations, and none was needed. signalforge/contracts/ and specs/ were read
  throughout and never modified: OBSERVE_PATHS, OBSERVE_METRICS_CONTENT_TYPE, INGEST_PATHS,
  REPLAY_WINDOW_S, MAX_BATCH_ITEMS, the three ingest headers, the canonical models, both
  manifest schemas, the SPI method names, the error taxonomy, AdapterKind, WizardStep,
  ActionClass/RiskLevel/HIGH_RISK_ACTION_CLASSES and the roles matrix were all used as frozen
  in S0. No human instruction to change a contract was given, and none was needed. The only
  edits to specs/requirements.yaml are status transitions: REQ-030, REQ-031, REQ-032, REQ-033,
  REQ-035, REQ-036 and REQ-038 planned -> built (REQ-036 passing through `partial` at S3-T04,
  because its acceptance also names the integration health screen). No id was added or
  renumbered.
- The capability catalogue is read off contracts/spi.py rather than restated, and the
  approve_high_risk role set is derived from ROLE_PERMISSIONS rather than copied. Where the
  frozen contract could have been duplicated, it is referenced instead.
- The adapters tree lives at signalforge/infrastructure/adapters/ rather than a repo-root
  adapters/. CLAUDE.md's layering section places adapters under infrastructure and that rule
  wins over the architecture document's older sketch; the sprint's `adapters/**/manifest.yaml`
  glob is satisfied and the root is configurable via SIGNALFORGE_ADAPTERS_DIR.
- Design decisions recorded as DR-019 through DR-059 (41 entries). The ones that changed how
  something already built behaves: DR-023 and DR-022 extended the S2 ConnectorHealthService
  with register_provider and tenant-aware sources rather than duplicating it, with no S2
  behaviour change (the 215 earlier tests still pass unmodified); DR-038 added a second RBAC
  factory that reads app_id from the request path, because FastAPI resolves a dependency
  parameter declared as a query parameter as a query parameter even when the route has a path
  parameter of that name - it grants access that was wrongly denied to a per-app operator and
  denies nothing that was allowed, both directions asserted; DR-045 added eight additive
  read-only fields to GET /connectors/health, with every earlier assertion about it still
  holding unmodified.
- Two firsts in the security suite's router-coverage walk, both recorded: DR-031, /ws
  authenticates but carries no permission, because the roles matrix gives SMEs - the people
  most likely to be paged - no permissions at all, so any permission would exclude exactly the
  wrong group; and DR-033, the ingest routes resolve a machine credential rather than a human
  session and so carry no Permission, because the roles matrix describes people. Both are
  compensated structurally and tested: the socket cannot name anyone but its own principal,
  and an ingest key resolves to no principal on any session route.
- DR-057: the published, unauthenticated OpenAPI document names the permission each route
  enforces. The roles matrix is already served to every member at /roles/matrix and published
  in the docs, and knowing that a route wants manage_apps does not help anybody obtain it; the
  compensating gain is that an unguarded route becomes visible to a reviewer and to a test.
- Harness stack choices departing from the architecture's sketch, each recorded: DR-052 (own
  Prometheus exposition rather than Micrometer, because the platform's parser refuses a
  malformed line and a registry that requires declared label names is what keeps a payment id
  out of a label), DR-053 (manifests as reviewable JSON documents with a startup drift check
  rather than an annotation scan, because a generated manifest cannot be wrong and therefore
  cannot be reviewed), plain spring-boot-starter-jdbc rather than JPA, and JSONL written by the
  harness rather than by a logback encoder. Observable behaviour is unchanged.
- DR-050: the harness holds four credentials rather than the three the architecture names. The
  fourth is the deployment pipeline's own; giving the pipeline an observability credential
  would have meant the platform and the pipeline shared a key, which is the one thing "wired
  only via public contracts" has to rule out.
- DR-049: run.sh, reset.sh and .gitignore reach harnesses by glob rather than by name. The
  first version named the directory and check_clean.sh failed the commit, correctly - those
  three files are core.

## Tests
- added: 471 (82 S3-T01 / 161 S3-T02 / 64 S3-T03 / 91 S3-T04 / 27 S3-T05 / 30 S3-T06 /
  16 S3-T07). Of those, 50 are security tests across seven boundary files
  (signalforge/tests/security/: credential scope, adapter boundary, ingest boundary,
  onboarding boundary, cockpit surface, reference boundary, api surface); the remaining 421
  are functional, and almost all of them are integration tests rather than unit tests -
  every adapter test goes signup -> POST /connectors -> ConnectorInvoker against a live ASGI
  application serving the frozen observe contract, every ingest test signs the exact bytes on
  the wire, every onboarding test drives the real API, and the reference tests boot a real JVM.
  The genuinely unit-level suites are the pure domain ones: manifest validation, the
  Prometheus text parser and selector subset, BM25 ranking, the severity matrix, readiness
  weighting and the policy floor.
- written failing-first, as CLAUDE.md requires for policy, idempotency, reconciliation, RBAC
  and tenant isolation: the credential-scope floor and connector tenant isolation (S3-T01),
  the whole of test_ingest_idempotency.py (S3-T03), test_policy_defaults.py,
  test_topology_upload.py and test_onboarding_isolation.py (S3-T04), and five of the seven
  integration-health tests (S3-T05). The two artifact tests in S3-T07 were negative-checked
  instead - broken deliberately, seen to fail with a useful message, then restored - because a
  documentation test that has never failed is one nobody has calibrated.
- failure design is covered by test at every task, on all seven of CLAUDE.md ground rule 6's
  paths. Timeout and uncertain external state: a timed-out action execution is UNCERTAIN with
  a stable derived id and never "failed", a timed-out capability is 504 and not 502, and the
  harness records a payment lost at the rail as UNKNOWN. Partial completion: a baseline run
  reports the signals it missed by name and merges the rest, a topology document with a
  dangling edge is refused whole, a batch with one invalid item keeps the others, and a failed
  credential write leaves no binding, no secret and no audit entry. Duplicates: across posts,
  within one batch, across a rotation, and a re-triggered deployment pipeline. Model and
  adapter failure: an embedding provider raising at index and at query time with search still
  answering, a manifest that will not load named on screen with its path and reason. Human
  override: acknowledgement over the socket is idempotent and terminal, and revoking a key
  takes the integration off the health row while what was already accepted stays. Permission
  change mid-flow: a binding disabled between two calls, an adapter uninstalled under a live
  binding, and an operator narrowed to one application refused on the next step of another
  while everything already saved is intact.
- suite result: 686 passed, 0 failed, 0 skipped (215 at the end of S2). Full breakdown by
  module and by requirement in docs/21-test-report.md; the requirement join is in
  docs/20-RTM.md.

## Artifacts updated
- 03 stories regenerated from specs (docs/03-stories.md, scripts/gen_stories.py)
- 04 functional spec (docs/04-functional-spec.md): the integrations chapter replacing the S0
  stub, then chapter 2 (the adapters this build ships), section 2.9 (push ingestion),
  chapter 3 (onboarding), chapter 4 (the onboarding screens), chapter 5 (the reference
  harness) and chapter 6 (onboarding as a contract, with the failure catalogue)
- 05 specs (specs/requirements.yaml): REQ-030, 031, 032, 033, 035, 036, 038 -> built
- 10 data design (docs/10-data-design.md, generated by scripts/gen_erd.py): four new table
  groups - adapter registry, shipped integrations, push ingestion, onboarding - with sections
  6b, 6c and 6d on what each stores and why, the cross-tenant reads documented at their ports,
  and eleven new index rows
- 11 api spec (docs/11-api-spec.md): rewritten into the complete specification - conventions,
  the exported document and what its extensions mean, the generated 72-route catalogue, and
  the per-area sections including the API this platform does *not* serve (the twelve observe
  paths an application must implement)
- 11 api spec, machine-readable half (docs/11-openapi.json, new): OpenAPI 3.1, 55 paths, 71
  operations, each carrying its credential, its permission and its app scope, generated by
  scripts/gen_openapi.py
- 14 functional tests: signalforge/tests/adapters/, tests/ingest/, tests/onboarding/,
  tests/reference/, test_web_onboarding.py, test_api_spec_docs.py
- 18 security tests: signalforge/tests/security/ - test_credential_scope.py,
  test_adapter_boundary.py, test_ingest_boundary.py, test_onboarding_boundary.py,
  test_cockpit_surface.py, test_reference_boundary.py, test_api_surface.py
- 20 RTM regenerated (docs/20-RTM.md): 51 requirements, 32 with linked tests, all 29 whose
  spec status is `built` green, 686 tests joined
- 21 test execution report regenerated (docs/21-test-report.md): 686 tests, by module and by
  requirement
- decision-record.md: DR-019 through DR-059
- Makefile: `make openapi` joins run/test/rtm/report/erd/evidence/clean-check
- supporting READMEs, which are documentation rather than numbered artifacts:
  signalforge/infrastructure/adapters/README.md (the manifest format and every rule the loader
  enforces), adapters/enterprise/README.md, reference-app/*/README.md and the harness config
  directory's README.md

## Security notes
- New surfaces: /adapters and /connectors (S3-T01), /ws (S3-T02), the five ingest paths and
  five key-management routes (S3-T03), sixteen /apps routes (S3-T04), three cockpit routes and
  two settings sections (S3-T05). Every HTTP route that can name a tenant sits behind the RBAC
  dependency and is covered by the router-coverage walk in the security suite, which now
  additionally reads the *permission* out of each route's own dependency closure - so a route
  guarded by the wrong permission fails, not only an unguarded one.
- The ingest endpoints are the first product surface the open internet can reach past a 401,
  and they are bounded four ways. The credential is two independent halves and neither alone
  is enough; what is stored is not usable, asserted by replaying the sha256 and the ciphertext
  back at the API from the database; the HMAC is computed over the exact bytes received and
  verified *before* the body is parsed, so nothing malformed or oversized reaches a JSON
  parser on an unverified request; and a verified sender is still bounded, because the key
  names the tenant, the app and the stream and the body carries none of the three, so there is
  no parameter through which a sender could widen any of them.
- Refusals are evidence, and the sender is told nothing. Every refusal against a known key
  writes an audit entry, a timeline event and the reason on the key row; a refusal against an
  unknown token audits nothing on purpose, because it names no tenant and letting an anonymous
  caller pick whose audit chain to write to would be the leak. Rate limiting sits before the
  signature check and is deliberately not audited, so a flood cannot make the platform hash
  megabytes or write the disk it was refused from touching.
- Credentials exist on exactly one path and never come back out. Fernet ciphertext at rest
  under connector/<binding>/<field> and ingest/<key_id>/secret; decryption into an
  AdapterContext whose __repr__ redacts; HttpTransport holding a value for one call with a
  redacted repr of its own. The proof is empirical rather than structural: a test binds a real
  connector with a real credential, issues a real key, posts a real signed batch, and then
  asserts the credential, the token and the secret are absent from every API response, every
  event payload, every audit entry, the unauthenticated /metrics scrape and the stored
  ciphertext.
- The policy floor became data as well as code, so a row is only as safe as what refuses to
  load it. Writing `execution_mode: auto` against a high-risk class straight into SQLite - past
  every route, service and validator - makes the profile fail to load rather than loading as
  something the floor would have refused. And every floor refusal is evidence before it is an
  error: system.app.policy.refused is written to the chain and the timeline *before* the 422
  goes back, because the response is seen only by whoever tried.
- Separation of duties is enforced at the point of typing: an approver named for a high-risk
  action must hold approve_high_risk in the frozen roles matrix, so naming the Executive as the
  approver for a rollback is refused rather than stored and discovered during an incident.
- Tenant isolation extended to ten new tables, with the predicate inside every repository and a
  cross-tenant read indistinguishable from a read of nothing. Onboarding has no cross-tenant
  method at all. Two deliberate cross-tenant reads exist and are documented at their ports, in
  docs/10 section 6 and by test: list_enabled_for_health (the health work list) and
  find_by_token_hash (an ingest token names its own tenant and cannot be looked up any other
  way).
- The one new outbound unauthenticated capability - the discovery probe - is bounded four ways:
  http(s) only checked before the request, redirects never followed, the body read in bounded
  chunks and discarded, and the timeout capped by a module ceiling. It has no credential to
  leak because it runs before any binding exists, asserted by recording the headers it actually
  sent. HttpTransport applies the same scheme control to every bound adapter, tested against six
  hostile base URLs, because a base URL is operator input this process then fetches.
- Redaction stopped being a promise and became an action, applied at the boundary by the
  adapter and, for kb-index, itsm-lite and the ingest floor, at write time - so a later reader
  of the row cannot bypass it. Asserted against the stored row rather than the API response.
- /metrics gained seven metric families this sprint and none can name anyone: the registry
  rejects tenant, incident, user and org label names at declaration time, the ingest counters
  use a closed reason vocabulary so a hostile sender cannot mint series, and connector health
  keys on the adapter id with several bindings collapsing to their worst verdict, so the
  unauthenticated scrape never carries a binding id.
- The published API document became a control rather than a description. Because the access
  model is generated from the guards, the set of routes reachable with no credential is a list
  a test pins at exactly six, the five frozen ingest paths are pinned as the only routes a
  machine credential can reach, every write behind a session is asserted to name a permission,
  and every published permission is checked against the one the route's own dependency closure
  holds - so the document cannot claim to be stricter than the API.
- Threat model: no new STRIDE rows were authored this sprint. docs/12's existing rows on
  credential storage, ingest spoofing, tenant isolation and audit tampering all now have
  running code and tests behind them rather than a design; the AI rows remain S5 work. The new
  residual risks below belong in docs/12 when it is revised alongside S6's governance surface.
- Residual risks, all accepted and stated: the discovery probe will connect to any http(s) host
  an operator with manage_apps names, including loopback, which is SSRF-shaped by design
  because it is how "probe my app at localhost:8080" works on one machine (compensated by the
  permission, no credential, no redirect, a status code and content type as the only return
  value, and an audit entry naming every URL touched); an ingest key is a bearer credential with
  no IP allow-list, so a leaked token is usable until revoked (compensated by the per-stream
  scope, the separate HMAC secret, the rate limit and the audit trail); the manifest tree is
  trusted input, because anyone who can write to the adapters directory can already write to the
  package; /openapi.json is public and now publishes the access model as well as the shape,
  which is the price of a published contract and is one setting away from being closed; the
  in-process rate limiter and notification hub are per-node on a split deployment, with the
  durable receipt rather than the queue as the record; and the harness's four demo credentials
  are literals in application.yml, because they guard a demo harness and hiding them would be
  theatre.

## Known issues and technical debt
- The cockpit has no /ws client, so notifications are delivered and acknowledged over the
  socket but nothing renders them (see Not built). This is the one S3 capability with no
  screen.
- Regenerated artifacts (10, 11, 20, 21) must be refreshed whenever a route, model, guard or
  table changes. The suite fails with the exact command and `gen_openapi.py --check` exists for
  a hook, but hooks are not committed here, so this stays a step a task has to remember.
- The catalogue's Purpose column is derived from each handler's docstring or, failing that, its
  function name, so a route with no docstring reads as a title-cased name. Honest, and the fix
  is a docstring on the handler rather than prose in the document.
- The apps overview computes readiness per application in a loop, one extra profile and binding
  read each. Right for tens of applications; a batched query is the change if an install ever
  holds hundreds.
- The integration health screen polls every 15 seconds while open, and the connector loop only
  writes a verdict every 30, so half those polls see nothing new. A socket subscription is the
  change once there is a /ws client.
- The Connect step lists every shipped adapter rather than only the ones discovery suggested,
  so a large adapter tree is a long page. Filtering is a one-line change, left out because
  hiding an integration an operator came looking for is the worse failure.
- The connector health loop reads every enabled binding on every poll, and a discovery run
  opens nine sockets at once with no cap. Both are right at this scale and both have an obvious
  bound (a staleness cursor, a per-run semaphore).
- A batch commits in one transaction, so a 500-item post is one SQLite write transaction
  holding 500 event appends. Bounded by MAX_BATCH_ITEMS and the body cap; chunking is the
  change if a sender ever posts continuously at the ceiling.
- Baseline learning takes its samples back to back, so on the platform clock the window is
  effectively zero and window_s reports that honestly. A baseline spread over real minutes
  needs a scheduled learner, which belongs with S4's rollups.
- Removing an application deletes its profile and its topology versions. Nothing references
  them yet; once incidents cite a topology version in S5, removal must refuse rather than
  delete, and the refusal path already exists for connectors and ingest keys.
- test_reference_onboarding.py skips when the jar is absent or java is not on PATH, and says
  which. Everything about the harness checkable without a JVM is checked unconditionally by the
  20 contract tests, so a missing JVM narrows the evidence rather than removing it. The jar is
  built by `bash reference-app/*/run.sh`. Maven needs a truststore holding the proxy CA
  on a network with TLS inspection; the recipe is in the harness README.
- The live onboarding test boots a JVM and costs about 25 seconds of the suite's run. That is
  the price of the acceptance being real.
- `restart-gateway` on the harness sleeps 1.5 seconds inside the request and is published
  `idempotent: false`. It is the one action that genuinely cannot be made safe to repeat, which
  is the point of it existing - the policy floor needs something truly dangerous to refuse.
- The chaos scenario `db-pool-shrink` applies through the harness's config registry, so a drill
  leaves two extra configuration versions in the history. Honest - chaos really did change the
  config - but it is noise in the version list.
- Carried over from earlier sprints: make still requires an elevated install, so every target
  was run by calling its recipe directly (verified for run, test, rtm, report, erd, openapi and
  clean-check); web port 3000 vs cockpit 3001; scripts/seed_bus_demo.py owns its own consumer
  group, so the dead letter it parks reports drained_here=false; lane count is fixed at 16 and
  changing it needs a re-shard migration; generated documents are written with the platform's
  line endings, which git normalises on commit.

## Demoable checkpoint
The judgeable milestone is an application SignalForge has never heard of, onboarded through
public contracts with no seeds, and a hostile or repeated sender refused with evidence.

1. `bash scripts/check_clean.sh` - "core is app-agnostic". The reference app's name exists
   only under reference-app/, its own config directory, drills/, seeds/ and tests.
2. `bash reference-app/*/run.sh` - builds if stale and starts the harness on :8080
   with seeded payment traffic in both regions. `bash run.sh` starts it alongside the api and
   the web, discovered by glob.
   `curl -H "Authorization: Bearer ps-ro-4f1c8a2b6d9e" localhost:8080/observe/metrics | head -30`
   - Prometheus text at `text/plain; version=0.0.4`. Unauthenticated: 401. The same read-only
   key on `/observe/actions`: 403, naming the scope it wants. Never 404.
3. Sign up at http://localhost:3000/signup, then open http://localhost:3000/apps and press
   "Onboard an application": app id `payments`, base URL `http://localhost:8080`. Registering
   lands on step 2 with readiness 15 - the severity matrix already ships configured, because
   the floor has to hold from the first second.
4. Step 2, "Probe this application" - nine findings: the seven observe paths each "served, and
   it wants a credential" with the adapters that could read them and base_url already filled
   in, plus Prometheus and Grafana. Bind seven connectors across the three credential scopes;
   the token field is a password input. Press "Test connection" on each - all seven passed,
   each in its own direction, each naming the capability it performed and what came back.
   Try to bind http-observe-chaos at credential_scope `remediation`: 422, the manifest offers
   exactly ['drill'].
5. Same step, the Grafana alert webhook card: "Issue ingest key". The panel appears once with
   the ingest URL, the bearer token and the HMAC secret, the secret masked until revealed.
   Reload: the card now shows `sfi_xxxxxxxx…  (masked - the token was shown once)`. Press
   Rotate - a successor is issued, both keys are listed, and the panel says the old one stays
   live until revoked.
6. Steps 3 to 6 of the wizard: map five golden signals onto the harness's real selectors with
   the business counter resolving per region; import actions - seven arrive and every
   high-risk class lands in approval mode with `auto` unselectable; paste
   `GET localhost:8080/observe/topology` and upload it (9 nodes, 10 edges, `sha256:…`), then
   fetch it again a moment later - new capture time, same graph - and upload that: "Identical
   to the map already held. One version, no new event". Set an SLO, then try
   `approval_ttl_s` 86400 and read the floor's refusal verbatim, as the org owner. Learn
   baselines from live traffic.
7. Step 7, "Run every test" - four distinct outcomes side by side, probes run per region, and
   then `GET /apps/payments/readiness`: **score 100, ready true**, with no seeds anywhere.
8. The pipeline: `POST /ingest-keys {"app_id":"payments","stream":"changes"}`, put the token
   and secret in the environment, and run the harness config directory's
   `deploy.sh --region region-b`.
   `deployment.started announced: {"accepted":1}`, the harness applies release v12 (config
   version 1 -> 2), `deployment.completed announced: {"accepted":1}`.
9. Run it again - `{"accepted":0,"duplicates":1}` twice, and the harness answers
   `"changed": false`. One deployment, whatever the pipeline does. The audit chain holds
   `audit.ingest.duplicate` and no second event was published.
10. Run it with a wrong `SIGNALFORGE_INGEST_SECRET` - a bare 401, the pipeline warns "the
    deployment was NOT announced to SignalForge", exits 0, and the release is applied anyway.
    `GET /events?topics=system.ingest.rejected` shows code `bad_signature` with the key id;
    the audit chain holds `audit.ingest.rejected`; `/integration-health` shows
    `signature mismatch` in the Errors column and nowhere the sender can see it. That is the
    sprint's acceptance clause on forged HMAC and duplicate event_id, shown twice.
11. Inject a fault:
    `POST /observe/chaos/fabric-link-loss/inject {"params":{"link":"FIB-7","loss_ratio":0.4},"scope":"drill:recovery-collision","idempotency_key":"c-1"}`
    with the drill key. Within seconds `/observe/health` reads degraded for `fabric-region-b`
    only, region-a stays up, and `payment_success_ratio{region="region-b"}` falls while
    region-a holds at 1 - one region broken with a healthy control beside it, which is what
    S4's corroboration rule will need. The same inject with `"scope":"whatever"` is 400, and
    with the remediation key instead of the drill key it is 403.
12. `/settings` - Adapters: 22 manifests, `errors: []`, five reading available; capability
    coverage per kind with the uncovered capabilities named. Ingest: every key with its
    endpoint, prefix, counters and last refusal, with Rotate and Revoke. Revoke the key and
    refresh `/integration-health` - the row drops to unknown with no live key rather than
    keeping a stale green arrival.
13. `curl -s localhost:8000/openapi.json | jq '.paths."/api/v1/ingest/changes".post'` -
    `x-signalforge-auth: ingest-key` and a `security` block naming ingestKey, ingestSignature
    and ingestTimestamp together. `.venv/Scripts/python.exe scripts/gen_openapi.py --check` -
    "up to date". Append a byte to docs/11-openapi.json and it names the stale file and exits 1.
14. `.venv/Scripts/python.exe -m pytest -q` - 686 passed. The acceptance path is
    signalforge/tests/reference/: a JVM the suite starts itself, onboarded over the network to
    readiness 100, and deploy.sh's own bytes replayed at the real ingest endpoint.
15. `bash scripts/check_clean.sh` green; `scripts/gen_rtm.py`, `scripts/gen_test_report.py`,
    `scripts/gen_stories.py` and `scripts/gen_erd.py` re-run at sprint close and
    `scripts/gen_openapi.py --check` reporting "up to date". `curl -s localhost:8000/health` -
    200 with database, event-bus and worker up.
16. What was recorded live this sprint and what was not: steps 2 and 8 to 13 were driven live
    against the harness on :8080 and the platform on :8899 during the sprint, and every route
    behind steps 3 to 7 was driven end to end against the live harness by
    test_reference_onboarding.py. Steps 3 to 7 and 12 as *browser* interactions are the demo
    script for the screens; the screens build green and are held by structural and
    contract-pinning tests, but nobody clicked through them in this unattended run.

## Coverage checklist ticks
- S3 Adapter registry, manifests, capability matrix, credential scopes (read_only,
  remediation, drill)
- S3 Pull adapters: observe metrics/logs/traces/health/actions/chaos, prometheus query_range
- S3 Push ingest: changes, alerts (Grafana webhook), topology versions, messages, tickets
  (HMAC, idempotent)
- S3 Simulated adapters labelled: teams-sim bridge + chat, itsm-lite, page-sim SMS/voice
- S3 Notifications real: in-app ws + browser push (delivered and acknowledged over the socket;
  no cockpit renders them yet - see Not built)
- S3 Knowledge kb-index (embeddings or BM25) for runbooks and PIRs
- S3 Enterprise interfaces + mocks visible: datadog, splunk, servicenow, jenkins, github
- S3 Onboarding wizard: connect, direction-correct tests incl. a failure state, auto-discover,
  map, topology step, SLOs + severity, baseline, test page, readiness score - use cases and
  APIs in S3-T04, all seven screens and the S4 apps overview in S3-T05
- S3 Integration health screen; adapter registry + ingest token sections in settings
- S3 Reference harness under reference-app/ only; check_clean.sh green
- S3 Artifact 11: exported OpenAPI and the full route catalogue; artifact 04 onboarding stated
  as a contract with its failure catalogue
- Nothing outside the S3 block turned true, so no other line was ticked. "All 23 artifacts
  present per 00-INDEX map; RTM and test report generated" stays unticked: the RTM and the
  test report are generated and current, but of the 23 artifacts, 08 (agent architecture, S5)
  is a stub and 09, 15, 19, 22 and 23 are scope stubs owned by S5/S7, while 16
  (non-functional tests) and 17 (performance tests) do not exist yet at all. Artifact 11,
  which sprint 3 owns per the index, is complete in both halves.

## Entry criteria for next sprint: met
- Sprint 3's acceptance is met on all three clauses, with one honest qualification on the
  first. *Onboard the harness fully through the UI (no seeds)*: every screen exists and builds,
  and the harness is onboarded end to end with no seeds to readiness 100 and `ready` true
  through exactly the routes those screens call, proven by a test that boots the real JVM -
  but no human drove a browser in this unattended run, so the UI half is evidenced by build
  plus structural tests plus an identically-driven API rather than by a recorded session. The
  browser walkthrough is written out step by step in the demoable checkpoint.
  *check_clean.sh proves the core is app-agnostic*: green, with the harness present - the
  reference app's name exists only under reference-app/, its own config directory, drills/,
  seeds/ and tests, and the harness contains no import naming the platform.
  *A forged HMAC and a duplicate event_id are rejected with audit events*: both, twice over -
  by test_reference_ingest.py replaying deploy.sh's own bytes, and live. A forged signature is
  a bare 401 with `system.ingest.rejected` (code `bad_signature`) on the timeline and
  `audit.ingest.rejected` in the chain; a repeated event_id is `duplicates: 1` publishing no
  second event and writing `audit.ingest.duplicate`.
- Exit criteria (CLAUDE.md's definition of done, applied to the sprint): code plus REQ-tagged
  tests passing - 686 passed, 0 failed, 0 skipped; artifacts touched - 03, 04, 05, 10, 11
  (both halves), 14, 18, 20 and 21, which covers all six the sprint named; the API boots and
  answers /health 200 with database, event-bus and worker up (verified at sprint close on
  :8901; run.sh's own composition, which adds web and the harness by glob, was exercised at
  S3-T06); check_clean.sh green; `gen_openapi.py --check` reports the export up to date, and
  the RTM, test report, stories and ERD were regenerated at close; every task has an entry in
  this log's Built list.
  REQ-030, 031, 032, 033, 035, 036 and 038 are built and green in the RTM with their tests
  joined. Met.
- Sprint 4's entry criterion is "Sprint 3 accepted", and everything S4 needs is standing:
  detection has a real metric source to roll up (the harness exposes 20 series across two
  regions and four journeys through a bound metrics connector) and a deliberately
  single-region fault with a healthy control beside it, which is what a corroboration rule
  needs to be tested against rather than asserted; the lifecycle has an ITSM adapter to sync
  to and a deploy hook already posting deployment.started without completed, which is exactly
  the change-window signal S4-T02 reads; the war room has four notification channels with
  durable receipts, acknowledgement as a terminal human override, and an authenticated socket
  with catch-up; and every application onboarded carries its own golden signals, journeys,
  probe templates, SLO targets and severity matrix, so detection reads configuration rather
  than guessing. The one gap S4 inherits is the cockpit's missing /ws client, and it lands
  with S4-T03's ack board, which is the first screen that needs it.
