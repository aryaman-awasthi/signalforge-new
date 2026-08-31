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
