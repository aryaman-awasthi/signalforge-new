# 11 - API specification

Owner: engineering. Sections are written as endpoints land; the exported OpenAPI document and
the complete route catalogue arrive with S3-T07. Every route except `/health`, `/metrics` and
the four `/auth` bootstrap endpoints carries the RBAC dependency (REQ-012), and the permission
each one needs is named below.

Errors are mapped once, in `signalforge/api/errors.py`: 401 authentication, 403 permission
denied (with `permission` and `reason`), 404 not found, 409 conflict, 422 domain validation,
429 rate limited with `Retry-After`.

## Adapter registry and connectors (REQ-030)

All of these require `manage_apps`. `app_id` may be given as a query parameter so a per-app
`manage_apps` binding applies.

### `GET /adapters`

Query: `kind`, `mode`, `scope`, `simulated` - all optional.

```json
{
  "adapters": [
    {
      "id": "http-observe-metrics",
      "name": "HTTP Observe - Metrics",
      "version": "1.0.0",
      "kind": "metrics",
      "mode": "pull",
      "direction": "inbound (SignalForge polls)",
      "vendor": "generic",
      "description": "...",
      "availability": "built_in",
      "status": "installed",
      "runnable": true,
      "simulated": false,
      "capabilities": ["query_instant", "query_range"],
      "unsupported": [],
      "mutating_capabilities": [],
      "credential_scopes": ["read_only"],
      "auth": {"method": "bearer", "credential_fields": ["token"], "header": "Authorization"},
      "limits": {"timeout_s": 10.0, "max_calls_per_minute": 120,
                 "max_result_bytes": 1048576, "max_rows": 5000},
      "redaction": {"policy_id": null, "fields": [], "patterns": []},
      "config_schema": {"type": "object", "required": ["base_url"], "properties": {}}
    }
  ],
  "errors": [{"path": "broken/manifest.yaml", "error": "ManifestValidationError: ..."}]
}
```

`status` is `installed` (a runtime is registered on this node), `available` (interface only) or
`runtime missing` (the manifest claims `built_in` but no runtime is registered - a packaging
fault). `errors` carries manifests that failed to load; the rest of the registry still works.

### `GET /adapters/capability-matrix`

```json
{
  "rows": [
    {"adapter_id": "...", "name": "...", "kind": "health", "mode": "pull",
     "availability": "built_in", "simulated": false, "credential_scopes": ["read_only"],
     "cells": [{"capability": "get_health", "supported": true, "mutating": false},
               {"capability": "run_probe", "supported": false, "mutating": false}]}
  ],
  "coverage": [
    {"kind": "metrics", "capabilities": ["query_instant", "query_range"],
     "adapters": ["..."], "covered": ["query_instant", "query_range"],
     "uncovered": [], "complete": true}
  ]
}
```

All thirteen kinds appear in `coverage`, covered or not, so a gap is visible rather than absent.

### `GET /adapters/{adapter_id}`

One `adapters[]` element. 404 when nothing is installed under that id.

### `GET /connectors`

Query: `app_id`, `adapter_id`, `enabled_only`. Returns the tenant's bindings.

```json
[{
  "id": "b1b0...", "app_id": "checkout", "adapter_id": "http-observe-metrics",
  "adapter_name": "HTTP Observe - Metrics", "name": "checkout metrics",
  "kind": "metrics", "mode": "pull", "direction": "inbound (SignalForge polls)",
  "credential_scope": "read_only",
  "config": {"base_url": "http://app.internal:8080"},
  "credential_fields": ["token"],
  "enabled": true, "simulated": false, "installed": true,
  "status": "up", "status_detail": "reachable",
  "last_checked_at": "2026-09-01T12:00:00Z", "last_ok_at": "2026-09-01T12:00:00Z",
  "last_error": null, "consecutive_failures": 0,
  "created_at": "2026-09-01T11:00:00Z", "updated_at": "2026-09-01T11:00:00Z"
}]
```

`config` is returned with the manifest's declared redaction fields masked. Credential *values*
are never returned by any endpoint. `installed: false` marks an orphaned binding whose adapter
folder was removed.

### `POST /connectors` -> 201

```json
{"app_id": "checkout", "adapter_id": "http-observe-metrics", "name": "checkout metrics",
 "credential_scope": "read_only",
 "config": {"base_url": "http://app.internal:8080"},
 "credentials": {"token": "..."}, "enabled": true}
```

- 404 when the adapter is not installed.
- 409 when the app already binds that adapter at that credential scope (the response names the
  existing binding id).
- 422 when the config fails the manifest's `config_schema`, when the credential fields do not
  match those the manifest declares, or when the manifest does not offer that credential scope.

Credentials are stored Fernet-encrypted; the binding, its credentials, its audit entry and its
`system.connector.bound` event are one transaction.

### `PATCH /connectors/{id}`

Body: any of `config`, `credentials` (rotation), `enabled`. Audits which of the three changed.

### `DELETE /connectors/{id}` -> 204

Deletes the binding and exactly its own secrets, and audits `system.connector.unbound`.

### `GET /connectors/health`

The integration health screen. Every `GET /connectors` field plus `freshness_s` - seconds since
the last successful check, `null` when there has never been one.

## Events emitted

| Topic | Plane | Tenant | When |
|---|---|---|---|
| `system.connector.bound` | signal | owning tenant | a binding is created |
| `system.connector.updated` | signal | owning tenant | config, credentials or enabled changed |
| `system.connector.unbound` | signal | owning tenant | a binding is deleted |
| `system.connector.health` | signal | owning tenant for a binding, `_platform` for the platform's own components | every poll |

## Unauthenticated endpoints (S1/S2, unchanged)

`GET /health` reports this node's own components only - a customer's connector never appears
and never changes the status code. `GET /metrics` renders from a registry that rejects
identifying label names at declaration time; connector gauges are keyed by adapter id and
`signalforge_connector_bindings{state}` counts bindings without naming one.
