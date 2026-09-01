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

## `GET /ws` - the cockpit notification socket (REQ-033)

A WebSocket, authenticated and self-scoped, and the only product route with no
permission check. It cannot have one: the people who most need to be paged - SMEs -
hold no permissions in the roles matrix. The compensating control is that the socket is
incapable of naming anyone else. The principal comes from the session, the subscription is
keyed on that principal's tenant and user id, and catch-up reads that recipient's own
notifications; there is no parameter through which a caller could ask for somebody else's.

Authentication is a bearer token in the `Authorization` header, or - because a browser cannot
set a header on a WebSocket handshake - as the `bearer, <token>` subprotocol pair. A
`?token=` query parameter is deliberately not accepted: it would put a live session token into
every access log and proxy history on the path. A missing or invalid token closes the
handshake with 1008.

Client frames:

```json
{"type": "hello", "channels": ["in_app", "browser_push"]}
{"type": "ack",   "notification_id": "n-1"}
{"type": "ping"}
```

Server frames: `ready` on connect, then one `notification` per still-open item (with
`"replay": true`), then live `notification` frames, plus `subscribed`, `ack.ok`, `pong` and
`error`. A notification frame is:

```json
{"type": "notification", "replay": false, "notification_id": "n-1",
 "adapter_id": "notify-in-app", "channel": "in_app", "title": "...", "body": "...",
 "incident_id": null, "requires_ack": true, "simulated": false,
 "status": "sent", "at": "2026-09-01T12:00:00+00:00"}
```

Acknowledgement is the human override: idempotent, terminal, and written with its audit entry
in one transaction, so an escalation timer and the audit chain cannot disagree about whether
somebody answered. Acknowledging another tenant's id answers `notification not found`, the
same as one that never existed.

## Adapter invocation (REQ-030, REQ-031)

There is no HTTP endpoint that invokes an adapter capability. Every call goes through
`ConnectorInvoker` in the application layer, which is what the onboarding wizard's connection
test (S3-T04), evidence gathering (S5) and the Tool Gateway (S6) each use; exposing a generic
"call this capability" route would put the policy floor behind a query parameter. Five checks
happen on every call: the binding exists in this tenant and is enabled, its adapter is still
installed with a runtime on this node, the credential-scope floor allows that capability on
that credential, the credential is decrypted for this call only, and a watchdog bounds the
whole capability while the manifest's `timeout_s` bounds each HTTP call inside it.

## Unauthenticated endpoints (S1/S2, unchanged)

`GET /health` reports this node's own components only - a customer's connector never appears
and never changes the status code. `GET /metrics` renders from a registry that rejects
identifying label names at declaration time; connector gauges are keyed by adapter id and
`signalforge_connector_bindings{state}` counts bindings without naming one.
