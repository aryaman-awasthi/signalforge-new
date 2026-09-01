# 04 - Functional specification

Owner: engineering. Written per feature area as features land (artifact 04, due S3 onward).
Each section describes behaviour that exists in the running product, with the REQ id it
satisfies and the tests that hold it in place. Chapters 1 and 2 cover integrations, 3 to 5 cover
onboarding - what the platform decides, what the operator sees, and one real application taken
through it - and chapter 6 states onboarding as a contract an integrating team can implement
against. Sections for detection, the war room, the incident brain, governance and drills arrive
with their sprints. The API shapes behind every route named here are in `docs/11-api-spec.md`.

## 1. Integrations: the adapter registry (REQ-030)

### 1.1 What an adapter is

An adapter is a folder under the adapters tree containing a `manifest.yaml`. Installing an
integration is copying a folder and restarting; no core code is edited, and no application is
named anywhere in the platform. The manifest schema is fixed by SignalForge, its content is
declared by the adapter. The full field reference lives beside the code in
`signalforge/infrastructure/adapters/README.md`.

| Field | Purpose |
|---|---|
| `id`, `name`, `version`, `vendor`, `description` | identity and registry display |
| `kind` | one of the thirteen SPI kinds |
| `mode` | `pull` (SignalForge polls), `push` (the app posts to ingest), `invoke` (SignalForge asks for a change) |
| `capabilities` | which SPI methods of that kind this adapter really implements |
| `auth` | method and the names of the credential fields |
| `credential_scopes` | which of `read_only`, `remediation`, `drill` a binding may claim |
| `config_schema` | JSON Schema the wizard renders and the platform validates a binding against |
| `limits` | per-call timeout, call rate, result size, row count |
| `redaction` | policy id, fields and patterns stripped from anything the adapter returns |
| `availability` | `built_in` (a runtime ships with it) or `available` (interface known, nothing installed here) |
| `simulated` | true when the adapter fakes the integrated system; surfaces as a SIMULATED badge everywhere its data is used |

### 1.2 What the registry refuses

A manifest that breaks one of the rules below never becomes an adapter. The rejection is
recorded with its path and returned on `GET /adapters` under `errors`, and the other adapters
still load - one broken folder must not cost an installation its whole integration surface.

1. **Capabilities are SPI methods.** The catalogue is read off `contracts/spi.py`, so a
   manifest cannot promise something the contract has no name for. `ping` is implicit and
   required of every adapter.
2. **Mode and capabilities agree.** `invoke` declares at least one mutating capability;
   `pull` and `push` declare none, so a write cannot be smuggled behind a reader's mode.
   `push` must declare `normalize`; `pull` must declare a fetching capability.
3. **Credential scopes respect the floor.** An adapter that can change the integrated system
   may not offer `read_only`. A `chaos` adapter offers exactly `[drill]`.
4. **Limits are bounded by the domain.** A manifest may ask for less than the ceiling, never
   more.
5. **Redaction is explicit** for `logs`, `traces`, `collaboration`, `knowledge` and `itsm`:
   those carry free text out of the customer's estate into evidence and prompts, so the
   manifest must state a `redaction.policy_id` even when the policy redacts nothing.
6. **Ids are unique.** Two folders claiming one id is an install mistake; neither silently
   wins.

### 1.3 Connector bindings

A binding wires one tenant's one application to one adapter at one credential scope. The
uniqueness key is `(app_id, adapter_id, credential_scope)`: read-only and remediation are
different credentials with different blast radii, so they are two bindings, and revoking one
leaves the other alone.

- Config is validated against the manifest's `config_schema` when it is written, once.
- Credentials are stored Fernet-encrypted (REQ-015) under `connector/<binding>/<field>`. The
  binding row holds only the field *names*; no API response, log line, event payload or
  audit entry has ever contained a value.
- The binding, its credentials, its audit entry and its event are one transaction. A
  credential stored under a binding that does not exist is unreachable key material, and a
  binding whose credential write failed is a connector that silently cannot authenticate.
- Deleting a binding deletes exactly its own secrets.
- Disabling a binding keeps its configuration and history and removes it from the health loop
  on the next poll.

### 1.4 Capability matrix

Rows are adapters, columns are the capabilities of their kind, and a per-kind coverage view
answers "can this deployment do metrics at all". Interface-only entries are listed but never
counted as coverage: claiming an integration because its interface exists is exactly what the
table is meant to prevent. The matrix is computed from the manifests and the SPI, never
authored, so it cannot claim a capability nobody implemented.

### 1.5 Connector health

Every enabled binding joins the same poll loop as the platform's own database, bus and worker,
through the same `HealthSource` port - which is why one screen can show a vendor API and this
platform's own database in one list. The provider is re-asked every poll, so a binding created
or disabled while the process runs joins or leaves without a restart.

| Observation | Verdict | Why |
|---|---|---|
| ping succeeded | UP | measured |
| ping returned false | DEGRADED, DOWN on the third in a row | the adapter reached it and it said no |
| timeout | DEGRADED, DOWN on the third in a row | uncertain: the call may still be running |
| credentials rejected | DOWN at once | it will not heal by waiting |
| credential missing from the store | DOWN at once | the binding is broken, not the target |
| adapter folder removed under a live binding | DOWN at once | orphaned binding, needs a human |
| no runtime installed on this node | UNKNOWN | nothing was measured, and a green tick would be the most misleading thing the screen could show |

Health is durable on the binding row, so after a restart the screen shows the last verdict and
the last error rather than an empty table. A verdict lands as a `system.connector.health`
event on the owning tenant's timeline (signal plane), and only a *transition* is audited -
health is polled every thirty seconds, and an audit chain of identical heartbeats is one
nobody reads.

Three separations follow from a source knowing which tenant it belongs to:

- **Readiness.** `/health` reports this node's own components only. A customer's dead
  integration is not a reason for the process to answer 503.
- **Visibility.** `/platform/status` shows a tenant the platform's components and its own
  connectors, never another tenant's, even though one loop produced them all.
- **Metrics.** `/metrics` is unauthenticated, so gauges are keyed by adapter id, never binding
  id, and several bindings of one adapter collapse to their worst verdict.
  `signalforge_connector_bindings{state}` counts bindings without naming one.

### 1.6 API surface

`GET /adapters`, `GET /adapters/capability-matrix`, `GET /adapters/{id}`, `GET|POST
/connectors`, `GET /connectors/health`, `GET|PATCH|DELETE /connectors/{id}` - all `manage_apps`.
Shapes are in docs/11.

## 2. Integrations: the adapters this build ships (REQ-031, REQ-033, REQ-035)

Seventeen adapters ship with a runtime and five ship as interfaces. None of them names an
application: every one is configured by a binding, and the only file in the platform that names
an adapter is `infrastructure/adapters/builtin.py`, which lives inside the adapters tree.

### 2.1 The observe set

Any application that serves `contracts/observe.py` is observable and partly controllable with
no platform code written for it. Six adapters divide that surface, because a binding is per
capability set and per credential scope: reading an application's logs and being allowed to
restart it are different permissions held as different credentials.

| Adapter | Kind | Capabilities | Notes |
|---|---|---|---|
| `http-observe-metrics` | metrics | `query_instant` | parses Prometheus text exposition; selects with `name{label="value"}` using `=`, `!=`, `=~`, `!~`. A scrape carries no history, so it declares no range query - `prometheus` answers those. |
| `http-observe-logs` | logs | `search`, `aggregate`, `sample` | aggregation is computed from what was read and never extrapolated; sampling strides across the window, because the first n records of an incident are all from its first second. |
| `http-observe-traces` | traces | `search`, `get_trace` | span attributes pass through the declared redaction. |
| `http-observe-health` | health | `get_health`, `run_probe` | probes run from SignalForge against a URL the app exposes; a non-2xx is a result, not an error. |
| `http-observe-actions` | actions | `manifest`, `execute`, `get_execution` | see 2.2. |
| `http-observe-chaos` | chaos | `manifest`, `inject`, `status`, `abort` | drill credential only; refuses a scope that is not `drill:<id>`. |
| `prometheus` | metrics | `query_instant`, `query_range` | the range adapter baselines and burn rates are computed from. |

Each pings the endpoint it actually uses. Pinging `/observe/health` for all of them would
report a metrics binding healthy while `/observe/metrics` returns 500.

### 2.2 Executing an action safely

Two behaviours matter more than the call itself, and both are about not knowing:

- **A timed-out execution is UNCERTAIN, not failed.** The request may still be running and the
  change may already have happened; reporting failure would invite a retry that applies it
  twice. `get_execution(idempotency_key)` is the reconcile-before-retry lookup the gateway uses
  to find out what really happened (REQ-062, S6).
- **A repeated idempotency key is not a second execution.** The adapter asks the application
  whether that key already ran before it sends anything.

The imported action manifest is validated against the frozen schema, so an application cannot
publish a high-risk action with no approver roles and have it become executable.

### 2.3 Push adapters

`grafana-alert-webhook` and `deploy-webhook` have no outbound call: their whole job is one pure
`normalize`. Three translation decisions are deliberate, because a normaliser that gets them
wrong is a misled responder at 3am. An unrecognised severity becomes `warning`, never
`critical` (which trains people to ignore pages) and never `info` (which hides a real one).
Grafana's unresolved-end-time sentinel becomes null rather than a timestamp in year one. A
deployment webhook with neither a status nor an event name is *rejected*: guessing "completed"
would close a change window that is still open, and the change window is how a responder finds
the cause. An alert with no fingerprint gets an id derived from its name and labels, so a
re-notification is not mistaken for a new alert.

For a push binding, healthy means "SignalForge is mounted and accepting posts for this
binding". Whether anything has arrived is freshness, which the ingest API records.

### 2.4 Topology import

`topo-import` covers both directions - pull `/observe/topology`, or normalise a document the
wizard uploads - and both produce the same snapshot from the same content, because a pull and an
upload that disagreed about the version would make "has the map changed since the incident
started" unanswerable. Two rules are absolute: a document imports **whole or not at all** (a
graph missing half its edges looks complete and understates blast radius, so a dangling edge
rejects the document with the offending edges named), and every import is **versioned by its
content** when the document does not declare a version, so re-importing an unchanged map is not
a change.

### 2.5 Simulated integrations

Three adapters simulate an integrated system so that an installation with no ServiceNow, no
Teams tenant and no pager can still rehearse the whole lifecycle. `simulated: true` in the
manifest carries a SIMULATED badge from the adapter through the event payload to the screen.
Simulated means the *system* is faked; the record of what SignalForge did is durable, because a
rehearsal whose pages and tickets vanish on restart proves nothing.

- **itsm-lite** is a working ticket queue with `LITE-000001` references. Create is idempotent
  on `ticket_id` - ITSM calls are retried by everything, and a duplicate incident ticket costs a
  human real time - and a closed ticket cannot be reopened by a retrying machine, because
  closure records that a person signed off.
- **teams-sim** is a bridge that plays a written script *and* accepts live input, both landing
  in one channel. The script is materialised at its scheduled times when the channel is first
  touched rather than driven by a timer, which buys three things: it follows the platform clock
  (a drill at 100x plays the conversation at 100x, with no drill-aware code in the adapter), it
  is idempotent on replay because message ids come from the channel and the line's position, and
  it survives a restart because a schedule made of rows has nothing to lose.
- **page-sim** records the transcript an SMS or voice call would have carried - truncated at 160
  characters, or as the words that would be spoken - so a rehearsal shows what the responder
  would actually have received.

### 2.6 Notifications

Four channels, one promise: every notification gets a durable receipt **before** anything is
transmitted. A page whose delivery succeeded and whose record did not is a page nobody can
prove happened, and an escalation timer built on the transport rather than the record would
silence itself on a lost frame.

| Outcome | Status |
|---|---|
| a live cockpit session took the frame | SENT |
| nobody is connected, or the session is not keeping up | QUEUED - not a failure |
| the simulated pager wrote its transcript | SENT |
| a person acknowledged | ACKED, and terminal |

Sending the same `notification_id` twice returns the first receipt without transmitting again.
In-app delivery and browser push both ride the authenticated `/ws` socket - browser push is
raised by the cockpit's service worker rather than handed to a third-party push service, so a
deployment with no outbound internet still notifies and incident text never leaves the estate.
A session declares which channels it can render; a browser-push notification to a session that
has not is QUEUED for catch-up. On connect, a session is handed everything still waiting for
acknowledgement, which is what makes "nobody was looking" an acceptable outcome.

### 2.7 Knowledge

`kb-index` stores runbooks, PIRs and notes and answers scored searches. BM25 is the floor and
always runs: it is deterministic, needs nothing external, and works the moment a document is
indexed. When an embedding provider is configured its cosine similarity is blended in. A model
failure - at index time or at query time - degrades the *ranking* and never the search: the
document is still indexed, still found lexically, and the search still answers. This is the
same discipline the LLM provider is held to (REQ-051): the deterministic path is the product.

Redaction happens at index time, not at query time. A runbook's text reaches briefs and
prompts, so what must not be there must never be stored; a policy applied on the way out could
always be bypassed by a later reader of the same row.

### 2.8 Enterprise connectors: Available, not installed

`datadog`, `splunk`, `servicenow`, `jenkins` and `github` ship as manifests with no runtime.
That is a deliberate, visible state. Hiding them would suggest the platform cannot integrate
with them; shipping a mock under a `built_in` flag would let a demo show a working Datadog
integration while nothing was ever queried. They are listed, their capability rows are shown,
and the matrix never counts them as coverage. Adding a real runtime later is an entry in
`builtin.py` and a manifest field - nothing else in the platform changes.

### 2.9 Push ingestion: `/api/v1/ingest/*` (REQ-032, REQ-017)

Pull adapters are how SignalForge reaches into an application. Ingestion is the other
direction, and it is the only product surface an unauthenticated caller can reach at all - so
the shape of it is almost entirely about what happens *before* the body is parsed.

#### 2.9.1 The credential

A sending application holds an **ingest key**, issued through the wizard or `POST /ingest-keys`
by whoever holds `manage_apps`. It is two independent halves, and neither alone is enough:

| Half | Where it lives | What it proves |
|---|---|---|
| bearer token (`sfi_...`) | only its sha256 is stored, exactly as refresh tokens are | who is posting |
| HMAC-SHA256 shared secret | Fernet-encrypted in `secrets` under `ingest/<key>/secret` | the body is unchanged and recent |

Both are returned exactly once, in the 201. Nothing can show them again - a platform that can
show you a credential can show it to whoever takes over your session - so "I lost it" is
answered by rotating, not by looking it up. The screen shows a display prefix so two keys can
be told apart.

A key is scoped to one tenant, one `app_id` and **one stream**. That last one matters: a token
that leaks from a deploy script cannot be used to inject alerts, because posting it to
`/api/v1/ingest/alerts` is refused.

#### 2.9.2 Rotation

`POST /ingest-keys/{id}/rotate` issues a *successor* rather than overwriting. Both keys are
live, each with its own secret, until the operator revokes the old one. A sender cannot be
switched atomically - it is a configuration change in somebody else's system - so an overwrite
would drop every request in flight between the two deploys. The contract's optional
`X-SignalForge-Key-Id` header is what makes the handover safe: it names which key the sender
believes it signed with, and a mismatch is refused rather than quietly verified against the
other secret. Revoking destroys the secret, so a revoked key cannot produce a signature that
would verify - it is not revoked on paper only.

#### 2.9.3 What a request passes through, in order

1. **A bearer token, and only a bearer token.** It resolves - by hash - to one key, and that
   key carries the tenant and the app everything in the request will be written under.
2. **A per-key rate limit**, before any expensive work, and deliberately *not* audited: an
   audit row per refused request would let a flood write the disk it was refused from touching.
3. **The HMAC**, over the exact bytes received and inside the replay window. The body is read
   once as bytes, bounded, and verified *before* it is parsed, so nothing hostile reaches a
   JSON parser on an unverified request. The signature is not a nonce - a captured request
   inside the window still verifies - which is exactly why `event_id`, not the signature, is
   what stops it being applied twice.
4. **The stream**, checked after verification so the reason can be recorded honestly.

The sender always gets a bare `401 authentication failed`; it never learns from the response
whether it got the token, the signature or its clock wrong. The reason is written to the key,
where `GET /ingest-keys` shows it to whoever holds `manage_apps`, and every refusal against a
*known* key lands in that tenant's audit chain and on its timeline. A refusal against an
*unknown* token audits nothing at all, deliberately: it names no tenant, and letting an
anonymous caller pick whose audit chain to write to would be the leak, not the fix.

#### 2.9.4 What a batch does

The wrapper is all-or-nothing (422: a sender that names no source system or exceeds the
contract's 500 items is broken). Individual items are individually rejectable - a 202 listing
each one's index and reason - because one malformed alert in five hundred must not lose the
other four hundred and ninety-nine. A rejected item does not consume its `event_id`: the sender
fixes the payload, resends with the same id, and that is an acceptance.

`event_id` is the idempotency unit the frozen contract names, and a stored receipt is what
turns that sentence into a guarantee. A sender whose connection dropped before our 202 arrived
cannot know whether we recorded its batch, and the only safe answer to the retry it will
certainly send is to acknowledge it without applying it twice. The uniqueness key is
**(tenant, event_id)**: not per key, so a rotation cannot cause a double-apply; not global, so
two tenants that pick the same producer-assigned id neither block nor observe each other. A
duplicate is counted in the ack and evidenced with one `audit.ingest.duplicate` entry per
batch.

Everything a batch does is one transaction - the receipts, the events they became, the counters
on the key and any audit entry. A receipt written without its event would silently swallow the
sender's retry, which is the failure the whole design exists to prevent.

#### 2.9.5 Canonical first, adapter as translator

The endpoint speaks `contracts/canonical.py`. An application that implements the canonical
shapes is ingestible with no platform code written for it, which is what "onboard through
public contracts" means in this direction. When a sender speaks its vendor's shape instead, the
key names a **push connector binding**, and that adapter's `normalize` translates - so the
Grafana and deployment normalisers built in S3-T02 are live product code on this path, with
their own rules intact (an unrecognised severity still becomes `warning`, the unresolved
sentinel still becomes null). One posted item may become several events - a Grafana batch
normalises into one canonical alert per alert - and it is still one idempotency unit, so its
retry is one duplicate rather than three.

Nothing about the translator is fatal to the request. A binding disabled between two posts, an
adapter uninstalled under it, or a payload the adapter refuses, all reject the items that
needed translating and name why; the canonical items in the same batch still land. A binding
that could never translate the stream - wrong kind, wrong mode, no `normalize` - is refused
when the key is *issued*, so the operator finds out while wiring it rather than at 3am.

Two of the five streams cannot have a push adapter at all: the frozen SPI defines `normalize`
only on alerts, changes and topology, so `collaboration` and `itsm` can never be push-mode.
They still work, because the key is the credential and the adapter is only ever an optional
translator.

#### 2.9.6 Streams, topics and the rules that survive the boundary

| Stream | Canonical model | Topic |
|---|---|---|
| `changes` | `ChangeEvent` | `deployment.<status>`, or `change.<status>` for non-deployments |
| `alerts` | `Alert` | `alert.fired` / `alert.resolved` |
| `topology` | `TopologySnapshot` | `topology.updated` |
| `messages` | `CollabMessage` | `collaboration.message.received` |
| `tickets` | `Ticket` | `itsm.ticket.updated` |

Everything lands on the **signal plane** carrying its provenance: the sending system as the
external actor, `authority: observed`, the measured `freshness_s`, and the `simulated` flag the
sender declared. Two domain rules cross the boundary with it. A topology snapshot with a
dangling edge is refused whole - the pull importer's rule, in the push direction, for the same
reason: a graph missing half its edges looks complete and understates blast radius. And free
text is masked at the boundary, at write time, by a redaction floor in the domain
(`ingest-default`: email addresses and long digit runs, applied to message text, ticket titles
and descriptions, and alert annotations). It lives there rather than in a manifest precisely
because the two streams that carry the most human prose can have no manifest; `redaction.applied`
on the envelope means something was actually removed, not that a policy exists.

#### 2.9.7 Freshness

For a push binding, healthy means "SignalForge is accepting posts". Whether anything has
*arrived* is a different question, and a ping cannot answer it: this platform is the server, so
it is reachable whether or not the sender died a week ago. The ingest key answers it -
`last_event_at`, `freshness_s`, and accepted / duplicate / rejected counters, all on
`GET /ingest-keys`.

### 2.10 Calling an adapter

Every call goes through one path (`ConnectorInvoker`), because five checks have to happen on
every single one and none may be a caller's responsibility to remember: the binding exists in
this tenant and is enabled; its adapter is still installed and this node has a runtime; the
credential-scope floor allows this capability on this credential; the credential is decrypted
for this call and nothing caches it; and the call is bounded. The floor is re-checked at *call*
time rather than at bind time, which is what makes a permission change mid-flow safe - the next
call is refused even though the binding, the runtime and the credential are all still there.

## 3. Onboarding: the wizard and what it is allowed to decide (REQ-036)

An application is onboarded through public contracts only. Nothing in `api`, `application` or
`domain` names an application, an adapter or a vendor: the wizard reads the frozen observe
contract to know what to probe, the adapter registry to know what could read it, and the roles
matrix to know who may approve what. Every step is one commit of the change, its audit entry and
its timeline event, so what the platform is watching and what the audit chain says can never
disagree.

The seven steps and what each one is responsible for:

| Step | What it captures | What it refuses |
|---|---|---|
| Basics | app_id, name, tier, environment, owner, **base URL** | a non-kebab-case app_id, a base URL that is not an absolute http(s) root |
| Connect | per-adapter config rendered from the adapter's own `config_schema`, plus auto-discovery and a direction-correct test | a credential scope the manifest does not offer, a duplicate binding at the same scope |
| Map | golden signals incl. business counters, region label, journeys, probe templates, actions imported from the application's manifest | a selector that could never match, a probe or SLO naming an undeclared journey, a business counter outside the business family |
| Topology | an uploaded map, versioned by its content | a document with a dangling edge - imported whole or not at all |
| SLOs | SLO targets, the severity matrix, the policy defaults | an SLO with no error budget, burn rates that cannot tell fast from slow, a matrix that cannot express P0, and every floor violation in section 3.5 |
| Baseline | what normal looks like per mapped signal | a baseline computed from fewer samples than the domain's floor |
| Test | every connector in its own direction, every probe in every region, and the readiness score | nothing - it reports, it does not gate |

### 3.1 The base URL does real work

Basics is not a description form. The base URL it captures is what auto-discovery walks and what
pre-fills every adapter whose `config_schema` declares a `base_url` property. That is the whole
of "the app base URL prefills the observe adapters": the wizard renders the adapter's own schema
and the field arrives filled. Nothing is invented - an adapter that does not ask for a base URL
gets an empty prefill.

It is also the first place a control is needed. The platform later makes requests to whatever is
stored there, so a scheme that is not `http` or `https` is refused at the point somebody types
it, and so is a URL carrying a query string or fragment: a root URL is a root URL.

### 3.2 Auto-discovery asks two questions

Discovery is unauthenticated, writes no binding, and probes:

1. **the application's own base URL**, once per path in `contracts/observe.py` - so the question
   is "which parts of the contract do you serve?" rather than "are you the application we know
   about?";
2. **the machine's well-known monitoring ports** - Prometheus on 9090 and Grafana on 3000 -
   matched to adapters by the `vendor` their manifests declare, never by an adapter id compiled
   into the core. A deployment that installs a different Prometheus adapter gets that one
   suggested with no code change, and the URLs are configurable for a deployment that runs them
   elsewhere.

A `401` or `403` counts as **found**. Discovery has no credential, so that is the normal answer
from an application that protects its observe surface; treating it as absence would make
discovery useless against exactly the applications that are configured correctly. A `404` is a
definite no. Unreachable is unreachable, and says so.

What comes back is a suggestion per finding: the adapter, whether this node can actually run it,
its credential scope and field names, and its config already pre-filled. A push-mode adapter is
suggested as an **ingest key with a path to configure**, not as a connector to poll, because
suggesting a binding for something that posts to us would be direction-wrong.

### 3.3 A connection test is direction-correct or it is theatre

The three adapter modes are three different questions, and one answer would be wrong for two of
them:

| Mode | What the test does | What a green tick means |
|---|---|---|
| `pull` | pings, then performs one read-only capability chosen per kind from the frozen SPI, and reports what came back ("read 6 metric series", "health is degraded across 2 components") | SignalForge can read it |
| `invoke` | reads the manifest the application publishes and stops | SignalForge can see what it may ask for - **it never executes an action to prove it can** |
| `push` | calls nothing; reports whether a key exists and when something last arrived | traffic is actually arriving |

The push row is the one that matters most. A push binding tested by calling out would always
pass, because SignalForge is the server: it is reachable whether or not the sender died a week
ago. So it reads **pending** with the reason until real traffic arrives, and freshness - not
reachability - is its measure.

The failure states are as carefully separated as the successes, because the operator's next
action differs: a rejected credential, an unreachable host, a target answering 500 and a timeout
are four different messages; a connector nobody enabled is *skipped*, not red; and an adapter
with no runtime on this node is skipped with the availability named. Nothing retries - a test
that retries is measuring the retry.

A test is a real observation, so its verdict is written back to the binding's health columns.
That closes the gap S3-T01 recorded: a binding created between two health polls used to read
`unknown` with no detail for up to the poll interval, and now the explicit test answers
immediately, into the same columns the health loop writes and the integration health screen
reads.

### 3.4 Mapping: signals, journeys, probes and actions

Golden signals are `traffic`, `errors`, `latency`, `saturation`, `availability` and `business`.
Business is a first-class family rather than a metric tag, because a payments counter falling is
the signal that matters most and the one no infrastructure dashboard carries. Each mapping names
a metric selector - parsed with the same code the metrics adapter uses, so a selector that could
never match is refused while somebody is looking at the field - and a *direction*, because
`errors_total` rising and `success_ratio` falling are the same event and nothing in the string
says so.

The profile reports whether the mapping **can corroborate**: two independent families with one
customer-facing, which is what the detection rule needs. An operator learns their mapping cannot
corroborate anything now rather than during the first incident.

Journeys are the unit customers experience and the unit the severity matrix reasons about. A
probe template belongs to a journey, fans out across the declared regions, and carries a
synthetic header so the application can exclude the platform's own health traffic from the
business counters it reports.

Actions are **imported from the application's own manifest**, never typed. The import keeps what
the application declared beside what the operator decided, permanently: "the app said this was
low risk and we raised it" is a fact an audit will want. A re-import refreshes the manifest side
and preserves the override, an action withdrawn from the manifest is dropped, and an action the
application reclassified into a high-risk class loses any `auto` setting rather than carrying it
into a class that may never auto-execute.

### 3.5 The policy floor, at the point somebody types it

The SLO step is where a human first types something the floor has to refuse, and the refusal is
structural rather than advisory. Refused for an **org owner**, who holds every permission this
platform grants - the floor is not a permission:

- a **high-risk class can never auto-execute**. Not by configuration, not by an operator's risk
  override, not by an import. The six classes are frozen in `contracts/canonical.py`;
- a **risk override may raise risk, never lower it** - neither below what the application
  declared, nor below `high` for a high-risk class;
- an **approver must actually hold `approve_high_risk`** in the frozen roles matrix. Naming the
  Executive as the approver for a rollback is a separation-of-duties violation and is refused;
- a **high-risk action may not be left with no approver at all**;
- the **approval TTL is capped** (1 hour), and the high-risk classes cannot be removed from the
  set that requires approval;
- **audit is not a field**. It cannot be disabled, so it cannot be configured.

Two consequences worth stating. First, the floor is enforced on the *merged* policy, so a partial
edit cannot widen it by omission. Second, the floor is now data as well as code: a profile row is
only as safe as what refuses to load it, so a row doctored past every route and service still
fails to load rather than loading as something the floor would have refused.

A refusal is evidence. It writes an audit entry and a timeline event
(`system.app.policy.refused`) before the 422 goes back, because the error response is seen only
by whoever tried, and "somebody tried to make a rollback auto-execute" is exactly what an audit
wants to hold.

The severity matrix is a deterministic function from observed impact - journey tier, regions
affected, success rate, customer impact - to a proposed severity. Most severe matching rule
wins; nothing matching is the default. It proposes; a human declares (REQ-043).

### 3.6 Topology is versioned by content

An uploaded map's version is the document's own when it declares one and a content fingerprint
when it does not, ignoring capture time and node order. Re-uploading an unchanged document
therefore finds the existing version and produces **no new version and no second event** - an
operator who is not sure the first upload worked cannot manufacture a fake map change by trying
again. That is what keeps "has the topology changed since the incident started" answerable.

When the application has a bound topology adapter, the upload is normalised *through it*, so an
upload and a scheduled pull produce the same snapshot from the same content; otherwise the domain
importer does it and the profile records which. Versions are never overwritten and never pruned
while the application exists.

### 3.7 Baselines complete partially, on purpose

Learning a baseline reads the metrics binding several times and summarises each mapped signal
over what came back. One scrape answers every signal - the metrics contract is a point-in-time
exposition, so reading it once per sample and selecting locally costs one call instead of one per
signal, and every signal in a sample is measured at the same instant.

The failure design matters more than the arithmetic. A signal whose selector matched nothing is
**reported by name** and the signals that did match are still stored, because a run that
discarded four good baselines over one bad selector would be unusable against a real application.
Fewer usable samples than the domain's floor is a miss, not a baseline computed from two points.
A run that fails outright changes nothing. And a baseline belongs to its signal: re-mapping the
signals drops the baselines that no longer have one, rather than keeping a number nothing can be
compared against.

### 3.8 The readiness score

Thirteen weighted checks summing to exactly 100, asserted by a test, because a score that drifts
as checks are added is not comparable between two applications. Every point is a check somebody
can act on: each names the wizard step that fixes it and says in one sentence what is missing.

Five checks are **blocking** - a base URL, at least one integration, integrations actually
answering, at least one golden signal, and at least one SLO. `ready` requires every blocking
check to pass *and* the score to clear 80, so a high number can never mean "watchable" while a
blocking check is red. The outstanding list is ordered blocking-first, then by points lost, so it
reads as "what to do next".

The test page is the step that turns the last checks green: it runs every connector test in its
own direction and every journey probe in every declared region, stores the summary, and
recomputes the score from what came back.

## 4. The onboarding screens (REQ-036, REQ-030, REQ-032, REQ-033)

Chapter 3 is what the platform *does*; this chapter is what an operator *sees*. Three screens
and two settings sections, all of them behind `manage_apps` at the API - the route guard in the
cockpit middleware is UX over that and has never been the control.

### 4.1 Applications (screen 4)

One row per onboarded application: name, app id, tier, environment, base URL, owning team, and
the readiness score with its verdict. The score and the verdict are shown as **two** facts,
because they are two facts: a 90 with no SLO is not watchable, and a bar alone would imply it
was. Both come from the API - the thirteen weighted checks and the blocking rule live in the
domain, so a screen cannot call something ready that the platform would not.

Registering an application is on this screen because registering and onboarding are one motion:
a successful POST lands the operator on step 2 with the application already created.

### 4.2 The wizard (screen 3)

Seven steps, as seven **routes** rather than tab state, so a half-finished onboarding is a URL
that can be sent to whoever owns the credential for step 2. Nothing is staged across steps: each
step writes its own section through its own endpoint and the whole application is re-read
afterwards, because the domain can refuse or adjust what a step sent - a policy edit the floor
rejects, a topology upload that turns out to be unchanged, a risk override that a re-import
raised. A wizard that trusted its own local state would show the operator something the platform
does not hold.

The stepper's per-step progress is `readiness.steps`, keyed by the domain's own `WizardStep`, so
a step reads complete because the weighted checks say so and not because a form was submitted
once. A test pins the seven keys against `WIZARD_STEP_ORDER`.

**Step 2 has three card shapes**, because there are three genuinely different jobs:

| Card | What the operator does | Why it is its own shape |
|---|---|---|
| pull / invoke | fills fields rendered from the adapter's own `config_schema`, gives a credential, presses Test | SignalForge does the calling, so config and a credential are the whole job |
| push | copies an ingest URL, issues a credential, hands it over, later rotates it | nothing to configure on this side; the work is somebody else's system |
| simulated | one button | the honest thing to say is not "a shorter form" but "this one is not real" |

Credential inputs on a pull card are write-only: posted once, encrypted at rest under the
binding, and never returned. The card can say whether a credential worked and never what it was,
so re-entering it is how it is changed - there is no "show", because there is nothing to show.

A push card shows the ingest URL, the masked token prefix and a Rotate button. The full token
and the HMAC secret appear exactly once, in the panel that follows an issue or a rotate, and
that panel says so - the platform holds a sha256 and a Fernet ciphertext, so "I lost it" is
answered by rotating. Rotation is presented as issuing a **successor** with both keys live,
never as an atomic swap, because moving the sender is a config change in somebody else's system.

Auto-discovery lives on step 2 rather than before it: it needs the base URL, it writes no
binding and sends no credential, and its findings **pre-fill** the cards rather than acting on
their own. A guarded endpoint (401/403) is shown as found, because it is.

Step 3 keeps the actions panel. What may be changed is narrow and the platform enforces it, not
the screen: the auto option is disabled for a high-risk class, and the approver chips are the
roles that actually hold `approve_high_risk` in the frozen matrix - but a refusal, when it comes,
is the API's and is shown verbatim.

Step 5 states the floor in full rather than hiding the controls it constrains. An operator who
cannot see a rule cannot design around it, and every floor refusal is already in the audit chain
before the error is returned.

**Failure states are rendered, not swallowed.** A manifest that would not load is named with its
path on the Connect step, so an integration that is missing from the list has a visible reason;
an interface with no runtime reads "available, not installed"; an identical topology re-upload
says so, or an operator retries thinking nothing happened; a partial baseline run lists the
signals it missed with the reason and says that what was learned is kept; the test page shows
four distinct outcomes, because a push connector reading *pending* is waiting for traffic and an
adapter with no runtime is *skipped*, neither of which is failure; and a permission narrowed
mid-flow lands as a message saying what was already saved is intact.

### 4.3 Integration health (screen 23)

One row per integration, with the columns chosen so a row cannot lie about its own direction:
direction, status, last pull, last push, freshness, errors and SIMULATED. A pull row's freshness
is when SignalForge last read the integrated system. A push row's is when something last
*arrived*, joined from the live ingest keys - see `GET /connectors/health` in docs/11.

Ingest keys that name no binding get their own rows. Two of the five frozen streams can never
have a push adapter, so for those the key *is* the whole integration and it would otherwise be
invisible on the one screen meant to show every integration.

A key with no traffic and no refusals reads **unknown**, not up: SignalForge has no evidence
either way, and unknown is the honest word for that.

### 4.4 Settings gains adapters and ingest (screen 21)

The adapter registry section lists every manifest with its kind, direction, credential scopes,
availability and simulated flag, the capability coverage per kind with the uncovered
capabilities named, and - deliberately prominent - **the manifests that would not load**, with
their path and reason. A silently short registry is how an installation ends up wondering where
its integration went.

The ingest section lists every credential a sending application holds with its endpoint, masked
prefix, freshness, counters and last refusal, and offers rotate and revoke. The refusal reason
is readable here and nowhere else on purpose: the sender gets a bare 401, because a caller that
could tell "unknown token" from "bad signature" from "clock skew" has an oracle.

## 5. The reference harness: what an onboardable application looks like (REQ-038)

Everything in chapters 1 to 4 is written as though any application can be onboarded through
the frozen contracts and no code in this platform ever learns which application it is. That is
a claim, and a claim of that shape is worth exactly as much as the attempt to falsify it. So
`reference-app/` holds a real second application - a payments system written in Java, with its
own database, its own configuration repository and its own deployment pipeline - and the whole
of chapters 1 to 4 is performed against it.

It is demo data, not product (ground rule 7). Deleting `reference-app/` leaves a working
platform; the only thing lost is the evidence. `scripts/check_clean.sh` fails the build if the
harness's name ever appears in product code, and it is run by the suite with the harness
present, so the two facts are asserted together.

### 5.1 What the application has to do, and what it does not

The harness takes no dependency on this platform: no library, no generated client, no shared
package, no callback registration. A test walks every source file and fails on an `import` that
names the platform. The coupling in each direction is a wire format and nothing else:

| Direction | What crosses | Frozen in |
|---|---|---|
| Platform reads the application | the twelve `/observe/*` paths | `contracts/observe.py` |
| Application declares its actions | an action manifest document | `schemas/action-manifest.schema.json` |
| Application declares its faults | a chaos manifest document | `schemas/chaos-manifest.schema.json` |
| Application's pipeline announces a change | one signed `POST` to an ingest path | `contracts/ingest.py` |

Note what is *not* in that table. The platform is never given a way to deploy, and the pipeline
is never given a way to read telemetry. The harness issues four separate credentials - read
only, remediation, drill and deploy - and refuses each on any path that does not need it. That
is what turns the platform's credential-scope floor from a policy into a control: a read-only
binding attempting a rollback is refused twice, once here and once at the far end.

### 5.2 What the harness is built to make possible

A harness that always works proves nothing, and one that fails uniformly proves almost as
little. Three properties were built in because the rest of the product needs them to exist:

**A single-region incident with a healthy control region.** The application runs two regions
whose routes share only the parts they genuinely must. One region's primary path crosses a
shared fibre span the other's does not, so damaging that span degrades exactly one region while
the other keeps serving. Blast radius, corroboration and "should we fail over" are only real
questions when the answer could have been "no".

**An outcome that is genuinely unknown.** Every route crosses the settlement rail, because a
payments business cannot route around the thing that moves the money. A payment lost *at* the
rail is recorded `UNKNOWN`, never `FAILED`: the request reached the point where money moves and
the answer was lost, so failure would be a guess and a retry would be a second payment. That
state is what the reconciliation action resolves, and it is why anything the platform says
about uncertain external state can be demonstrated rather than described.

**A timeout that hides a success.** The application's configuration registry accepts an
injectable apply delay. With it set, a rollback takes longer than its caller is prepared to
wait: the caller times out and the change lands anyway. That is not a simulated bug, it is the
ordinary shape of a distributed change, and it is the only honest way to produce an execution
whose outcome is genuinely uncertain rather than merely labelled so.

### 5.3 Executions are written before they are attempted

The application's ledger keys its `executions` table on the caller's own idempotency key, and
the row is inserted *before* the side effect is attempted. Three things follow, and all three
are what the platform's reconcile-before-retry depends on:

- a caller that timed out can still find out what happened, by asking for its own key;
- a retry under the same key returns the original execution instead of acting twice;
- an application killed mid-action leaves a row reading `running` - an honest report of an
  unknown outcome rather than a missing one.

`GET /observe/actions/executions/{key}` answering 404 therefore means something precise: this
key never ran. It has to be distinguishable from an execution that ran and failed, and it is.

### 5.4 Manifests are documents, not a scan of the code

The application publishes its actions and its faults as JSON documents that a human wrote and a
human can review, and it refuses to start if a published action has no implementation or an
implementation is not published. That is stricter than generating the manifest by annotation
scan: a generated manifest cannot be wrong, which means it also cannot be reviewed. The suite
asserts the same agreement without a JVM, so drift fails a test rather than a demo.

Every high-risk action names approver roles, and the suite checks those roles against the frozen
matrix rather than against plausible-looking names - naming a role that cannot approve makes an
action unapprovable at exactly the moment it is needed. Every chaos scenario states a blast
radius, states its abort conditions, is marked simulated, and carries a maximum duration the
application enforces itself: an injection that outlives it is reverted by the application, so a
drill that ends badly, a controller that crashes or an operator who goes home does not leave a
fault behind.

### 5.5 The deployment pipeline

The configuration repository's `deploy.sh` needs `bash`, `curl` and `openssl` and nothing else.
It applies a release to one region and announces it with a signed POST of a canonical
`ChangeEvent`. Two behaviours are deliberate.

The `event_id` is *derived* from the release, the region and the status rather than generated.
Pipelines are re-triggered constantly - a retried job, a re-run stage, an operator making sure -
and if each re-trigger became another deployment in the timeline, "what changed just before this
incident" would be answered with noise. The repeat is acknowledged as a duplicate and applied
once. The application enforces the same rule from its own side: re-applying the same release
with the same settings reports `changed: false` rather than minting a configuration version
nobody made.

A failure to announce never fails the deploy. Unreachable platform, refused credential, no
credential configured at all: the script warns on stderr, applies the change and exits
successfully saying the deployment went unannounced. Monitoring does not get to gate shipping.
The one reversal is a failed *apply*, which is announced as `deployment.failed` before the
script exits non-zero, because an incident opened in the next five minutes is very likely about
it.

### 5.6 What was proven, and how

The suite starts the built application as a subprocess and onboards it over the network through
the same routes the wizard's screens call, with no seeds: discover finds the seven observe paths
and reports each as served-and-guarded; seven connectors bind with three different credential
scopes; every connection test passes in its own direction; five golden signals map onto the
application's real metric selectors including a business counter that resolves per region; the
seven manifest actions import with every high-risk class landing in approval mode and none
automatable; the application's own topology exports, uploads and - re-exported a moment later
with a new capture time and the same graph - re-uploads as one version and no second event;
baselines learn from live traffic with nothing missed; every test passes; and readiness scores
100 with `ready` true.

The pipeline half is proven the same way. `deploy.sh` runs as a subprocess against a recording
server, and the bytes and headers it actually sent - not a re-serialisation of them - are
replayed at the real ingest endpoint. They are accepted; the same run repeated is a duplicate
that publishes nothing; and a run signed with the wrong secret is refused with a bare 401, with
the reason on the key row, in the tenant's timeline and in the audit chain.

## 6. Onboarding as a contract (REQ-036, REQ-030, REQ-032, REQ-038)

Chapter 3 is what the platform does with an application, chapter 4 is what the operator sees,
chapter 5 is one application that went through it. This chapter is the one an *integrating team*
reads: what SignalForge needs, what it will never ask for, in what order it has to arrive, and
what happens to each part of it when something breaks. Every step names the routes it drives, so
an application that would rather be onboarded by a script than by seven screens can make the same
calls in the same order; the shapes are in `docs/11-api-spec.md` and `docs/11-openapi.json`.

### 6.1 What an application must provide

Onboarding is scored, not gated, and the score says exactly what is missing. Five checks are
**blocking**: without them an application can be registered, but nothing should claim to be
watching it.

| What SignalForge needs | Points | Blocking | Provided by | Without it |
|---|---|---|---|---|
| An application id, name, tier, environment and owning team | - | - | Basics | there is nothing to attach a role binding, an event or an incident to |
| A base URL that is an absolute `http(s)` root | 10 | **yes** | Basics | auto-discovery and journey probes have nothing to resolve against |
| At least one integration - a connector binding or an ingest key | 10 | **yes** | Connect | no signal arrives at all |
| Those integrations actually answering | 10 | **yes** | Connect | the map is a plan, not a feed |
| At least one golden signal with a selector that parses | 10 | **yes** | Map | nothing to evaluate |
| At least one SLO with an error budget | 10 | **yes** | SLOs | burn rate, and therefore proposal, is undefined |
| A business counter | 5 | no | Map | customer impact is inferred from infrastructure only |
| A region label | 5 | no | Map | per-region health cannot be separated, so a one-region fault reads as a whole-application fault |
| Journeys with probe templates | 10 | no | Map | severity has no customer-facing unit to reason about |
| Actions imported from the application's manifest | 5 | no | Map | the platform can advise and never act |
| A topology document | 10 | no | Topology | blast radius cannot be computed |
| A severity matrix | 5 | no | SLOs | the shipped default is used - it exists from the first second on purpose |
| Baselines | 5 | no | Baseline | anomalies have nothing to be anomalous against |
| A green end-to-end test | 5 | no | Test | nobody has confirmed the whole path since it was configured |

`ready` requires every blocking check *and* a score of 80. The weights sum to exactly 100 and a
test asserts it, so two applications' scores mean the same thing.

Note what is not on the list: an SDK, an agent, a library, a callback registration, a schema in
the application's database, or any awareness on the application's part that SignalForge exists.
The application serves the frozen observe contract - or posts to the frozen ingest paths, or
both - and the rest is configuration the operator types once.

### 6.2 What SignalForge does not ask for, on purpose

- **No credential wider than the capability.** Credentials are bound per scope: `read_only` for
  telemetry, `remediation` for actions, `drill` for chaos. A manifest that declares a mutating
  capability may not offer `read_only`, and the floor is re-checked at call time, not only when
  the binding is created.
- **No deployment access.** The platform is never given a way to release. The pipeline direction
  is inbound only, one signed POST, and it holds a key that reaches exactly one stream.
- **No write access to anything it reads.** A metrics, logs, traces, health or topology binding
  is bound at `read_only` and the SPI kind has no mutating method to call.
- **No standing execution.** An `invoke` binding is tested by *reading the manifest the
  application publishes*, never by executing something to prove it can.
- **No credential ever comes back out.** Connector credentials go in write-only and are stored
  Fernet-encrypted; an ingest token and its HMAC secret are readable exactly once, in the 201
  that issues them. Losing one is answered by rotating, which issues a successor and leaves the
  original live until it is revoked.

### 6.3 The order, and why it is an order

| # | Step | Routes | Needs first | Writes | Event |
|---|---|---|---|---|---|
| 1 | Basics | `POST /apps`, `PATCH /apps/{app_id}` | nothing | the application | `system.app.registered` / `.updated` |
| 2 | Connect | `POST /apps/{app_id}/discover`, `POST /connectors`, `POST /ingest-keys`, `POST /apps/{app_id}/connection-tests` | a base URL, for discovery to have a target | bindings, ingest keys, health verdicts | `system.app.discovered`, `system.connector.bound`, `system.ingest.key.issued`, `system.connector.tested` |
| 3 | Map | `PUT /apps/{app_id}/mapping`, `POST /apps/{app_id}/actions/import`, `PATCH .../actions/{action_id}` | an actions binding, to import from the manifest; nothing for the rest - a selector is parsed here, not resolved against live series | signals, region label, journeys, probes, actions | `system.app.mapping.updated`, `system.app.actions.updated` |
| 4 | Topology | `PUT` / `GET /apps/{app_id}/topology` | nothing; a bound topology adapter normalises the upload when there is one | a version, append-only | `topology.updated` |
| 5 | SLOs and policy | `PUT /apps/{app_id}/governance` | journeys, because an SLO names one | SLOs, severity matrix, policy defaults | `system.app.governance.updated`, or `system.app.policy.refused` |
| 6 | Baseline | `POST /apps/{app_id}/baseline` | mapped signals and a metrics binding | baselines per signal | `system.app.baseline.learned` |
| 7 | Test | `POST /apps/{app_id}/test`, `GET /apps/{app_id}/readiness` | whatever exists - it reports, it does not gate | the test summary | `system.app.tested` |

The order is a dependency order, not a ceremony. Discovery needs somewhere to probe; importing
actions needs the connector that reads the manifest; an SLO names a journey the mapping declared;
a baseline is a measurement, so it needs both a signal to measure and a binding to measure it
through. Everything else is free: an operator who already knows the map can skip discovery, and
an application with no actions manifest simply never scores those five points.

Each step is one commit of the change, its audit entry and its event, so the profile and the
audit chain cannot disagree about what the platform was told to watch and when.

### 6.4 Two directions in, and one that is neither

| Direction | The application's part | SignalForge's part | Freshness means |
|---|---|---|---|
| **pull** | serve the contract and hold still | polls on a schedule and on demand, with the manifest's timeout and rate limit | when SignalForge last read successfully |
| **push** | POST a signed batch when something happens | issues the credential, verifies, deduplicates on `event_id`, answers 202 | when something last **arrived** |
| **simulated** | nothing - there is no integrated system | runs a built-in stand-in and marks everything it produces SIMULATED | not applicable; the card says "built-in" |

A push integration cannot be tested by calling out and a pull integration cannot be measured by
waiting, which is why one connection test would have been wrong for two of the three modes and
why the health row carries two different freshness numbers. A push-only application is entirely
valid: it never lets the platform in, gets its changes, alerts, tickets and messages on the
timeline, and gives up the things that need reading - probes, baselines and the actions the
platform could otherwise offer.

### 6.5 Onboarding is re-entrant

Every step is a route with a stable URL, and every write is idempotent in the way that step
needs. A half-finished onboarding is a link that can be sent to whoever holds the credential for
step 2; nothing is staged in a browser and lost. Re-running a step is safe by design: an
identical topology document is one version and no second event, an actions re-import refreshes
what the application declared and preserves what the operator overrode, a re-test replaces the
summary, and rotating a credential issues a successor rather than swapping one atomically.

That matters more than it sounds. The alternative - a wizard whose steps only work in sequence
and only once - is the reason onboarding half-finishes in most tools, and a half-onboarded
application is exactly the one nobody is watching during the incident.

### 6.6 Where every onboarding failure lands

| Failure | Where it shows up | What the platform does | Held by |
|---|---|---|---|
| **Timeout** | a probe, a connection test, an actions import, a baseline read | reports unreachable rather than absent; a timed-out capability is `504` because the outcome is unknown, never `502`; the run keeps what it got | `test_discovery.py`, `test_connection_tests.py` |
| **Uncertain external state** | a push binding with no traffic yet | reads `pending` with the reason - never green, because SignalForge is the server and would always answer its own ping - and turns green when something arrives | `test_connection_tests.py`, `test_integration_health.py` |
| **Partial completion** | a baseline run over five signals where two selectors match nothing | stores the three, names the two with their reasons, leaves earlier baselines intact; a failed run changes nothing; a topology document with a dangling edge is refused whole | `test_baseline.py`, `test_topology_upload.py` |
| **Duplicates** | a re-uploaded topology, a re-triggered pipeline, a repeated batch | one version, one event; `event_id` deduplicates per tenant and the repeat is counted, not applied | `test_topology_upload.py`, `test_ingest_idempotency.py` |
| **Model / adapter failure** | a manifest that will not parse, an adapter with no runtime on this node, a target answering 500 | the broken manifest is listed with its path and reason and the rest of the registry still loads; a runtime-less adapter is bindable and honestly `unknown`; a 500 is a named failure on the connector row, never a silent success | `test_registry.py`, `test_connector_health.py` |
| **Human override** | a risk override, a disabled binding, a revoked key | the override survives a re-import unless the application itself raised the risk; a disabled binding leaves the health loop on the next poll and keeps its last verdict; a revoked key takes the integration off the health row while its receipts survive | `test_mapping.py`, `test_integration_health.py` |
| **Permission change mid-flow** | an operator narrowed to one application between two steps | the next write is refused with a 403 naming the permission, everything already saved stays, and the screen says so rather than blanking | `test_onboarding_isolation.py` |

The floor's refusals are the eighth case and behave the same way: `422` with the domain's own
sentence, an audit entry and a `system.app.policy.refused` event written *before* the response,
because the response is seen only by whoever tried.

### 6.7 Offboarding, and what survives it

`DELETE /apps/{app_id}` answers `409` while any connector binding or live ingest key still points
at the application: removal is not a way to orphan a credential. With those gone, the application,
its onboarding profile and its topology versions are deleted, and its secrets are destroyed with
the bindings that owned them.

What does not go is the record. Events already on the timeline, audit entries, ingest receipts and
seals are the history of what happened while the application was watched, and nothing in the
onboarding path can delete an event - the only code that removes a recorded event is the
retention sweep, it works on the signal plane only, and it refuses to cross a seal. Offboarding
therefore stops the watching and keeps the evidence, which is the correct way round.

## 7. Detection: steady state to a proposal (REQ-040, REQ-041, REQ-042, REQ-046)

Onboarding ends with an application whose golden signals, journeys, probe templates, SLO targets
and severity matrix are all stored. Detection is what reads them. It runs continuously, it
concludes in public, and it stops one step short of the decision: **it proposes, and a human
declares** (REQ-043). There is no code path in this chapter that creates an incident.

### 7.1 The loop, and why it is four jobs

    metric-rollups    every 5s    one scrape, folded into five-second buckets
    detection         every 5s    evaluate the buckets and announce what changed
    probes            every 30s   run each journey probe in each declared region
    rollup-retention  every 1h    age out rollups and probe results

One scrape per tick answers every mapped signal, because `/observe/metrics` is a point-in-time
exposition: reading it once and selecting locally costs one HTTP call instead of one per signal,
and every signal in the result is then measured at the same instant - which is what makes
"errors rose while the business counter fell" a statement about one moment rather than two.

Polling and evaluating are two jobs rather than one so that each owns its failures: an
application whose profile will not evaluate must not be able to stop the scrape that feeds every
other application's baseline. The probe cadence is deliberately six times slower than the metric
one, because a synthetic probe is real traffic against somebody's production system and
detection that floods the application it is watching is not detection.

### 7.2 A bucket is one observation

Rollups live in a second database, `rollups.db`, keyed on
`(tenant, app, signal, region, bucket_start)`. That key is the idempotency the whole detector
rests on. The poller is a loop on a timer, so a slow tick that overlaps the next, a restart
mid-sweep, or an operator forcing an evaluation can all land two polls inside one five-second
bucket - and two polls must not become two samples, because a duplicate that moved a baseline
would be a duplicate that hid an incident. The second write is counted as a duplicate and
changes nothing.

The database is separate for three reasons, all about failure: a five-second write loop has no
business sharing a transaction with the audit hash chain; everything in it is derived and can be
rebuilt by letting the poller run, so pruning it hourly is safe; and its rows age out in hours
while the incident plane is kept and sealed, and two lifetimes in one file is how the wrong
thing eventually gets deleted.

### 7.3 What normal is, and what stops it moving

A baseline is **rolling**: an application at two percent errors on a Tuesday afternoon is not the
same application at two percent during a release freeze, so the comparison is against its own
recent past. Three rules keep it honest.

- The bucket being judged is never in its own baseline, and the reference window stops four
  buckets short of it. An anomaly has to last three evaluations, and a baseline that had already
  absorbed the first two would have widened its standard deviation by exactly the size of the
  event it is meant to notice.
- While a signal is breaching the baseline is **held**. Without that, a breach that lasts long
  enough teaches the window that it is normal, and after a minute the detector reports that
  everything is fine while the application is still on fire. An anomaly ends when the signal
  recovers, not when the detector gets used to it.
- Until there is a window to roll over, the onboarding-time baseline is used and says so
  (`baseline_source: onboarding`). A number learned in five scrapes during a wizard step is
  usable and should not pretend to be more.

Whether a series is a **level** or a **counter** is derived rather than configured: Prometheus
text exposition does not say whether a number accumulates, and the profile records which
direction is bad rather than which shape the number has. A window that never decreases and
increases in at least half its buckets is a counter and is evaluated as a per-second rate;
anything else - including a gauge that made one large step - is a level. `errors_total` rising
forever is not an anomaly; `errors_total` rising four times faster than it was is.

### 7.4 Anomaly: a breach sustained across three evaluations

One evaluation past a threshold is a garbage-collection pause, a redeploy, a slow disk or a
neighbour's backup job. An anomaly is a breach that is still there on the third consecutive
evaluation - fifteen seconds at the five-second cadence.

| Seen | Streak | Status |
|---|---|---|
| nothing comparable | reset to 0 | `stale`, or `unknown` if never measured |
| inside the threshold | reset to 0 | `healthy` |
| past the threshold, streak < 3 | +1 | `breaching` |
| past the threshold, streak >= 3 | +1 | `anomalous` |

One clean evaluation clears the streak outright - not a decay, because a streak that survived
recovery would eventually fire on three unrelated blips an hour apart. Missing data neither
advances a streak nor clears one: it lands `stale`, which is **not** healthy. A detector that
reads silence as health is worse than no detector, because it is confidently wrong at exactly the
moment the thing it was watching stopped answering. The streak is durable, so a restart during a
developing incident resumes counting rather than adding fifteen seconds to the detection time.

### 7.5 SLO burn: fast proposes, slow opens Watch

    burn = observed error ratio / (1 - objective)

At 1x the budget lasts exactly the window it was sized for. Two windows, both using the
application's own multipliers from the SLO step:

| Window | Default multiplier | Effect |
|---|---|---|
| 5 minutes | 14.4x | **proposes** an incident |
| 30 minutes | 6.0x | opens **Watch** |

The indicator is the mapped signal the SLO names, and it must be a ratio: a request *count* says
nothing about what fraction of requests failed. A signal that is not a ratio produces a burn
observation saying exactly that and no verdict, because a burn rate that is silently wrong is
worse than one that is missing. A success ratio is inverted into an error ratio and an error
ratio is read as it stands - which of the two an operator mapped is what `direction` records, so
a budget can be read off a signal called `payment_success_ratio` with no naming convention. The
**worst region** burns, not the average of every region: a two-region deployment exists so one
region can fail while the other keeps serving, and averaging is how a total failure in one reads
as half a problem.

### 7.6 Corroboration: the gate a proposal has to pass

One family of signals going wrong is a story with one witness. Latency alone is a slow disk, an
error counter alone is a noisy client retrying, saturation alone is a backup job. A proposal
needs **two independent signal families, at least one of them customer-facing** - availability,
business or errors, or a failing synthetic probe, which is the platform's own first-hand evidence
that a journey does not work.

Independence is by *family*, not by signal. Two error metrics on two services are one family and
corroborate nothing: they are the same measurement taken twice, and counting them as two
witnesses is how alert storms turn into incidents nobody declared. Corroboration counts
`anomalous`, never `breaching`, so the sustained rule cannot be bypassed by having two noisy
families at once.

Every conclusion carries its evidence refs - `rollup:<signal>/<region>@<bucket>`,
`slo:<slo>/<window>/<region>`, `journey:<journey>/<region>`, `alert-cluster:<id>` - each of them
re-readable. A proposal an operator cannot drill into is an assertion, and the whole design of
this product is that a human decides.

### 7.7 Health score: weighted, renormalised, worst region wins

| Family | Weight | Why it is worth that much |
|---|---|---|
| availability | 30 | not being there is the worst thing that can happen |
| errors | 25 | being there and failing is the second worst |
| business | 20 | payments not settling is what the business actually notices |
| latency | 15 | slow is bad, but slow is still working |
| saturation | 10 | a leading indicator, not an outcome |

Traffic carries **no** weight. It is context, not health: traffic falling can be an outage or a
Sunday, and a score that could not tell those apart would drift every weekend. It is still
mapped, still rolled up and still available as corroborating evidence - it just does not score.

Only families that are mapped *and measured* count, and the score is renormalised over the weight
actually covered, so an application with no saturation metric is not permanently capped at 90 for
a signal it does not emit; `covered_weight` is reported beside the score so a 100 out of 45
cannot be mistaken for a 100 out of 100. A stale family scores as unmeasured rather than as
healthy and drops out of the covered weight. Within a family the **worst** signal speaks, because
averaging would let four healthy error metrics bury the one that is on fire. Across regions the
**worst region wins**: an application at 100 in one region and 20 in another is a 20.

### 7.8 Alert storms: clustered by time and topology, suppressed with a count

One fault produces many alerts - on the database, on every service that calls it, on the queue
backing up behind them, and on the synthetic checks in front of all of it. Clustering is
deliberately mechanical, because a responder has to be able to disagree with it: two alerts join
when they are within two minutes of each other **and** the topology nodes they name are within
two hops in the application's own map. An alert whose labels name no node clusters only with
alerts of the same *name* inside the window - it would be easy to sweep the unmapped ones into
whatever cluster is nearby, and that is how a second, unrelated fault gets suppressed during an
incident.

The **root** is the alert on the most depended-upon node in the cluster: `A depends_on B` means a
failure of B explains A, so the node the largest number of cluster-mates point at is the one
worth reading first. Ties break by earliest start then by alert id, so the same storm always
names the same root and a screen that re-renders every five seconds does not flicker between
explanations. Children are suppressed **with a visible count** and nothing is deleted: the
cluster carries every member id.

### 7.9 Severity: proposed from measured impact, declared by a person

The matrix is the application's own, shipped configured with the P0/P1/P2 defaults and editable
in the SLO step. Detection supplies the four numbers it reasons in:

| Matrix input | Where it comes from |
|---|---|
| journey tier | the most important journey with a failing probe, or with a breached SLO |
| regions affected | regions holding an anomalous customer-facing signal or a failing probe |
| success rate | the worst measured journey success rate, else the worst customer-facing ratio signal |
| customer impacting | any anomalous customer-facing family, or any failing probe |

Each is a measurement and each is reported beside the proposal, so "P0" is visibly the
consequence of "tier-1 journey below 80 percent in both regions" rather than of a model. A probe
that could not be *run* is `unknown` and contributes to none of them: "we could not check" and
"the journey is broken" lead to opposite next actions.

The proposal carries `requires_human: true` and `declared: false`, and the value object refuses
to be constructed any other way. Nothing in this chapter can declare.

### 7.10 The stance, and what it means to a person

| Stance | When | What a person sees |
|---|---|---|
| Steady | nothing is breaching | nothing |
| Watch | a breach is being observed, a slow burn is running, or a journey probe is failing | a quiet "AI is checking" banner |
| Proposed | corroboration holds **and** a fast burn crossed, a customer-facing family is anomalous, or a journey failed three probe runs | a proposal sheet with evidence and a Declare button |

Watch opens on a *breach*, not on a sustained anomaly, because the first evaluation past the
threshold is exactly when a quiet "we are looking at this" is useful - and it is fifteen seconds
before anything could be proposed. Proposed requires corroboration, so a single noisy family can
reach Watch and can never reach a proposal, however long it lasts.

### 7.11 Where every detection failure lands

| Failure | Where it lands | Held by |
|---|---|---|
| **Timeout** | a timed-out scrape writes no rollup, is announced **once** per outage rather than once per tick, and the signals it fed go `stale` - never healthy | `test_rollups.py` |
| **Uncertain external state** | a probe the platform could not run is `unknown`, never `failed`, and contributes to no success rate and no severity proposal | `test_probes.py` |
| **Partial completion** | a scrape where two of five selectors match nothing writes the three and names the two with their selectors and what they matched | `test_rollups.py` |
| **Duplicates** | two polls inside one five-second bucket are one observation; two probe sweeps at one instant are one measurement | `test_rollups.py`, `test_probes.py` |
| **Model / adapter failure** | a signal with no window and no onboarding baseline reports `learning: n of 6 reference samples` rather than inventing a threshold; an SLO whose indicator is not a ratio reports that instead of a number | `test_anomaly.py`, `test_burn.py` |
| **Human override** | forcing an evaluation is a human act with an audit entry, and it proposes exactly what the loop would have proposed; reading the view advances nothing | `test_detection_flow.py`, `test_anomaly.py` |
| **Permission change mid-flow** | a connector disabled between the sweep's work list and the call yields `unknown`, not a failed journey; an operator narrowed to one application is refused on another and everything already measured survives | `test_probes.py`, `test_detection_boundary.py` |

Removing an application removes what the detector believes about it - the signal states and the
snapshot - after the removal commits and never inside it, because a derived store that failed to
clean up must not roll back the removal the operator asked for.

## 8. The incident lifecycle: a proposal becomes a decision (REQ-043, REQ-044)

Chapter 7 ends one step short of an incident on purpose. This chapter is that step and the four
that follow it, and the single rule it is built around is that **the platform never takes any of
them**.

### 8.1 Six states, in two halves

| State | What it means | Who moves into it |
|---|---|---|
| `steady` | nothing is breaching | detection |
| `watch` | something is being observed - the quiet "AI is checking" banner | detection |
| `proposed` | corroborated evidence and a severity the application's own matrix computed | detection |
| `active` | a person declared an incident | **a human, only** |
| `recovering` | a person says a mitigation is applied and is being validated | **a human, only** |
| `closed` | a person signed it off | **a human, only** |

The first three are *stances*. Detection owns them, they are recomputed from measurements every
five seconds, and no incident row exists. The last three are an incident, and one exists because
somebody pressed a button. `GET /apps/{app_id}/lifecycle` publishes `source` for exactly this
reason: the same six words name both halves, and a screen that could not tell "the detector
believes this right now" from "a person decided this" would be describing a product this
deliberately is not.

An application returns to a stance when its incident closes. `active -> steady` is not a
transition: an incident does not stop being an incident because the metrics recovered.

### 8.2 The floor: three acts reserved for people

`assert_transition` refuses a non-human actor on every move into, through and out of an incident,
and `assert_severity_change` does the same for a re-grade. Both live in the domain, so a use
case, a future agent, a drill and a hand-written script all meet the same refusal - and the
refusal is a 422, not a 403, because there is no permission that grants it and therefore nothing
for a 403 to name.

There is exactly one move a machine may make inside an incident: `recovering -> active`. A
regression during validation escalates, and escalation is the safe direction; it cannot create an
incident, relax one or end one. S6's revalidation loop (REQ-065) is built on that one edge.

**Closure is terminal for everyone.** Nothing reopens a closed incident - not a machine, and not
another person. Closure is the record that a human signed off, and a system that can un-sign it
is a system whose record means nothing. A regression after closure is a *new* declaration, by a
new person at a new time, which is what the record should say.

### 8.3 Declaring

`POST /incidents?app_id=…` behind `declare_incident`. It reads the detector's current belief
without advancing it - a declaration must not be able to age a breach into an anomaly, and the
proposal on the record has to be the one that was on the screen the person was looking at - and
then commits four things in one transaction: the incident, the transition that records who moved
it, the audit entry, and the incident-plane event.

Three facts are captured that cannot be recovered later:

- **the proposal**, copied rather than referenced. The rollups its evidence refs point at age out
  in hours; an incident that could only explain itself by re-reading live telemetry would explain
  itself differently every time it was opened.
- **the stance it was declared from**, so "declared straight out of steady" is visible as the
  judgement call it was.
- **what was changing**, copied from the open change windows (8.6).

The severity is the person's either way, and the record says which way. Omitted, the severity the
matrix proposed is accepted and recorded as `proposed`; supplied, it is recorded as `chosen` and
`overrode_detection` is true. With no proposal *and* no severity the declaration is refused with
a 422 that names what it needs - the platform will not invent a grade.

A declaration while one is already open is refused with a 409 naming the incident to join.
Two commanders pressing Declare seconds apart is what a pager does to a rota, and the check is
re-read inside the declaring transaction rather than only before it.

### 8.4 Running it, and reading it

| Route | Permission |
|---|---|
| `POST /incidents` | `declare_incident` |
| `PATCH /incidents/{id}/severity` | `change_severity` |
| `POST /incidents/{id}/state` | `declare_incident` |
| `POST /incidents/{id}/close` | `close_incident` |
| `POST /incidents/{id}/itsm-sync` | `declare_incident` |
| `GET /incidents`, `GET /incidents/{id}` | **none** - a session in the tenant |

The read path is the one asymmetry, and it comes out of the frozen roles matrix rather than a
preference: `ROLE_PERMISSIONS[Role.SME]` is empty, so the person most likely to be paged holds no
permission at all. Any permission on the read path would lock the incident out of exactly the
people it exists to reach. The reads are therefore session-scoped to the caller's own tenant and
carry none - the same decision `/ws` records as DR-031 - and a cross-tenant read answers the way
a read of nothing does.

The incident-scoped writes need the application to evaluate a per-application role binding
against, and it is not in the URL, so `deps.require_incident` resolves the incident first and
authorizes against the application it names. A commander bound to one application is bound to its
incidents and to no others.

Re-submitting the severity that is already set is a no-op: no transition, no event, no ticket
update. A screen re-posting what is true must not fill the timeline people are reading during an
incident. Moving to the state it is already in is a 409 rather than a silent success, because
that one is a person clicking the wrong button.

Every move appends to `incident_transitions`, which is append-only and never summarised into the
incident row: "who moved this to recovering, when, and what did they say" is what a
post-incident review reads first, and a current-state column cannot answer it. A severity change
is recorded as a transition whose `from_state` equals its `to_state` - the grade moved, the state
did not - so one ordered list is the whole history.

### 8.5 ITSM sync

On every lifecycle move, and on a 30-second sweep for anything unsettled, the incident's ticket is
brought into line through the application's own bound ITSM connector - the shipped `itsm-lite`
queue, or a real one, through the same `ConnectorInvoker` and the same credential-scope floor as
every other adapter call.

    active      -> ticket in_progress
    recovering  -> ticket in_progress
    closed      -> ticket closed

`recovering` is deliberately not `resolved`: recovering means a mitigation is being validated,
and resolved is a claim about the outcome that the platform has no business making on a person's
behalf while the validation is still running. Because ITSM systems refuse skipped states, closing
an incident whose ticket is in progress is two updates - resolve, then close - walked one step at
a time.

The sync **never raises**. A ticket queue that is down, slow, misconfigured or absent must not be
able to stop a P0 being declared, so the declaration commits first and the ticket is attempted
after. What happened lands on the incident's own row in five states:

| State | What happened | What the retry does |
|---|---|---|
| `not_configured` | no ITSM connector is bound to this application | nothing - there is nothing to sync to |
| `pending` | not attempted yet | attempt it |
| `synced` | the ticket matches the incident | nothing until the incident moves |
| `failed` | the ITSM refused, and said why | attempt again; the reason is on the row |
| `uncertain` | a call timed out - the ticket may or may not exist | **reconcile, then retry** |

`uncertain` is the state this design exists for. A create that timed out is not a create that
failed: retrying it blindly is how an incident ends up with two tickets, and treating it as done
is how it ends up with none. The retry calls `get` first - the ITSM equivalent of the actions
SPI's reconcile-before-retry - and only creates when the queue itself says the ticket is not
there. A person can force the attempt now with `POST /incidents/{id}/itsm-sync` rather than
waiting for the sweep.

### 8.6 The change window (REQ-044)

The signal is not "somebody deployed". It is **`deployment.started` with no
`deployment.completed`**: a change that is in flight right now. That is what earns a chip beside
a health score, because the first question during an incident is "what changed", and "something
is changing *now*" outranks every entry in a history.

The chip is fed by real deploy-hook calls. A pipeline posts canonical `ChangeEvent`s to the frozen
`/api/v1/ingest/changes` path with its HMAC; the events land on the timeline; a consumer group on
the bus folds them into `change_windows`, keyed on `(tenant, app, change_id)`. Writing through the
dispatcher's own unit of work means the projection, the idempotency-ledger row and the consumer
offset commit together, so catch-up after a restart and an operator requeue are both harmless.

The fold survives the three things a real pipeline does:

- **duplicates.** A re-triggered pipeline announces the same change again. Applying an event
  twice produces an equal window.
- **out-of-order delivery.** A `completed` can be recorded before its `started`. Nothing reads
  arrival order: the earliest `started_at` wins and a completion, once seen, is never unseen, so
  a late start cannot reopen a finished deployment.
- **silence.** A pipeline that dies between the two calls leaves a window open forever. It is not
  closed on a guess - nobody knows whether the deploy landed - but past four hours it is marked
  `stale` and the chip goes out, because a chip that stays lit for three days is a chip people
  stop seeing. The window is still listed, with "started 3.1h ago and never announced completion".

`failed` and `rolled_back` end a window too, and which of the three endings it was is recorded
rather than collapsed: a rolled-back deployment is *more* interesting as evidence, not less.

### 8.7 Where every lifecycle failure lands

| Failure | Where it lands | Held by |
|---|---|---|
| **Timeout** | a ticket create that timed out is `uncertain`, never `failed`; the incident is declared regardless | `test_itsm_sync.py` |
| **Uncertain external state** | the retry calls `get` before it creates, adopts the ticket that was already there, and one incident keeps one ticket | `test_itsm_sync.py` |
| **Partial completion** | the declaration commits before the ITSM is touched, so an application with no ticket queue still gets an incident and the row says why there is no ticket | `test_itsm_sync.py` |
| **Duplicates** | a second declaration is refused naming the incident to join; a re-submitted severity is a no-op with no event; a re-announced deployment is one change window | `test_incident_idempotency.py` |
| **Model / adapter failure** | a person can declare while the detector cannot be read at all - the refusal names the reason and asks for a severity, and the incident records `proposal.unavailable` | `test_incident_lifecycle.py` |
| **Human override** | declaring against a steady stance, and choosing a severity the matrix did not propose, are both recorded as overrides with the proposal kept beside the decision | `test_lifecycle_floor.py`, `test_incident_lifecycle.py` |
| **Permission change mid-flow** | a commander re-bound to another application is refused on the next write and what was already declared is intact; a connector disabled mid-incident leaves the incident closable | `test_incident_boundary.py`, `test_itsm_sync.py` |

## 9. The war room: a decision becomes a response (REQ-045)

Declaring an incident answers *whether*. The war room answers *who* - and it answers it without
asking, because the person who declares a P0 at 03:00 should not also have to work out who to
wake.

### 9.1 The room opens because the incident was declared

There is no "open a war room" button. `IncidentService.declare` fires a hook after its commit and
the room assembles itself. That ordering carries the whole failure design of the chapter: the
room is opened *after* the declaration is committed and cannot roll it back, and every failure
inside it - a topology that will not parse, a notification connector that is down, a bridge that
does not exist - is caught, recorded and survived. A P0 with a room that failed to assemble is
bad; a P0 that could not be declared because the room failed to assemble is unforgivable.

One incident has one room, enforced by a unique constraint on `(tenant, incident)`. The lifecycle
already refuses a second open incident per application, so the two guards together mean two
commanders pressing Declare seconds apart produce one incident, one room and one set of pages.

### 9.2 Membership is derived in three separate steps

The steps are kept apart on purpose, because each is a different kind of claim and only one of
them needs a database:

1. **`BlastRadius`** - what is affected. Read from the incident's own declaration snapshot (the
   severity, the regions and journeys detection named, whether a customer is feeling it) and from
   the application's stored topology. Facts about the incident, with no notion of people.
2. **`required_roles`** - which roles that calls for. A pure function of the radius: a table,
   arguable, testable without a database, and the place to have the argument.
3. **Resolving roles to people** - role bindings scoped to this application, or tenant-wide.

The radius is a *snapshot*, copied onto the room rather than recomputed on read. Re-deriving it
would make membership drift as the topology is re-imported and the telemetry moves, and "why was
I paged" has to have a stable answer.

**Blast radius walks to dependents, not to dependencies.** Edge direction in the canonical
topology reads source-depends-on-target, so the walk goes backwards: from the broken node to the
things that named it. Walking forwards would answer "what does this application need", which is a
different and equally interesting question, and answering it here by accident is exactly the sort
of quiet wrongness a war room cannot afford. An application with no matching node produces an
empty radius rather than seeding from every node, because calling the whole estate a blast radius
reads as a catastrophe on every incident and therefore on none.

### 9.3 The role table

| Role | When | Paged |
|---|---|---|
| incident commander | always | yes |
| tech lead | always | yes |
| SME | anything downstream, any journey named, or P0/P1 | yes |
| comms lead | a customer is feeling it | yes |
| executive | P0, or P1 across the whole estate | **no - present, not woken** |

Being in the room and being paged are different things, and the table says which is which. An
executive is in the room for a P0 and is not woken by a pager for one; conflating the two is how
executives stop reading their notifications.

Two roles are deliberately never required. The **auditor** is read-only by the frozen matrix and
can change nothing during an incident, so paging one is noise - and they may join any room they
can read. The **org owner** is an administrative role; anybody who should be in the room
operationally holds an operational role, and the binding says so.

### 9.4 A role nobody holds is a stated gap

If a customer-facing P0 calls for a comms lead and nobody is bound to one, the room says so - on
its face, on the incident timeline as `warroom.membership.gap`, and in the audit chain. It is not
an error; a war room with no comms lead still runs. It is a fact somebody has to be able to see
while they can still do something about it, and the failure it prevents is the expensive one:
nobody notices the customer was never told until the post-incident review.

### 9.5 Paging, and what happens when nobody answers

A page is a question put to one person, and the design is about the case where they do not
answer.

- **Channels come from severity, not from a per-page choice.** A P0 rings a phone (voice, SMS,
  in-app, push, DM); a P3 does not. Waking somebody at 03:00 for a P3 is how a pager gets muted,
  and a muted pager is how the next P0 is missed.
- **The countdown is arithmetic over stored times.** A page holds `paged_at` and
  `ack_deadline_at`; how long is left is computed against the caller's clock. No timer is held
  anywhere. A restart loses nothing, a screen closed for ten minutes reopens on the right number,
  and a drill on a virtual clock at 100x escalates at 100x with no drill-aware code.
- **A page nobody could be reached through fails immediately** rather than counting down. A
  countdown there would be a lie: five minutes of a timer nobody is watching, followed by an
  escalation to a channel that also does not exist.
- **Acknowledgement is terminal, idempotent and self-scoped.** A double-tapped button on a train,
  a redelivered socket frame and a retried request are one act. Only the person the page names may
  answer it - not their commander, not the org owner - because "I have this" is a claim only the
  person who has it can make.
- **Escalation is bounded and never silent.** The next target is the next person in the rotation
  who has not already been paged for this chain, then the escalation policy's next step, then any
  other holder of the required role. Running out raises rather than stopping quietly, publishing
  `warroom.page.escalation.exhausted` with `needs_human: true` - because the moment nobody is
  left to page is the moment a human has to be told, and a quiet no-op is how that becomes
  nobody's problem.

**The sweep reconciles before it escalates.** An acknowledgement can arrive two ways: the war
room's own ack route, or the `/ws` socket the S3 notification inbox already serves. Both are a
real person really answering, so the sweep reads the delivery receipt back before escalating -
the same shape as the ITSM sync asking the queue what it holds before creating a second ticket.

**One page is one notification, however many times the code runs.** Notification ids are derived
from `(page, binding, channel)`, so a retried declare, a restarted process and two workers racing
all hand the same id to the notification adapter, which returns the first receipt without
transmitting again. A responder paged twice for one thing stops trusting the pager.

### 9.6 The join brief

What somebody walking in at minute forty needs: what is happening, the impact, what we know, what
has been done, recent changes, and what is being asked of *them*. Sections rather than a
paragraph, because the reader is on a phone with one hand.

Template now, model in S5 (REQ-051), and the ordering is deliberate rather than a shortcut. The
brief is the first thing a paged responder reads while still deciding whether to get out of bed,
so the version that exists when the model is down has to be genuinely useful. `source` is stamped
on every brief - `template`, `model` or `degraded` - so a reader can calibrate how much to trust
it, and the field exists before there is a model so no screen changes when there is one.

The section that matters most is `caveats`. A brief drawn from a declaration made while the
detector was down knows far less than one drawn from a corroborated proposal, and it says so:
"drawn from the declaration alone", "not corroborated - one signal family, not two - so the cause
named above is a hypothesis", "no topology is mapped". Confidence the evidence does not support is
the failure mode of every incident summary and the one a model will be most tempted to produce, so
the deterministic version sets the standard the model has to meet.

### 9.7 Bridge commitments become tasks

Bridges lose commitments. Somebody says "I'll roll back region-b in ten minutes", four other
things happen, and forty minutes later nobody has and everybody assumed somebody had. The harvest
turns those sentences into tasks with an owner and a due time.

It is deterministic and conservative, and both choices are load-bearing. Deterministic, because
S5's model will do it better and this has to keep working when the model is down. Conservative,
because a false positive is worse than a miss: a task nobody agreed to sits on the board with
somebody's name against it, and arguing about it during a P0 costs more than catching one more
commitment. So a question is not a commitment ("should we roll back?"), an option is not a
commitment ("we can roll back if this gets worse"), and a forecast about a service is not a
commitment by a person ("Payments will be down for a while").

**It never guesses an owner.** A commitment naming somebody the platform cannot resolve becomes an
*unassigned* task carrying the name as a label. Two users called Sam means no match, not a coin
toss. An unowned task on the board is fixable in one click; a task owned by the wrong Sam is how
the wrong person gets paged at 03:00.

Authorship is settled where it is certain. The frozen collaboration SPI's `post(channel, text,
thread_id)` carries no author - the adapter writes the transcript under the connector's identity,
which is correct, since that is who the far side sees posting. So a commitment said through
SignalForge is harvested at the moment it is posted, where the session *is* the author and nothing
has to be inferred. The background sweep reads the same transcript afterwards and creates nothing,
because commitment keys are derived from the message id: the two paths compose through one
idempotency guard rather than knowing about each other. What the sweep alone finds - scripted
lines, anybody posting from the far side - genuinely has no SignalForge author and stays
unassigned, which is the honest answer.

### 9.8 What carries a permission, and what carries something stronger

The frozen roles matrix gives the SME no permissions at all. The person most likely to be paged
holds nothing, so the acts that *are* that person cannot sit behind a permission without locking
the war room out of the people it exists to reach.

| Write | Guard |
|---|---|
| `POST /incidents/{id}/pages` | `declare_incident` |
| `POST /incidents/{id}/pages/{pid}/escalate` | `declare_incident` |
| `POST /incidents/{id}/tasks` | `declare_incident` |
| `PATCH /incidents/{id}/tasks/{tid}` | `declare_incident` |
| `POST /incidents/{id}/pages/{pid}/ack` | **self-scoped** |
| `POST /incidents/{id}/war-room/join` | **self-scoped** |
| `POST /incidents/{id}/bridge` | **self-scoped** |
| `POST /incidents/{id}/tasks/{tid}/done` | **self-scoped** |

Self-scoped means the actor is the session and can never be a parameter, and the entity refuses
the act when it names somebody else. That is *stronger* than a permission, not weaker:
`declare_incident` would let any holder acknowledge anybody's page, whereas the page refuses
everyone but its addressee. Putting work on somebody else's board stays command work; ticking your
own task does not. An unassigned task is completable by anybody in the tenant, because somebody has
to be able to close out work that was actually done, and refusing until a commander assigns it
first is how a board fills with things everybody knows are finished.

### 9.9 Where every war-room failure lands

| Failure | Where it lands | Held by |
|---|---|---|
| **Timeout** | a notification channel that times out is recorded `failed` on that channel's row; the other channels still carried the page and the countdown runs normally | `test_paging_idempotency.py` |
| **Uncertain external state** | a page answered over the `/ws` socket rather than the ack route is reconciled from the delivery receipt before the sweep escalates past it | `test_paging_idempotency.py` |
| **Partial completion** | a P0 whose install has no SMS connector goes out on the channels it has, is `sent`, and says which carried it; a room whose bridge is absent still pages and still tracks tasks | `test_paging_idempotency.py`, `test_bridge_commitments.py` |
| **Duplicates** | derived notification ids mean one page is one notification; derived commitment keys mean one sentence is one task across both harvest paths; a second declare is refused | `test_paging_idempotency.py`, `test_bridge_commitments.py` |
| **Model / adapter failure** | no notification connector at all is a `failed` page with the reason on the row, not a silent countdown; a topology that will not parse opens the room without downstream services and the brief says none is mapped | `test_paging_escalation.py` |
| **Human override** | acknowledgement is terminal against any machine, and a commander can escalate before the countdown ends - recorded as a human act with their id on it | `test_paging_escalation.py` |
| **Permission change mid-flow** | the ack path depends on no permission, so a re-bound responder can still answer the page they were sent; command writes are refused on the next call and what was already acknowledged stands | `test_warroom_boundary.py` |
| **Nobody answers at all** | the chain exhausts loudly - `warroom.page.escalation.exhausted` with `needs_human: true` on the timeline and in the audit chain - and the page keeps saying it went unanswered | `test_paging_escalation.py` |

## 10. The incident screens (REQ-040, REQ-042, REQ-043, REQ-044, REQ-045)

Six screens, and one question runs through all of them: is the reader looking at
something the platform *measured*, something it *proposed*, or something a person
*decided*? Those three need different responses, the same six words name the
first and third (`steady`/`watch`/`proposed` against `active`/`recovering`/
`closed`), and every screen below says which it is showing rather than leaving a
reader to infer it from a colour.

### 10.1 S5 - application detail

The header carries the state, the word for where it came from (`a detection
stance, recomputed every 5s` or `declared by a person`), and the change-window
chip. Below it: golden signal tiles, journeys, the health panel and recent
changes.

**Nothing on this screen is computed in the browser.** The health score, the
corroboration verdict and the severity proposal are all read from the API. A
second implementation of the weighting in TypeScript would eventually disagree
with the one in the domain, and it would disagree during an incident.

Three renderings are deliberate:

- **Golden tiles show the worst region, not an average.** An average of "one
  region is down and one is fine" is "half broken", which describes nothing that
  is happening and hides the region to look at.
- **`unknown` is never drawn as healthy.** A signal that could not be measured
  gets its own muted state everywhere. "We could not check" and "this is fine"
  lead to opposite actions, and a grey tile that looks green is how a blind spot
  survives a review. The journey list keeps `not checked` separate from `failed`
  for the same reason.
- **The health panel's Grafana tab always states its fallback.** A browser gives
  an iframe no usable error, so a blank frame could be Grafana being down, not
  configured, or refusing to be embedded - and the screen cannot tell those
  apart. It says so in advance, beside the frame, and the SignalForge view is the
  default because it is computed from data this platform holds and cannot fail
  independently.

The chip is lit by the API's own `chip` field, not by a count the browser
derives. A window open past the platform's ceiling is `stale`: the chip goes out,
because a chip lit for three days is a chip people stop seeing, but the row stays
listed as "started 3.1h ago, never announced completion". It is not closed on a
guess - nobody knows whether that deploy landed.

### 10.2 S6 - the Watch banner

Quiet is the requirement, not a stylistic preference. `watch` means something is
being observed and nothing has been decided: no corroboration, no severity, no
incident. A banner that shouted here would train people to ignore the one that
shouts when it matters, so this is a low-contrast strip with no alarm colour and
no button that could be mistaken for a decision.

What it does do is **name what is being checked**. "The AI is looking into it"
with no list is the thing operators most reasonably distrust; listing the signals
under observation, with how many evaluations each has been breaching, is what
makes the quiet version credible.

### 10.3 S7 - the proposal sheet

The corroboration checklist, then Declare.

"The AI thinks this is a P0" is not something an incident commander can act on at
03:00. What they can act on is: two independent families are saying it, one is
customer-facing, these regions are affected, this is the journey impact, here are
the evidence refs. The checklist renders the platform's own verdict item by item
- **including the items that did not pass**, because a proposal that is not
corroborated is precisely the one to look at carefully, and hiding the failed
rows would make it look like the strong one.

Declaring at the proposed severity and declaring at a different one are two
separate buttons rather than a dropdown and a submit. Both are human acts; the
API records `proposed` or `chosen`, and the sheet says that choosing differently
is kept as an override with what the matrix proposed beside it.

The sheet does not render at all once an incident exists. A second Declare button
beside a live incident is how a duplicate gets pressed.

### 10.4 S8 - the acknowledgement board

One row per person asked, each with its own timer and, next to it, **why they
were asked**. The reason is not decoration: somebody woken at 03:00 deserves to
know why it was them, and a board of names and timers makes every page look
interchangeable.

Three things the board deliberately does not do:

- **It does not compute its own countdown.** The number is the server's, ticked
  locally between polls and re-anchored on every one. A responder's laptop clock
  can be minutes off; if the browser computed `deadline - Date.now()`, that
  screen would show two minutes remaining while the escalation sweep was already
  waking a colleague, and the reasonable conclusion would be that the pager is
  broken. This is pinned by test.
- **It does not offer Acknowledge on somebody else's row.** The API refuses it -
  a page is answered by the person it names - and a button guaranteed to 403
  teaches people the product is unreliable.
- **It does not draw a `failed` page as counting down.** A page no channel could
  carry reached nobody, and it says so with the reason instead of a timer nobody
  is watching.

### 10.5 S9 - the war-room shell

Ten panels with fixed titles - Incident Overview, Live Bridge Transcript, Team
Chat, AI Incident Brain, Next Best Action, AI Copilot, Incident Timeline, Warning
and Guidance, Health Score and Metrics, Affected Services - plus **Who is in the
room**. The titles are a vocabulary: somebody who learns where "Next Best Action"
is should find it under that name in every room, every drill and every screenshot
in the runbook, so they are pinned verbatim by test and renaming one is a
decision made in that file.

Four panels describe agent features Sprint 5 builds. They render **now**, with
their real titles and one sentence saying what will fill them. Two reasons: the
layout an incident commander learns must not move under them when the agents
land, and a panel that quietly does not exist is indistinguishable from one that
is broken.

*Who is in the room* shows presence, not invitations: `here`, `paged, not
answered`, `in the radius`. That difference is the entire reason a commander
looks at the panel, and one status field could not express it.

The Health Score panel in the room shows the score **as declared**, and says so.
It is the snapshot copied onto the incident, not the live number - an incident
that re-read live telemetry would explain itself differently every time somebody
opened it.

### 10.6 S22 - the mobile paging page

One screen, one decision: acknowledge, or read the brief. Everything else can
wait for a laptop.

Big targets and no confirmation dialog, because acknowledging is idempotent and
terminal on the server - a mis-tap costs nothing, and a confirmation step costs
seconds when seconds are the point. The countdown is the server's number here
too. The brief's caveats are carried onto the phone rather than trimmed for
space: a responder deciding whether to get up needs to know how much is actually
known.

It reads `/my/pages`, which has **no recipient parameter at all**. There is no way
for this screen to show anybody else's pages, which is what lets the route carry
no permission - and the person holding the phone is usually an SME, who by the
frozen roles matrix holds none.

### 10.7 How the cockpit stays current

**The socket, and the credential that made it possible.** The screens were
polled through the typed cookie proxy for one honest reason: the session token
lives in an httpOnly cookie that browser JavaScript cannot read, so a
cross-origin WebSocket handshake had nothing to authenticate with, and handing
JavaScript the token to fix that would have traded a permanent control for a
refresh interval.

The other way out is a credential of its own, and that is what the cockpit uses.
An authenticated session asks its own origin for a **socket ticket** (`POST
/auth/ws-ticket`, through the same cookie proxy every other call uses) and spends
it on the handshake as the `ticket, <value>` subprotocol - a header, never a
query parameter, so it never reaches an access log. The ticket lives thirty
seconds, is single-use, is bound to the tenant, the user and the session it came
from, and is *typed*, so the access-token verifier refuses it and it can reach no
HTTP route at all. A ticket that leaks is worth one handshake for half a minute.
The httpOnly cookie is untouched.

**What arrives is a nudge, not data.** A change in a war room becomes
`{"type": "room.changed", incident_id, topic, seq}` - the incident, what moved,
and the sequence number, and nothing else. The screen reacts by re-reading
through the authorised HTTP route it was already using. That is deliberate: a
socket carrying room contents would be a second read path with its own
authorisation to get wrong, in a product whose whole security story is that every
read is tenant-scoped at the repository. Because a nudge discloses nothing,
watching an incident needs no permission - a socket watching a room its session
may not read simply gets a 403 when it re-reads.

**The poll never stops; it slows down.** Thirty seconds while the socket is up,
five and ten when it is not. Three things can silently cost a screen a frame -
the ticket endpoint being unreachable, the socket dropping, or the hub skipping a
frame on purpose when one browser falls behind - and in every one of them the
screen must degrade to *late* rather than to *wrong*. It says which mode it is
in, because a responder who believes a screen is live when it is not is exactly
how somebody concludes that nobody is answering their page.

Nothing on these screens is computed in the browser either way: the countdowns
are the server's own numbers, so a stale screen lags reality and never disagrees
with it.

---

## 11. The Incident Brain: a response becomes an argument (REQ-050, REQ-052, REQ-053, REQ-054)

Sprint 4 ended with a room, a blast radius and a task board. What it could not do
is say *why*. This section is that: a versioned record of what the platform
believes about an incident, what it believes it from, and how confident it is -
built so that an incident commander can disagree with it.

Full architecture in `docs/08-agent-architecture.md`; what is asserted about it in
`docs/15-ai-eval.md`.

### 11.1 What the brain is

A graph of nine node kinds - fact, assumption, hypothesis, evidence, impact, risk,
action, approval, communication - and **nothing in it is ever overwritten**.

Every node carries a `derived_key` (a stable identity for the thing it is about)
and a `version`. When a belief changes, a new version is written naming the one it
supersedes, and the old row stays readable with what it said and when it stopped
being current. A belief withdrawn without a replacement is `retracted`, kept, and
excluded from the current view - a ruled-out hypothesis is often the most
interesting row at a post-incident review.

Two consequences that matter more than the storage detail:

- **"Why did hypothesis #2 move from 65% to 32%?" is answered by reading rows**,
  six months later, without re-deriving anything.
- **An agent running every twenty seconds against an unchanged world writes
  nothing.** A version is a change of belief, not a heartbeat.

Every node also carries where it came from: the source, the actor type, the
authority - authoritative, observed or inferred - and, only where a model was
involved, the model, its classification and whether it degraded. **Anything an
agent concluded is `inferred`, without exception.** The brain correlates things
other subsystems measured; recording its output as observed would put a
correlation into the audit chain wearing the authority of a measurement.

### 11.2 Confirmed and predicted are different words

A service is **confirmed** as affected when something was measured on it - an
anomaly, a failing probe, an alert naming it. A service is **predicted** when it
depends on something confirmed and *nothing has been measured on it at all*.

The two are computed separately, stored separately, returned as separate lists,
and rendered under two legends with two colours. The reason is not tidiness. A
predicted downstream outage shown like an observed one is how a war room pages
four teams for an outage that never happened - and once that has happened twice,
people stop believing the blast radius at all.

Propagation is bounded at three hops with a likelihood decaying per hop, because
a real dependency graph is connected enough that an unbounded walk reaches
everything, and "everything is predicted to be affected" is the same sentence as
"we have no idea".

Topology versions are kept forever, so an incident can be read against the map
that was in force when it started, and "has the shape of this estate changed since
this began?" is a question with an answer.

### 11.3 Where a confidence comes from

**Arithmetic, never a model.**

Each piece of evidence linked to a hypothesis has a kind with a published base
weight, scaled by the source's authority and by a strength the measurement itself
supplies. Weights sum - supporting positive, refuting negative - and pass through
a logistic anchored at a prior of 0.35.

Three properties are deliberate:

- **A hypothesis with no evidence sits at the prior, not at zero.** Zero reads as
  "ruled out"; an unevidenced hypothesis has not been ruled out, it has merely not
  been tested, and the screen says so.
- **Inferred evidence counts a quarter** of authoritative evidence. That is what
  stops a chain of model inferences manufacturing certainty out of nothing
  measured.
- **Historical similarity is capped in total.** However many prior incidents
  agree, history alone cannot carry a hypothesis past even odds - and cannot rule
  one out either. One real measurement outweighs the entire advisory budget.

Every recomputation writes a history entry naming the previous value, the new one,
the net weight, the evidence on each side, and which evidence is new. The
explanation is written from those numbers, so it says the same thing with or
without a model. A model may write one sentence beside it; its schema has no
number field for a confidence to arrive in.

**Three competing hypotheses are always offered** - a change-induced one, a
saturation or dependency one, and an infrastructure or regional one - plus the
proximate change by name and any recurrence of a prior incident's recorded cause.
A room that sees one hypothesis tests one hypothesis.

Each card also carries the **discriminating test**: what would most move this
next. A percentage without a next step is a number to argue about rather than act
on.

### 11.4 What the bridge is understood to be saying

Every message is classified deterministically first - fact, assumption, question,
decision, commitment, blocker, chatter - by rules that run with no network, no
key and no budget. The model then sees *only* the lines the rules could not place,
and may promote from those and from nothing else. When the rules say "commitment"
they have matched an explicit promise, and a model talking them out of it would
lose a task somebody made at 03:00; when they say "chatter" they have merely
failed to match a pattern, and that is exactly where *"Sam, can you check the
pool?"* lives.

**Contradictions** are found by comparing a human health claim about a named
subject against what the platform measured about that subject. "The database is
fine" while its connectivity probe is failing is either a stale belief driving the
wrong work or a measurement pointed at the wrong thing, and both are worth twenty
minutes of an incident. It becomes a risk node, a timeline event, and a row on the
guidance panel.

Sprint 4's commitment harvester still owns the task board. This layer creates no
tasks; two writers with two key schemes producing two rows for one sentence is a
failure avoided by not having two writers.

### 11.5 Learning from before, without repeating it

Prior incidents in the same tenant are reduced to structured fingerprints -
signal families, services, regions, change types, application, severity - and
compared. An embedding cosine from the same gateway blends in as a minority term
and, when it degrades, the structural score stands alone at full weight: a
degraded model path must not look like a lack of similarity.

Relevance decays with a four-month half life and the *decayed* number is the one
shown, because an operator comparing "82% similar" between a two-year-old incident
and last week's is comparing the wrong numbers.

The most useful output is not "this was the cause". It is **"we spent forty
minutes on this last time and it was not it"** - a warning drawn from what a prior
incident *ruled out*, pinned where somebody will read it. Every row carries the
sentence that says what it is: guidance from a prior incident, never a finding
about this one.

### 11.6 Evidence is gathered broad, then aggregate, then narrow

The naive way to answer "why is this happening" is to put the transcript, the
telemetry and the change log into a prompt. It works once, on a small incident,
and then it costs in proportion to how bad the incident is - the six-hour P0 being
exactly the one that blows the budget and exactly the one a truncated prompt
damages most.

So: a **broad** pass of cheap structured reads producing counts; an **aggregate**
pass collapsing them into rows a person would write (the worst signal per family,
alert clusters rather than alerts); and a **narrow** pass, per hypothesis, under
its own query budget, which is the only one carrying excerpts - each capped, each
masked by the platform's one redaction function, each carrying a reference so the
whole thing is one click away rather than in the prompt.

A refused query is recorded rather than skipped. "Nothing was found" and "we
stopped looking" are different findings, and a bundle that could not tell them
apart would let a budget quietly become a conclusion.

### 11.7 The copilot answers, or says it cannot

Ask a question about an incident; get an answer drawn only from that incident's
graph, with a chip per citation.

Every citation must resolve to a node the retrieval actually returned. An answer
citing anything else - or asserting a fact with no citation at all - is
**discarded**, not annotated, and the insufficient-evidence answer is served
instead. An ungrounded answer displayed with a warning is displayed.

The refusal is a real answer: it lists what the incident *does* hold near the
question, because "no" alone teaches an operator to stop asking.

Four states, four badges: grounded, insufficient evidence, answer discarded, and
degraded.

### 11.8 Untrusted text stays untrusted

Bridge messages, chat, logs, alert annotations, ticket bodies, retrieved
documents, tool results - all of it is text this platform did not write, and some
of it may be hostile.

It is fenced inside a per-call, unguessable delimiter, in a user message,
underneath a standing instruction that says plainly that content inside is
evidence and never an instruction. Attempts to instruct the assistant are
**flagged and kept verbatim** - stripping the sentence would destroy the evidence
and teach an attacker what the filter looks for - and surface as a risk node, a
timeline event and a row on the guidance panel.

The control that actually holds is simpler than any of that: **no agent holds a
tool.** No invoker, no gateway, no write path. A model fully persuaded by an
injected instruction has nothing to do about it.

### 11.9 What happens when there is no model

Everything below still works, which is the point of building it this way:

| | With a model | Without |
|---|---|---|
| Statement classification | rules + promotion | rules |
| Contradictions, telemetry, topology, change | deterministic | unchanged |
| Historical similarity | structural + embeddings | structural at full weight |
| Confidence and its history | deterministic | unchanged |
| "Why it moved" | explanation + narrative | explanation |
| Join brief | model rewrite, caveats welded on | template, badged degraded |
| Copilot | grounded answer with citations | the record, listed, with citations |

The badge is read from what the last pass actually did, not from whether a key is
configured. A revoked key still looks configured; only the trace knows.

### 11.10 The screens

**S10 - AI Incident Brain.** Three hypothesis cards, each with the computed
confidence, a sparkline of every recomputation, the deterministic explanation of
the last delta, evidence for and against as two separate lists, and the
discriminating test. Beneath them the ledger, tabbed by node type so a fact and an
assumption are never read in one column, every row carrying its authority chip and
- where a belief has been revised - a button that opens every version.

The sparkline is fixed to the full range rather than scaled to the data: an
auto-scaled one would make a wobble between 61% and 63% look identical to a
collapse from 65% to 32%.

Everyone in the tenant reads this panel; only incident command runs a pass.
"Re-reason now" writes the platform's beliefs about a live incident, so it
carries `declare_incident` (DR-101) and the button is hidden from anyone who does
not hold it, with the panel saying who does. A control that exists and always
answers 403 teaches a room that the product is broken; the pass still happens for
everyone, on the next event that wakes the brain.

**S13 - Affected Services.** A layered dependency graph laid out by hops from a
confirmed fault, two impact lists under two legends, a version switcher over the
kept maps, and a changed-since line.

**S14 - Warning and Guidance.** Contradictions, pinned mistake warnings, similar
prior incidents with their relevance movement, and any content that tried to
instruct the assistant. The caveat comes from the server rather than the
component, because a client that *could* render the rows without it eventually
would.

**S18 - IC Copilot.** A question box, the verdict badge before the prose, and a
citation chip per node with its provenance in the tooltip.

All four share the cockpit's existing socket. There is one WebSocket store in this
application and it stays that way; the server's cockpit consumer subscribes to
`brain.*` alongside `incident.*` and `warroom.*`, so a hypothesis that moved
arrives on the frame the platform was already sending.
