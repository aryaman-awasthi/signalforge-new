# Adapter folders

An adapter is a directory in this tree containing a `manifest.yaml`. The registry
(`registry.py`) discovers `**/manifest.yaml` under this root at startup, so
installing an adapter is copying a folder and restarting; nothing in the core is
edited, and no adapter is named anywhere in the platform's code.

```
signalforge/infrastructure/adapters/
  <adapter-id>/
    manifest.yaml      what the adapter declares about itself (this document)
    adapter.py         the runtime, registered into AdapterRuntimeRegistry
```

The schema is hardcoded by SignalForge (`domain/adapters/manifest.py`); the
content is declared by the adapter. A manifest that breaks a rule below is
rejected at load time, recorded with its path, and shown on `GET /adapters`
under `errors` - the other adapters still load.

## Fields

| Field | Required | Meaning |
|---|---|---|
| `id` | yes | lowercase kebab-case, 3-64 chars. Unique across the tree. |
| `name` | yes | human label shown in the registry and the wizard. |
| `version` | yes | `MAJOR.MINOR.PATCH`. |
| `kind` | yes | one of the 13 SPI kinds (`metrics`, `logs`, `traces`, `health`, `alerts`, `changes`, `topology`, `collaboration`, `itsm`, `actions`, `notifications`, `knowledge`, `chaos`). |
| `mode` | yes | `pull` (SignalForge polls), `push` (the app posts to ingest), `invoke` (SignalForge asks for a change). |
| `capabilities` | yes | SPI method names of that kind that this adapter really implements. `ping` is implicit and always required. |
| `credential_scopes` | yes | which of `read_only`, `remediation`, `drill` a binding may claim. |
| `auth` | no | `method` (`none`, `bearer`, `basic`, `api_key`, `hmac`), `credential_fields` (secret names), `header`. |
| `config_schema` | no | JSON Schema (2020-12) for a binding's config. The wizard renders fields from it; a binding is validated against it. |
| `limits` | no | `timeout_s`, `max_calls_per_minute`, `max_result_bytes`, `max_rows`. Bounded by the domain; a manifest may ask for less, never more. |
| `redaction` | no* | `policy_id`, `fields`, `patterns`. Required for kinds that carry free text (see rule 5). |
| `availability` | no | `built_in` (a runtime ships with it) or `available` (interface known, no runtime here). Defaults to `built_in`. |
| `simulated` | no | true when the adapter fakes the integrated system. Surfaces as a SIMULATED badge everywhere the data is used. |
| `vendor`, `description` | no | free text for the registry screen. |

## Rules the loader enforces

1. **Capabilities are SPI methods.** They are read off `contracts/spi.py`, so a
   manifest cannot promise something with no name in the contract.
2. **Mode and capabilities agree.** `invoke` must declare at least one mutating
   capability; `pull` and `push` must declare none. `push` must declare
   `normalize`; `pull` must declare a fetching capability.
3. **Credential scopes respect the floor.** An adapter with mutating
   capabilities may not offer `read_only`. A `chaos` adapter offers exactly
   `[drill]`.
4. **Limits are bounded** by `domain/adapters/manifest.py`.
5. **Redaction is explicit** for `logs`, `traces`, `collaboration`, `knowledge`
   and `itsm`: those carry free text out of the customer's estate into evidence
   and prompts, so the manifest must state a `redaction.policy_id` even when the
   policy redacts nothing.

## Example

```yaml
id: example-metrics
name: Example metrics over the observe contract
version: 1.0.0
kind: metrics
mode: pull
vendor: generic
description: Reads Prometheus text exposition from an app's /observe/metrics.
availability: built_in
simulated: false
capabilities: [query_instant, query_range]
credential_scopes: [read_only]
auth:
  method: bearer
  credential_fields: [token]
  header: Authorization
config_schema:
  type: object
  required: [base_url]
  additionalProperties: false
  properties:
    base_url:
      type: string
      minLength: 1
limits:
  timeout_s: 10
  max_calls_per_minute: 120
  max_result_bytes: 1048576
  max_rows: 5000
redaction:
  policy_id: metrics-none
```
