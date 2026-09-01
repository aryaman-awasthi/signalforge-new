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
