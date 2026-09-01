# 11 - API specification

Owner: engineering. This is the prose half of the API contract. The machine-readable half is
[`docs/11-openapi.json`](11-openapi.json), exported from the running application by
`scripts/gen_openapi.py` (`make openapi`) and byte-identical to what `GET /openapi.json`
serves. Prose carries what a schema cannot: why a route refuses something, which of two
directions a number describes, and what a status code means for the caller's *next* action.
Where the two disagree the export is right and this file is stale, and a test says so before
anyone reads it.

How to read it: **Conventions** first (they apply to every route and are not repeated),
then the generated **route catalogue** - every route, its credential and its permission -
then a section per area with the bodies and the refusals that matter, then the events the API
emits, the notification socket, and the observe contract an onboarded application serves.

## Conventions

### Credentials

Three kinds, and every operation in the exported document states which it wants in
`x-signalforge-auth`:

| Credential | Who holds it | How it is presented | Routes |
|---|---|---|---|
| **session** | a person | `Authorization: Bearer <access token>` from `/auth/signup`, `/auth/login` or `/auth/refresh`; 15 minutes, rotated by the refresh token | everything except the rows below |
| **ingest key** | a sending application | `Authorization: Bearer sfi_…` **plus** an HMAC-SHA256 over `"<timestamp>.<raw body>"` in `X-SignalForge-Signature`, timestamped within 300s | the five `/api/v1/ingest/*` paths |
| **none** | anyone | - | `/health`, `/metrics` and the four `/auth` bootstrap routes: signup, login, refresh, accept-invite |

That third row is a closed list. A test in the security suite reads the published document and
fails if any other operation is reachable without a credential, so "we accidentally shipped an
unauthenticated route" is a failing build rather than a finding.

A session is not a permission. `/auth/me`, `/roles/matrix`, `/orgs/current`, `/users`,
`/oncall/rotations` (read) and `/escalation/policies` (read) need a valid session and nothing
more - every member of a tenant may see who is on call and which policies exist. Everything
that writes, and everything that reads another party's material, names a permission from the
frozen roles matrix.

### Per-application scope

A permission can be granted org-wide or bound to one application. Which application a call is
about therefore has to be readable before the handler runs, and it comes from one of three
places: the `?app_id=` query parameter (`deps.require`), the `{app_id}` path segment
(`deps.require_app`), or - for the incident-scoped routes, where the application is not in the
URL at all - the incident named by `{incident_id}` (`deps.require_incident`, S4-T02). The
catalogue names which, per route, and the exported document repeats it in
`x-signalforge-app-scope` as `query`, `path` or `incident`. All three resolve to the same
decision; the first split exists because FastAPI resolves a dependency parameter declared as a
query parameter as a query parameter even when the route has a path parameter of that name
(DR-038), and the third because an incident is what names its own application.

### Errors

Mapped once, in `signalforge/api/errors.py`, so a new route cannot invent its own vocabulary:

| Code | Meaning | Body |
|---|---|---|
| 401 | no credential, or one that no longer authenticates | `{"detail": "authentication failed"}` - deliberately uninformative on the ingest paths |
| 403 | authenticated, not allowed | `{"detail": "permission denied", "permission": ..., "reason": ...}`, and an `audit.authz.denied` entry |
| 404 | not in this tenant - identical whether it belongs to somebody else or to nobody | `{"detail": ...}` |
| 409 | a duplicate, or a removal something live still depends on | names the conflicting object |
| 413 | body over the configured cap (2 MiB default), refused before it is read | |
| 422 | domain validation **and** every policy-floor refusal, including for an org owner | the domain's own sentence, not a schema dump |
| 429 | rate limited, with `Retry-After` | login and ingest have separate limits |
| 502 | the integrated system SignalForge was asked to call failed | `{"detail": ..., "error": "AdapterUnavailableError"}` |
| 504 | the integrated system did not answer in time - the outcome is **unknown**, not failed | |

502 and 504 are the pair worth internalising: they are how "the thing SignalForge called is
broken" is told apart from "SignalForge is broken", and 504 never means the call did not happen.

### Tenancy, idempotency and time

Every read and write carries the tenant predicate inside the repository, not in the handler, so
a cross-tenant id is a 404 rather than a leak. Producer-assigned ids are the idempotency unit
where one exists: `event_id` on ingest (unique per tenant), the topology document's content
fingerprint on upload, and the caller's `idempotency_key` on an action execution. Repeating one
is acknowledged, never re-applied. All timestamps are RFC 3339 with an offset; `occurred_at` can
follow a drill clock while `recorded_at` is always wall time.

## The exported document

`docs/11-openapi.json` is OpenAPI 3.1, generated by `signalforge/api/openapi.py` from the live
route table. Beyond the stock FastAPI export it carries, per operation:

| Field | Value |
|---|---|
| `security` | `sessionBearer`, or the three ingest schemes together (`ingestKey` + `ingestSignature` + `ingestTimestamp`, all required), or `[]` for the six routes that need nothing |
| `x-signalforge-auth` | `session`, `ingest-key` or `none` |
| `x-signalforge-permission` | the permission the route's RBAC dependency closes over; absent when a session alone is enough |
| `x-signalforge-app-scope` | `path` or `query` - where the application id for a per-app role binding is read from |
| `x-signalforge-ingest-stream` | which stream an ingest key must be scoped to; a correctly signed request from a key bound to another stream is a 403 |
| `x-signalforge-optional-headers` | `X-SignalForge-Key-Id` on the ingest paths |

Those fields are **read out of the route's own guard**, not from a list kept beside it.
That is the whole point: a route guarded by the wrong permission is published as guarded by the
wrong permission, where a reviewer and a test can both see it, and an unguarded route is
published as `none` - which is a closed list the security suite pins.

Regenerate with `make openapi` (or `python scripts/gen_openapi.py`) whenever a route, a schema
or a guard changes; `python scripts/gen_openapi.py --check` fails without writing, for a hook.
The suite fails if the committed export is not what the application would serve today.

The one route the export cannot describe is `GET /ws`: OpenAPI has no WebSocket. It is in the
catalogue below and its frame protocol is written out later in this document.

## Route catalogue

<!-- BEGIN GENERATED ROUTE CATALOGUE (scripts/gen_openapi.py) -->

98 routes: 87 need a human session, 5 a sending application's ingest key, and 6 no credential at all - and that last number is the one the security suite pins to a closed list.

The permission column is read from each route's own RBAC dependency, so a route guarded by the wrong permission is documented as guarded by the wrong permission. It also says where a per-application role binding is evaluated from: `?app_id=`, the optional query parameter every route guarded by `deps.require` accepts, or `path`, the application named in the URL on the wizard's own routes.

### `auth`

Session bootstrap: signup, login, refresh, invite acceptance, logout and the caller's own identity. The only routes that need no credential are here, because they are how a caller gets one.

| Method | Path | Credential | Permission | Purpose |
|---|---|---|---|---|
| POST | `/auth/accept-invite` | **none** | - | Accept invite |
| POST | `/auth/login` | **none** | - | Login |
| POST | `/auth/logout` | session | (session only) | Logout |
| GET | `/auth/me` | session | (session only) | Me |
| POST | `/auth/refresh` | **none** | - | Refresh |
| POST | `/auth/signup` | **none** | - | Signup |

### `orgs`

The calling session's own tenant.

| Method | Path | Credential | Permission | Purpose |
|---|---|---|---|---|
| GET | `/orgs/current` | session | (session only) | Current org |
| PATCH | `/orgs/current` | session | `manage_org` (`?app_id=`) | Rename org |

### `users`

Members of the tenant and their role bindings, org-wide or per application.

| Method | Path | Credential | Permission | Purpose |
|---|---|---|---|---|
| GET | `/users` | session | (session only) | List users |
| PUT | `/users/{user_id}/roles` | session | `manage_org` (`?app_id=`) | Set user roles |

### `invites`

Invitations into the tenant. The invite token is in the 201 and nowhere else; revoking one is immediate.

| Method | Path | Credential | Permission | Purpose |
|---|---|---|---|---|
| GET | `/invites` | session | `manage_org` (`?app_id=`) | List invites |
| POST | `/invites` | session | `manage_org` (`?app_id=`) | Create invite |
| DELETE | `/invites/{invite_id}` | session | `manage_org` (`?app_id=`) | Revoke invite |

### `roles`

The frozen roles and permissions matrix, read-only for everyone.

| Method | Path | Credential | Permission | Purpose |
|---|---|---|---|---|
| GET | `/roles/matrix` | session | (session only) | Roles matrix |

### `oncall`

Rotations that escalation policies page.

| Method | Path | Credential | Permission | Purpose |
|---|---|---|---|---|
| GET | `/oncall/rotations` | session | (session only) | List rotations |
| POST | `/oncall/rotations` | session | `manage_org` (`?app_id=`) | Create rotation |
| DELETE | `/oncall/rotations/{rotation_id}` | session | `manage_org` (`?app_id=`) | Delete rotation |
| PUT | `/oncall/rotations/{rotation_id}` | session | `manage_org` (`?app_id=`) | Update rotation |

### `escalation`

Escalation policies, their ordered steps and a test fire that pages nobody.

| Method | Path | Credential | Permission | Purpose |
|---|---|---|---|---|
| GET | `/escalation/policies` | session | (session only) | List policies |
| POST | `/escalation/policies` | session | `manage_org` (`?app_id=`) | Create policy |
| DELETE | `/escalation/policies/{policy_id}` | session | `manage_org` (`?app_id=`) | Delete policy |
| PUT | `/escalation/policies/{policy_id}` | session | `manage_org` (`?app_id=`) | Update policy |
| POST | `/escalation/policies/{policy_id}/test` | session | `run_drills` (`?app_id=`) | Test fire policy |

### `events`

Replay of the event log, the causal chain of one event, and the seals that make a stored chain provable.

| Method | Path | Credential | Permission | Purpose |
|---|---|---|---|---|
| GET | `/events` | session | `read_audit` (`?app_id=`) | Replay |
| GET | `/events/seals` | session | `read_audit` (`?app_id=`) | List seals |
| POST | `/events/seals` | session | `close_incident` (`?app_id=`) | Create seal |
| POST | `/events/seals/{seal_id}/verify` | session | `read_audit` (`?app_id=`) | Verify seal |
| GET | `/events/{event_id}` | session | `read_audit` (`?app_id=`) | Get event |
| GET | `/events/{event_id}/chain` | session | `read_audit` (`?app_id=`) | Causal chain |

### `adapters`

The adapter registry read off the manifests on disk, the capability matrix, and per-application connector bindings with their health.

| Method | Path | Credential | Permission | Purpose |
|---|---|---|---|---|
| GET | `/adapters` | session | `manage_apps` (`?app_id=`) | List adapters |
| GET | `/adapters/capability-matrix` | session | `manage_apps` (`?app_id=`) | Capability matrix |
| GET | `/adapters/{adapter_id}` | session | `manage_apps` (`?app_id=`) | Get adapter |
| GET | `/connectors` | session | `manage_apps` (`?app_id=`) | List connectors |
| POST | `/connectors` | session | `manage_apps` (`?app_id=`) | Create connector |
| GET | `/connectors/health` | session | `manage_apps` (`?app_id=`) | What the S23 integration health screen reads: direction, last pull, last push, freshness, errors and the SIMULATED flag - the bindings the health... |
| DELETE | `/connectors/{binding_id}` | session | `manage_apps` (`?app_id=`) | Delete connector |
| GET | `/connectors/{binding_id}` | session | `manage_apps` (`?app_id=`) | Get connector |
| PATCH | `/connectors/{binding_id}` | session | `manage_apps` (`?app_id=`) | Update connector |

### `onboarding`

The seven wizard steps: basics, connect and discover, map, topology, SLOs and policy, baseline, test - plus the readiness score.

| Method | Path | Credential | Permission | Purpose |
|---|---|---|---|---|
| GET | `/apps` | session | `manage_apps` (`?app_id=`) | List applications |
| POST | `/apps` | session | `manage_apps` (`?app_id=`) | Register application |
| DELETE | `/apps/{app_id}` | session | `manage_apps` (path) | Remove application |
| GET | `/apps/{app_id}` | session | `manage_apps` (path) | Get application |
| PATCH | `/apps/{app_id}` | session | `manage_apps` (path) | Update application |
| POST | `/apps/{app_id}/actions/import` | session | `manage_apps` (path) | Import the actions the application publishes in its own manifest. |
| PATCH | `/apps/{app_id}/actions/{action_id}` | session | `manage_apps` (path) | Override action |
| POST | `/apps/{app_id}/baseline` | session | `manage_apps` (path) | Learn baseline |
| POST | `/apps/{app_id}/connection-tests` | session | `manage_apps` (path) | Run connection tests |
| POST | `/apps/{app_id}/discover` | session | `manage_apps` (path) | Probe the application's base URL for the frozen observe contract, and the machine's well-known monitoring ports, without a credential and without... |
| PUT | `/apps/{app_id}/governance` | session | `manage_apps` (path) | SLO targets, the severity matrix and the policy defaults. |
| PUT | `/apps/{app_id}/mapping` | session | `manage_apps` (path) | Set mapping |
| GET | `/apps/{app_id}/readiness` | session | `manage_apps` (path) | Get readiness |
| POST | `/apps/{app_id}/test` | session | `manage_apps` (path) | Everything at once: every connector tested in its own direction, every journey probe run in every declared region, and the readiness score... |
| GET | `/apps/{app_id}/topology` | session | `manage_apps` (path) | Get topology |
| PUT | `/apps/{app_id}/topology` | session | `manage_apps` (path) | Upload topology |

### `detection`

What the platform currently believes about an application: every mapped signal against its rolling baseline, the weighted health score per region, SLO burn rates, corroboration and the severity the application's own matrix would propose. Detection proposes; only a human declares.

| Method | Path | Credential | Permission | Purpose |
|---|---|---|---|---|
| GET | `/apps/{app_id}/detection` | session | `manage_apps` (path) | The current stance, every signal against its baseline, the health score per region, SLO burn, corroboration and the severity the matrix would propose. |
| POST | `/apps/{app_id}/detection/evaluate` | session | `manage_apps` (path) | Run an evaluation now instead of waiting for the next tick. |
| GET | `/apps/{app_id}/rollups` | session | `manage_apps` (path) | The five-second buckets a verdict was derived from, so a proposal can be checked against the measurements rather than believed. |

### `incidents`

The incident lifecycle: steady, watch and proposed are detection's stances; active, recovering and closed are an incident, and only a person moves between the two halves. Reads are session-scoped to the caller's tenant and carry no permission, because the frozen roles matrix gives the SME - the role that answers a page - none; every write names declare_incident, change_severity or close_incident. Also the change window: a deployment announced as started and never announced as completed.

| Method | Path | Credential | Permission | Purpose |
|---|---|---|---|---|
| GET | `/apps/{app_id}/change-window` | session | `manage_apps` (path) | Changes announced as started and never announced as completed - the chip beside the health score, sourced from real deploy-hook calls. |
| GET | `/apps/{app_id}/lifecycle` | session | `manage_apps` (path) | Where this application sits: a detection stance, or an incident somebody declared - and the response says which of the two it is reading. |
| GET | `/incidents` | session | (session only) | The tenant's incidents. |
| POST | `/incidents` | session | `declare_incident` (`?app_id=`) | Declare an incident. |
| GET | `/incidents/{incident_id}` | session | (session only) | One incident with its whole transition history: who moved it, when, and what they said. |
| POST | `/incidents/{incident_id}/close` | session | `close_incident` | Close an incident. |
| POST | `/incidents/{incident_id}/itsm-sync` | session | `declare_incident` | Try the ITSM again now instead of waiting for the retry sweep. |
| PATCH | `/incidents/{incident_id}/severity` | session | `change_severity` | Re-grade an open incident. |
| POST | `/incidents/{incident_id}/state` | session | `declare_incident` | Move an incident to recovering, or back to active after a regression. |

### `ingest`

Ingest keys, and the five frozen push endpoints a sending application posts to.

| Method | Path | Credential | Permission | Purpose |
|---|---|---|---|---|
| POST | `/api/v1/ingest/alerts` | ingest key + HMAC | - | Accept a batch of alerts |
| POST | `/api/v1/ingest/changes` | ingest key + HMAC | - | Accept a batch of change events (deployments, config, flags) |
| POST | `/api/v1/ingest/messages` | ingest key + HMAC | - | Accept a batch of collaboration messages |
| POST | `/api/v1/ingest/tickets` | ingest key + HMAC | - | Accept a batch of ITSM tickets |
| POST | `/api/v1/ingest/topology` | ingest key + HMAC | - | Accept a topology snapshot |
| GET | `/ingest-keys` | session | `manage_apps` (`?app_id=`) | List ingest keys |
| POST | `/ingest-keys` | session | `manage_apps` (`?app_id=`) | Issue ingest key |
| DELETE | `/ingest-keys/{key_id}` | session | `manage_apps` (`?app_id=`) | Revoke ingest key |
| GET | `/ingest-keys/{key_id}` | session | `manage_apps` (`?app_id=`) | Get ingest key |
| POST | `/ingest-keys/{key_id}/rotate` | session | `manage_apps` (`?app_id=`) | Issue a successor. |

### `war-room`

The room an incident is run from: membership derived from the blast radius, paging with per-row acknowledgement countdowns and next-in-rotation escalation, the join brief, the live bridge and the task board. Four writes here carry no permission and are self-scoped instead - acknowledging, joining, speaking and completing your own task - because the frozen roles matrix gives the SME none, and the entity refusing to act on anybody else's behalf is a tighter control than a permission any holder could use on anybody's page.

| Method | Path | Credential | Permission | Purpose |
|---|---|---|---|---|
| GET | `/incidents/{incident_id}/bridge` | session | (session only) | The live transcript. |
| POST | `/incidents/{incident_id}/bridge` | session | (session only) | Say something. |
| GET | `/incidents/{incident_id}/brief` | session | (session only) | What somebody walking in at minute forty needs to know. |
| GET | `/incidents/{incident_id}/pages` | session | (session only) | The ack board, oldest first, each row carrying its own countdown. |
| POST | `/incidents/{incident_id}/pages` | session | `declare_incident` | Page somebody who was not in the blast radius. |
| POST | `/incidents/{incident_id}/pages/{page_id}/ack` | session | (session only) | "I have this." Self-scoped and carries no permission, because the role that answers a page holds none. |
| POST | `/incidents/{incident_id}/pages/{page_id}/escalate` | session | `declare_incident` | Escalate now rather than waiting out the countdown. |
| GET | `/incidents/{incident_id}/tasks` | session | (session only) | Commitments with an owner and a due time, harvested or typed. |
| POST | `/incidents/{incident_id}/tasks` | session | `declare_incident` | Put work on the board. |
| PATCH | `/incidents/{incident_id}/tasks/{task_id}` | session | `declare_incident` | Reassign, re-time or cancel - all decisions about somebody else's work. |
| POST | `/incidents/{incident_id}/tasks/{task_id}/done` | session | (session only) | Tick your own task. |
| GET | `/incidents/{incident_id}/war-room` | session | (session only) | The room: who should be there, who was paged, who turned up, and the roles nobody holds. |
| POST | `/incidents/{incident_id}/war-room/join` | session | (session only) | Turn up. |
| GET | `/my/pages` | session | (session only) | What is on this person's phone. |

### `platform`

Liveness, the Prometheus scrape, platform status, the dead-letter queue and agent-run traces.

| Method | Path | Credential | Permission | Purpose |
|---|---|---|---|---|
| GET | `/health` | **none** | - | Health |
| GET | `/metrics` | **none** | - | Metrics |
| GET | `/platform/agent-runs` | session | `read_audit` (`?app_id=`) | List agent runs |
| GET | `/platform/agent-runs/{run_id}` | session | `read_audit` (`?app_id=`) | Get agent run |
| GET | `/platform/dead-letters` | session | `manage_apps` (`?app_id=`) | List dead letters |
| POST | `/platform/dead-letters/{dead_letter_id}/requeue` | session | `manage_apps` (`?app_id=`) | Requeue dead letter |
| GET | `/platform/status` | session | `manage_apps` (`?app_id=`) | Platform status |
| WS | `/ws` | session | (session only) | Notification socket |

<!-- END GENERATED ROUTE CATALOGUE -->

## Identity, org and on-call (REQ-010, REQ-012, REQ-014)

Bootstrap first. `POST /auth/signup` creates a tenant, its first user (org owner) and a session
in one transaction; `POST /auth/accept-invite` does the same for an invited user against an
existing tenant. Both answer 201 with `{user, org, tokens}`. `POST /auth/login` answers the same
shape, rate limited per `(org, email)` so one account being attacked never locks out the
tenant, and a rate-limited attempt is itself audited. `POST /auth/refresh` rotates: the presented
refresh token is spent and a new pair comes back, so a stolen refresh token is usable at most
once and its use is detectable. `POST /auth/logout` revokes the session; the access token is
short-lived by design and the revocation store is what makes logout immediate.

`GET /auth/me` is what a cockpit calls on load: the principal's ids, display name, role bindings
and the *resolved* permission set, so the UI never re-derives the matrix. `GET /roles/matrix`
returns the frozen matrix itself, for screens that need to explain a denial.

Org management is `manage_org`, which the roles matrix grants to the org owner alone:
`PATCH /orgs/current` (rename), `POST|GET /invites` and `DELETE /invites/{id}` (issue, list,
revoke - the invite token is in the 201 and nowhere else), `PUT /users/{user_id}/roles`
(replaces the whole binding set, org-wide or per application), `POST|PUT|DELETE
/oncall/rotations…` and `POST|PUT|DELETE /escalation/policies…`.
`POST /escalation/policies/{id}/test` is `run_drills` rather than `manage_org`: it returns the
page plan the policy would produce, step by step with its timers, and pages nobody.

## Events, replay and the platform surface (REQ-023, REQ-025)

`GET /events` replays the log: `after_seq`, `until`, repeated `topics`, `incident_id`, `plane`
and `limit`, answering a page plus the head sequence, so a client resumes from `lastSeq` rather
than re-reading. `GET /events/{id}` is one envelope and `GET /events/{id}/chain` is its causal
chain - the events that caused it and the events it caused - which is what makes "why did this
happen" answerable out of interleaved noise. All three are `read_audit`.

Seals make a stored chain provable. `POST /events/seals` (`close_incident`) freezes the chain up
to a sequence with a hash binding `seq`, `event_id`, `payload_hash` and the previous hash;
`GET /events/seals` lists them and `POST /events/seals/{id}/verify` recomputes, both
`read_audit`. The split is deliberate: an auditor can verify everything and seal nothing.

The operator surface is `GET /platform/status` (components, bus head and per-group lag,
dead-letter counts, the clock source with a visible drill badge, worker jobs and retention
watermarks), `GET /platform/dead-letters` with
`POST /platform/dead-letters/{id}/requeue`, and `GET /platform/agent-runs[/{id}]`
(`read_audit`) for what the agents did and what it cost. Requeue records the intent, audits it,
and reports `drained_here` honestly: on a node that runs no consumer for that group, the requeue
is real and the drain happens elsewhere.

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

What screen 23 reads. Every `GET /connectors` field, plus a **pull** side and a **push** side,
because a single freshness number cannot be honest about both directions:

| Field | Side | Meaning |
|---|---|---|
| `freshness_s` | pull | seconds since SignalForge last read the integrated system successfully; `null` when it never has |
| `last_event_at` | push | when anything last arrived on this binding's live ingest keys |
| `event_freshness_s` | push | seconds since that arrival; `null` when nothing ever has |
| `ingest_keys` | push | how many *live* keys name this binding |
| `ingest_paths` | push | the frozen ingest paths those keys post to |
| `ingest_accepted` / `ingest_duplicates` / `ingest_rejected` | push | counters summed across those keys |
| `ingest_last_error` | push | why the most recently used key refused a sender; the sender itself only ever saw a bare 401 |

The join is by `ingest_keys.binding_id`, tenant-scoped and app-scoped like the bindings
themselves, and **revoked keys are excluded**: a credential that can no longer authenticate is
not a live sender, and leaving its last arrival on the row would keep an integration looking
healthy after the operator deliberately cut it off. Two reads serve the whole screen however
many integrations a tenant holds.

Why this exists: a push binding cannot be measured by pinging. SignalForge is the *server* for
those, so it is reachable whether or not the sender died a week ago, and a row that reported
"checked 2s ago" would be answering a question nobody asked. Keys that name no binding - the
canonical senders, and the collaboration and itsm streams that can never have a push adapter -
are not on this endpoint; the screen lists them from `GET /ingest-keys`.

## Onboarding: `/apps` (REQ-036)

All of these require `manage_apps`. The application is named in the **path**, and the RBAC
dependency reads it from there (`deps.require_app`), so an operator whose `manage_apps` binding
is scoped to one application is evaluated against that application rather than org-wide. The two
routes with no `app_id` in the path - listing and registering - are org-level and use the query
form (`deps.require`).

| Route | Step | Notes |
|---|---|---|
| `GET /apps` | - | each application with its readiness score and `ready` flag |
| `POST /apps` -> 201 | Basics | 409 if the app_id is taken in this tenant; the response carries the application, the profile (with the policy floor already stored) and the readiness score |
| `GET /apps/{app_id}` | - | application + profile + readiness |
| `PATCH /apps/{app_id}` | Basics | partial; `clear_base_url` removes it |
| `DELETE /apps/{app_id}` -> 204 | - | 409 while any connector or active ingest key still points at the application |
| `POST /apps/{app_id}/discover` | Connect | unauthenticated outbound probes; writes an event, never a binding |
| `POST /apps/{app_id}/connection-tests` | Connect | all bindings, or one via `binding_id`; direction-correct results |
| `PUT /apps/{app_id}/mapping` | Map | signals, region label, regions, journeys, probes - each omitted field is unchanged |
| `POST /apps/{app_id}/actions/import` | Map | imports the application's action manifest through its bound actions connector |
| `PATCH /apps/{app_id}/actions/{action_id}` | Map | risk override, approver roles, execution mode, enabled |
| `PUT /apps/{app_id}/topology` | Topology | `{"document": {...}}`; answers `unchanged: true` for content already imported |
| `GET /apps/{app_id}/topology` | Topology | the latest snapshot plus the version list |
| `PUT /apps/{app_id}/governance` | SLOs | SLOs and the severity matrix replace whole; the policy is patched and the floor is enforced on the merge |
| `POST /apps/{app_id}/baseline` | Baseline | `{"samples": 3..30}`; returns what was learned and what was missed, with reasons |
| `POST /apps/{app_id}/test` | Test | runs every connector test and every journey probe, stores the summary, returns the recomputed readiness |
| `GET /apps/{app_id}/readiness` | - | the thirteen weighted checks, the score, `ready`, and the outstanding list blocking-first |

Status codes worth naming:

| Code | When |
|---|---|
| 404 | the application is not in this tenant - identical body whether it belongs to another tenant or to nobody |
| 409 | a duplicate app_id, or a removal blocked by a live connector or ingest key |
| 422 | domain validation *and* every policy-floor refusal, including for an org owner |
| 502 | the integrated system failed during an import, a test read or a baseline run - `{"detail": ..., "error": "AdapterUnavailableError"}` |
| 504 | the integrated system did not answer in time; the outcome is unknown, not failed |

502 and 504 are new with this task and belong to the whole API: they are how "the thing
SignalForge was asked to call is broken" is told apart from "SignalForge is broken". No adapter
error message carries a credential - the transport that raises them redacts its own repr.

## Detection: `/apps/{app_id}/detection` (REQ-040, REQ-041, REQ-042, REQ-046)

Three routes, all `manage_apps` with the application named in the path. They read what the
platform currently believes; **none of them declares anything**. Declaring, changing a severity
and closing are human acts (REQ-043) and live under `/incidents` below.

| Route | What it does |
|---|---|
| `GET /apps/{app_id}/detection` | the stance, every mapped signal against its baseline, the health score per region, SLO burn, corroboration, the storm clusters and the severity the application's own matrix would propose |
| `POST /apps/{app_id}/detection/evaluate` | run an evaluation now instead of waiting for the next five-second tick. A human override of the cadence, audited as one |
| `GET /apps/{app_id}/rollups` | the five-second buckets a verdict was derived from: `signal_key`, `region` and `window_s` (max 86400) |

`GET .../detection` **advances nothing**. It summarises the signal states already stored rather
than running an evaluation, because a read that aged a breach into an anomaly would make every
proposal depend on how often somebody refreshed a screen. `POST .../detection/evaluate` is the
one that moves the streaks, and it writes an audit entry naming who asked.

The stance is the field to read first:

| Stance | When | What it means to a person |
|---|---|---|
| `steady` | nothing is breaching | nobody is being told anything |
| `watch` | a breach is being observed, a slow burn is running, or a journey probe is failing | a quiet "AI is checking" banner, no page |
| `proposed` | corroboration holds **and** a fast burn crossed, a customer-facing family is anomalous, or a journey failed three probe runs | a proposal sheet with evidence, and a Declare button a human presses |

Every conclusion carries what it was drawn from. `severity.evidence[]` and
`corroboration.evidence[]` hold refs of the form `rollup:<signal>/<region>@<bucket>`,
`slo:<slo_id>/<window>/<region>`, `journey:<journey>/<region>` and
`alert-cluster:<cluster_id>` - each of them re-readable, the first through
`GET /apps/{app_id}/rollups`. A signal state names its `basis` (`level` or `rate`, decided
from the shape of the window rather than configured), its `threshold`, its `baseline_source`
(`rolling`, `onboarding` or `frozen` - held while the signal is breaching so a long incident
cannot be averaged into normality) and its `streak` out of three.

Two response fields are constants rather than state: `severity.requires_human` is always true
and `severity.declared` is always false. They are published so that a client cannot render a
proposal as though it were an incident.

| Code | When |
|---|---|
| 404 | the application is not in this tenant - identical body whether it belongs to another tenant or to nobody |
| 422 | `window_s` outside 0..86400, or a signal key that is not mapped |

## Incidents and the change window: `/incidents`, `/apps/{app_id}/lifecycle` (REQ-043, REQ-044)

Detection reaches `proposed` and stops. Everything in this section is the other side of that
line, and the guard on each route says which side it is on.

| Route | Credential | Permission |
|---|---|---|
| `POST /incidents?app_id=…` | session | `declare_incident` |
| `GET /incidents` · `GET /incidents/{id}` | session | **none** |
| `PATCH /incidents/{id}/severity` | session | `change_severity` |
| `POST /incidents/{id}/state` | session | `declare_incident` |
| `POST /incidents/{id}/close` | session | `close_incident` |
| `POST /incidents/{id}/itsm-sync` | session | `declare_incident` |
| `GET /apps/{app_id}/lifecycle` · `GET /apps/{app_id}/change-window` | session | `manage_apps` |

The reads carry no permission on purpose. `ROLE_PERMISSIONS[Role.SME]` in the frozen matrix is
empty, so the person most likely to be paged holds none at all; any permission on the read path
would lock the incident out of exactly the people it exists to reach. They are session-scoped to
the caller's own tenant instead, and a cross-tenant read answers 404 - the same reasoning `/ws`
records as DR-031. The four writes name the three incident permissions the matrix does define,
and the incident-scoped ones resolve the application off the incident
(`x-signalforge-app-scope: incident`).

**Declaring.** `POST /incidents?app_id=…` takes `{title?, summary?, severity?, reason?}`. With no
`severity` the one detection's matrix proposed is accepted and recorded as `severity_source:
proposed`; with one it is recorded as `chosen` and `overrode_detection` is true. With neither a
proposal nor a severity the call is a 422 naming what it needs - the platform will not invent a
grade. A second declaration while one is open is a 409 naming the incident to join.

The response carries the whole record, including `proposal` - a **snapshot** of what detection
was saying at that instant, evidence refs included, copied rather than referenced because the
rollups behind those refs age out in hours - and `change_windows`, what was in flight when the
incident was declared. `declared_by_human` is a published constant and is always true.

**Moving it.** `POST /incidents/{id}/state` takes `active` or `recovering` only; closing has its
own route because it has its own permission, and a stance is not something a person sets on an
incident. Re-submitting the severity that is already set is a no-op with no event; moving to the
state it is already in is a 409. `GET /incidents/{id}` returns the full `transitions[]` history:
who moved it, when, from what to what, and what they said. A severity change appears there as a
transition whose `from_state` equals its `to_state`.

**The ticket.** `ticket.state` is `not_configured`, `pending`, `synced`, `failed` or `uncertain`.
`uncertain` means a call timed out and the ticket may or may not exist; the retry sweep and
`POST /incidents/{id}/itsm-sync` both reconcile - `get` before `create` - so one incident keeps
one ticket. A ticket queue that is down never blocks a declaration.

**The change window.** `GET /apps/{app_id}/change-window` returns `open` (the chip), `windows[]`
(in flight), `stale[]` (started, never completed, older than `max_age_s`) and `recent[]`
(finished within a day). It is a projection of the `deployment.*` events a pipeline posted to
`/api/v1/ingest/changes`, keyed on `change_id`, so a re-triggered pipeline is one window and a
`completed` delivered before its `started` cannot reopen a finished deployment.

`GET /apps/{app_id}/lifecycle` is the two halves in one read: `state`, `source` (`detection` or
`incident`), the stance and the health the detector is reporting, the open incident if there is
one, the change window, and `next_states` / `human_moves` so a screen can tell which buttons
exist from which a person may press. `declare_requires_human` is a published constant and is
always true.

| Code | When |
|---|---|
| 404 | the application or the incident is not in this tenant - identical body whether it belongs to another tenant or to nobody |
| 409 | a second declaration while one is open; a move the lifecycle refuses from the state it is in; closing an incident that is closed |
| 422 | a severity that is not P0-P3, a state this route may not set, or a declaration with neither a proposal nor a severity |

## The war room: `/incidents/{id}/war-room`, `/my/pages` (REQ-045)

The room opens because the incident was declared - there is no route that creates one. Everything
here reads or acts on a room that already exists.

The guard split is the sharpest in this API and it comes from the frozen roles matrix rather than
from preference: `ROLE_PERMISSIONS[Role.SME]` is empty, so the person most likely to be paged
holds nothing.

| Route | Credential | Permission |
|---|---|---|
| `GET /incidents/{id}/war-room` | session | **none** |
| `GET /incidents/{id}/brief` | session | **none** |
| `GET /incidents/{id}/pages` · `GET /incidents/{id}/tasks` · `GET /incidents/{id}/bridge` | session | **none** |
| `GET /my/pages` | session | **none** |
| `POST /incidents/{id}/pages` | session | `declare_incident` |
| `POST /incidents/{id}/pages/{pid}/escalate` | session | `declare_incident` |
| `POST /incidents/{id}/tasks` · `PATCH /incidents/{id}/tasks/{tid}` | session | `declare_incident` |
| `POST /incidents/{id}/pages/{pid}/ack` | session | **none - self-scoped** |
| `POST /incidents/{id}/war-room/join` | session | **none - self-scoped** |
| `POST /incidents/{id}/bridge` | session | **none - self-scoped** |
| `POST /incidents/{id}/tasks/{tid}/done` | session | **none - self-scoped** |

**Self-scoped** is a stronger control than a permission, not a weaker one. The actor is the
session and can never be a request parameter, and the entity refuses the act when it names
somebody else: `declare_incident` would let any holder acknowledge anybody's page, whereas
`Page.acknowledged` refuses everyone but the addressee. `/my/pages` has no recipient parameter at
all, which is what lets the S22 mobile paging page carry no permission. The four exemptions are
pinned by name in `signalforge/tests/security/test_warroom_boundary.py`, in both directions, and
joined to the `SESSION_ONLY_WRITES` list in `test_api_surface.py` so neither can drift.

### `GET /incidents/{id}/war-room`

Returns the room, its blast radius, the roles it required, the roles nobody held, its members, its
pages and its tasks in one read - the S9 shell is one screen and should be one request.

Three member fields are separate on purpose. `required` is what the blast radius called for,
`paged` is whether a page went out, `present` is whether they turned up. "Four were required, four
were paged, two are here" is the sentence an incident commander needs at minute ten, and one
status field cannot say it.

`gaps` lists required roles nobody is bound to for this application. A war room with no comms lead
still runs; the gap is stated rather than rendered as a shorter member list that looks complete.

`blast_radius.source` is `proposal` when detection had a verdict at declare time and `declaration`
when only the severity a person typed was available.

### `GET /incidents/{id}/pages` and `GET /my/pages`

The ack board and one person's pager. Every row carries `ack_deadline_at` (the stored fact) and
`countdown_s` (the same fact against the server's clock at the moment of the read), so a client
with a skewed clock still shows a timer that agrees with the escalation sweep. `countdown_s` is
negative once overdue and null once the page is settled; `reached` is false when no channel took
it, which is a page that fails at once rather than counting down toward an escalation to channels
that also do not exist.

### `POST /incidents/{id}/pages/{pid}/ack`

Idempotent and terminal. A second call returns the same page with the same `acked_at` and writes
no second event; the first note is the record and a later one does not rewrite it. Answering also
marks the answerer present in the room.

| Code | When |
|---|---|
| 403 | the page names somebody else - a bare "permission denied"; the reason goes to the audit chain, not to the caller |
| 404 | the page is not in this tenant |
| 409 | the page is failed, escalated or cancelled, and none of those can be answered |

### `POST /incidents/{id}/pages/{pid}/escalate`

A commander deciding not to wait. Returns the **successor** page - the one now ringing - because
that is the row the board needs to start counting down. Escalating an acknowledged or already
escalated page is 409; running out of people to page is 409 with `warroom.page.escalation.exhausted`
published on the timeline first, because the moment nobody is left is the moment somebody has to
be told.

### `GET /incidents/{id}/brief`

The join brief, addressed to the caller when they are in the room. `source` says who wrote it:
`template` today, `model` when S5's writer lands, `degraded` when a model failed back to the
template. `text` is the same brief flattened for a channel with no structure - an SMS, a voice
call, a bridge post - built in one place so the phone and the screen cannot drift apart.

`caveats` is always populated. A brief drawn from a declaration made while the detector was down
says so rather than reading like one drawn from corroborated evidence.

### `GET` / `POST /incidents/{id}/bridge`

The live transcript, and speaking on it. With no collaboration connector bound, the read returns
`has_bridge: false` with a reason and an empty list - the panel says there is no bridge rather
than rendering silence - and the post is 404. Speaking marks the speaker present, and a commitment
in what was said becomes a task at that moment, while the author is known with certainty.

### `GET` / `POST` / `PATCH` / `POST …/done` on `/incidents/{id}/tasks`

Commitments with an owner and a due time, harvested from the bridge or typed. `owner_user_id` is
nullable and `owner_label` sits beside it: a commitment naming somebody the platform cannot
resolve to exactly one user is a visibly unassigned task carrying the name as typed, never a task
owned by a guess. `evidence_ref` holds what was actually said, which is how a disputed task is
settled.

Creating and reassigning are command work and carry `declare_incident`. Completing is self-scoped:
an owned task is completed by its owner, and an unassigned one by anybody in the tenant - because
somebody has to be able to close out work that was actually done.

| Code | When |
|---|---|
| 403 | completing somebody else's task |
| 404 | the task or the incident is not in this tenant |
| 422 | assigning to a user outside the organisation, completing a cancelled task, or reassigning a settled one |

## Ingest keys (REQ-032)

All `manage_apps`. An ingest key is the credential a *sending application* holds: a bearer
token plus an HMAC shared secret, scoped to one tenant, one `app_id` and one stream.

### `POST /ingest-keys` -> 201

```json
{"app_id": "checkout", "stream": "changes", "name": "checkout ci", "binding_id": null}
```

`stream` is one of `changes`, `alerts`, `topology`, `messages`, `tickets`. `binding_id`
optionally names a **push** connector binding of the matching kind whose adapter normalises
vendor-shaped payloads; omit it when the sender posts the canonical contract shape.

The 201 is the only response that ever carries credential material:

```json
{
  "id": "3f0c...", "app_id": "checkout", "stream": "changes", "name": "checkout ci",
  "path": "/api/v1/ingest/changes",
  "token": "sfi_...", "secret": "...",
  "token_prefix": "sfi_S_5VtZit", "binding_id": null, "rotated_from": null,
  "active": true, "created_at": "2026-09-01T12:00:00Z", "created_by": "u-1",
  "revoked_at": null, "revoked_by": null,
  "last_used_at": null, "last_event_at": null, "freshness_s": null, "last_error": null,
  "accepted_count": 0, "duplicate_count": 0, "rejected_count": 0
}
```

`token` and `secret` are returned once and cannot be shown again: only the token's sha256 is
stored, and the secret lives Fernet-encrypted under `ingest/<key_id>/secret`. Prefix the
returned `path` with your API base URL to get the ingest URL.

- 422 when the named binding is not found, is not installed, is the wrong adapter kind for the
  stream, is not push-mode, or does not declare `normalize`.

### `GET /ingest-keys`

Query: `app_id`, `stream`, `include_revoked` (default false). Every field above except `token`
and `secret`. This is what an integration health screen reads for a push integration:
`freshness_s` is seconds since anything actually *arrived*, which a ping cannot measure because
SignalForge is the server; `last_error` is why the most recent request was refused.

### `GET /ingest-keys/{id}` · `POST /ingest-keys/{id}/rotate` -> 201 · `DELETE /ingest-keys/{id}` -> 200

`rotate` issues a **successor** (new id, new token, new secret, `rotated_from` set) and leaves
the original live, so a sender can be moved over without dropping requests in flight. `DELETE`
revokes rather than deletes - the receipts a key wrote are the record that a sender's events
were already accepted - and destroys the shared secret, so a revoked key cannot produce a
signature that would verify. Both are audited. A cross-tenant id answers 404 with the same
message as one that never existed.

## `POST /api/v1/ingest/{changes,alerts,topology,messages,tickets}` -> 202 (REQ-032, REQ-017)

The five paths are frozen in `contracts/ingest.py` and registered literally, not as a template.
These are the only product routes that resolve a *machine* credential rather than a human
session, and they carry no permission: the caller is a sending application, and the roles
matrix describes people. The compensating control is that the key names the tenant, the app and
the stream, and the request body carries none of the three - there is no parameter through
which a verified sender could reach past its own key.

Headers:

| Header | Required | Meaning |
|---|---|---|
| `Authorization: Bearer <token>` | yes | the ingest key |
| `X-SignalForge-Timestamp` | yes | unix seconds at signing time; must be within 300s |
| `X-SignalForge-Signature` | yes | hex HMAC-SHA256 of `"<timestamp>.<raw body>"` |
| `X-SignalForge-Key-Id` | no | which key the sender signed with; a mismatch is refused |

Body - the frozen batch shape, payloads in canonical form (or the vendor's, when the key names
a normalising binding):

```json
{"source_system": "jenkins", "simulated": false,
 "items": [{"event_id": "1c9f...", "occurred_at": "2026-09-01T12:00:00Z",
            "payload": {"change_id": "deploy-42", "change_type": "deployment",
                        "service": "checkout", "status": "started",
                        "started_at": "2026-09-01T12:00:00Z"}}]}
```

202 body:

```json
{"accepted": 4, "duplicates": 1,
 "rejected": [{"index": 3, "reason": "service: Field required"}],
 "request_id": "bd664c52639040f7"}
```

Status codes:

| Code | When |
|---|---|
| 202 | authentic request; per-item outcomes are in the body |
| 401 | missing, unknown or revoked token; missing, forged or stale signature; key-id mismatch. Always the bare `{"detail": "authentication failed"}` |
| 403 | the key is bound to a different stream |
| 413 | body larger than the configured cap (2 MiB default) |
| 422 | authentic but the batch wrapper is invalid - no `source_system`, empty or oversized `items`, unknown fields, non-JSON body |
| 429 | per-key rate limit, with `Retry-After` |

`event_id` is the idempotency unit, unique per `(tenant, event_id)`: a repeat is counted in
`duplicates` and published nothing. A rejected item does not consume its id, so a sender can
fix the payload and resend with the same one.

## Events emitted

| Topic | Plane | Tenant | When |
|---|---|---|---|
| `system.connector.bound` | signal | owning tenant | a binding is created |
| `system.connector.updated` | signal | owning tenant | config, credentials or enabled changed |
| `system.connector.unbound` | signal | owning tenant | a binding is deleted |
| `system.connector.health` | signal | owning tenant for a binding, `_platform` for the platform's own components | every poll |
| `system.ingest.key.issued` / `.rotated` / `.revoked` | signal | owning tenant | key lifecycle; carries the display prefix, never the credential |
| `system.ingest.rejected` | signal | owning tenant | a request refused after its key resolved, or a batch with rejected items |
| `deployment.<status>` / `change.<status>` | signal | owning tenant | an accepted `changes` item |
| `alert.fired` / `alert.resolved` | signal | owning tenant | an accepted `alerts` item |
| `topology.updated` | signal | owning tenant | an accepted `topology` item |
| `collaboration.message.received` | signal | owning tenant | an accepted `messages` item |
| `itsm.ticket.updated` | signal | owning tenant | an accepted `tickets` item |
| `system.app.registered` / `.updated` / `.removed` | signal | owning tenant | Basics; `.updated` carries `base_url_changed` and the previous URL |
| `system.app.discovered` | signal | owning tenant | an auto-discovery run: what was probed, what answered, which adapters were suggested |
| `system.connector.tested` | signal | owning tenant | one per binding tested, with the direction, the outcome and each check |
| `system.app.mapping.updated` | signal | owning tenant | signals, region label, journeys and probes saved |
| `system.app.actions.updated` | signal | owning tenant | an actions import, or one action's risk / approvers / mode changed |
| `system.app.governance.updated` | signal | owning tenant | SLOs, severity matrix or policy defaults saved |
| `system.app.policy.refused` | signal | owning tenant | the policy floor refused an edit - written *before* the 422 |
| `topology.updated` | signal | owning tenant | a topology upload that produced a new version |
| `system.app.baseline.learned` | signal | owning tenant | a baseline run: what was learned and what was missed, with reasons |
| `system.app.tested` | signal | owning tenant | the wizard's test page: connector and probe outcomes |
| `detection.stance.changed` | signal | owning tenant | Steady <-> Watch <-> Proposed. Carries the reasons, the health score and the corroboration verdict |
| `detection.anomaly.opened` / `.cleared` | signal | owning tenant | a breach reached three sustained evaluations, or recovered. Carries the value, the threshold, the baseline and its source |
| `detection.signal.stale` | signal | owning tenant | a mapped signal stopped reporting. Stale is not healthy |
| `detection.slo.burn` | signal | owning tenant | the worst SLO's burn verdict changed. Fast burn proposes, slow burn opens Watch |
| `detection.health.changed` | signal | owning tenant | the weighted health score crossed a band. Worst region wins |
| `detection.alert.storm` | signal | owning tenant | alerts clustered by time and topology, with the root and the suppressed count |
| `detection.severity.proposed` | signal | owning tenant | a corroborated proposal, with `requires_human: true`, `declared: false` and the evidence refs it was drawn from |
| `detection.evaluation.forced` | signal | owning tenant | a **human** ran an evaluation off-cadence. The one detection topic with a human actor, and an audit entry |
| `system.detection.poll.degraded` / `.recovered` | signal | owning tenant | a metrics scrape started or stopped failing. Written **once** per outage, not once per five-second tick |

| `incident.declared` | **incident** | owning tenant | a person declared. Carries the severity, whether it was `proposed` or `chosen`, the stance it was declared from, the evidence and what was changing |
| `incident.severity.changed` | **incident** | owning tenant | a person re-graded an open incident, with the previous grade and the one the matrix had proposed |
| `incident.state.changed` | **incident** | owning tenant | active <-> recovering, with the reason and the incident's age |
| `incident.closed` | **incident** | owning tenant | a person closed it. Terminal: nothing reopens a closed incident |
| `incident.itsm.synced` | **incident** | owning tenant | the ticket now matches the incident. System actor, `observed` authority - the ticket queue is the system of record for tickets |
| `incident.itsm.sync.failed` / `.uncertain` | **incident** | owning tenant | the ITSM refused with a reason, or a call timed out and the ticket may or may not exist. Published on a *change* of sync state, never once per retry |
| `warroom.opened` | **incident** | owning tenant | the room assembled on declare. Carries the blast radius, the roles it required and who it resolved them to |
| `warroom.membership.gap` | **incident** | owning tenant | one event per required role nobody is bound to. Separate events rather than a list, so a gap reaches somebody who is not looking at the room |
| `warroom.member.joined` | **incident** | owning tenant | a person turned up. Human actor. Only the *first* arrival, so a polling screen cannot fill a timeline people are reading |
| `warroom.page.sent` | **incident** | owning tenant | a page went out, naming the channels that carried it and the deadline it must be answered by |
| `warroom.page.failed` | **incident** | owning tenant | no bound channel could carry it. Nobody was reached, and the row says so instead of counting down |
| `warroom.page.acknowledged` | **incident** | owning tenant | somebody answered. Human actor, and carries `answered_in_s` |
| `warroom.page.escalated` | **incident** | owning tenant | the countdown ran out, or a commander escalated by hand. `by_human` distinguishes them |
| `warroom.page.escalation.exhausted` | **incident** | owning tenant | every rotation member and policy step has been paged and nobody answered. Carries `needs_human: true` |
| `warroom.bridge.posted` | **incident** | owning tenant | somebody spoke on the bridge. Human actor; the text is not repeated in the payload, only its length |
| `warroom.task.created` / `.updated` | **incident** | owning tenant | a commitment became work, or was reassigned, completed or cancelled. System actor when harvested from the bridge, human when typed |

Every `detection.*` event except `detection.evaluation.forced` carries an `actor.type` of
`system` and a `source.authority` of `observed`: the platform measured this, it is not the
system of record for it, and an evidence chain that cannot tell those apart is one an auditor
cannot use. All of them are on the **signal** plane and carry no `incident_id`, because
detection proposes and only a human declares (REQ-043).

Every `incident.*` event is the opposite in every dimension, and the contrast is the point: the
**incident** plane, so it is kept and sealed rather than aged out; a `human` actor with the
session's own user id (the two `itsm` topics excepted - a machine talking to another machine);
`authoritative` authority, because SignalForge *is* the system of record for its own lifecycle;
and an `incident_id`, which makes the incident the partition key so one incident's events are
ordered among themselves and never wait behind another's. Each of the four human topics is also
an audit entry with the same payload, written in the same transaction.

Audit topics `audit.ingest.rejected` and `audit.ingest.duplicate` carry the same facts into the
hash chain, one entry per batch rather than one per item. Every onboarding topic above is also an
audit entry with the same payload, written in the same transaction as the change it describes.

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

## What an onboarded application serves (REQ-038)

This platform's API has a second half that is not served by this platform: the contract an
application implements so that the generic adapters can read it. It is frozen in
`contracts/observe.py`, it is what the `http-observe-*` adapters call, and an application that
serves it needs no code written for it here.

| Method and path | Returns | Credential scope the adapters bind with |
|---|---|---|
| `GET /observe/metrics` | Prometheus text exposition, `text/plain; version=0.0.4` | read_only |
| `GET /observe/logs?query&start&end&limit` | `{records: LogRecord[], total?}` | read_only |
| `GET /observe/traces?trace_id\|service\|operation\|errors_only&start&end&limit` | `{spans: TraceSpan[]}` | read_only |
| `GET /observe/health` | `HealthReport` | read_only |
| `GET /observe/topology` | `TopologySnapshot` | read_only |
| `GET /observe/actions` | an action manifest document | remediation |
| `POST /observe/actions/{action_id}/execute` | `ActionExecution` | remediation |
| `GET /observe/actions/executions/{idempotency_key}` | `ActionExecution`, or **404 meaning "never ran"** | remediation |
| `GET /observe/chaos` | a chaos manifest document | drill |
| `POST /observe/chaos/{scenario_id}/inject` | `ChaosInjection` | drill |
| `GET /observe/chaos/injections/{injection_id}` | `ChaosInjection` | drill |
| `POST /observe/chaos/injections/{injection_id}/abort` | `ChaosInjection` | drill |

Four requirements on the application, each of which an adapter depends on:

- **`/observe/metrics` is a scrape, not a query API.** It carries the current sample and no
  history, which is why `http-observe-metrics` declares `query_instant` and refuses
  `query_range` rather than fabricating a range from one point.
- **A repeated `idempotency_key` must not execute twice**, and the execution lookup must
  distinguish "never ran" (404) from "ran and failed" (200 with `state: failed`). Reconcile
  before retry is built on exactly that distinction.
- **A timeout is not a failure.** If the application cannot answer in time, the adapter reports
  `UNCERTAIN` and the platform reconciles; an application that reports success early or failure
  late makes that unreliable.
- **A guarded path should answer 401 or 403, not 404.** Auto-discovery reads either as "served,
  and it wants a credential", which is what is true; a 404 is read as "this application does not
  serve that", which would be a lie.

The reference harness in `reference-app/` implements all twelve, and
`signalforge/tests/reference/` asserts the set it maps is exactly the frozen set - neither short
nor extended. A path missing means an adapter that cannot be bound; an extra path under
`/observe` means a surface no adapter will ever call, which is how a private API starts.

### What an application's deployment pipeline sends

Nothing more than one signed POST to `/api/v1/ingest/changes`, described above. The reference
pipeline is a shell script using `curl` and `openssl` and no library, which is the point: the
ingest contract is small enough to implement from its documentation. Its `event_id` is derived
from the release, the region and the status, so a re-triggered pipeline posts the same id and
the platform answers `duplicates: 1` instead of recording a second deployment.
