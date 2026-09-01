# 04 - Functional specification

Owner: engineering. Written per feature area as features land (artifact 04, due S3 onward).
Each section describes behaviour that exists in the running product, with the REQ id it
satisfies and the tests that hold it in place. Sections for onboarding, detection, the war
room, the incident brain, governance and drills arrive with their sprints.

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
