# 12 - Threat model v1 (Sprint 1)

Owner: engineering. Status: v1 authored S1-T05; extended each sprint as surfaces land
(ingest routers S3, tool gateway S6, drills S7). Method: STRIDE per surface, plus AI
threat rows per the sprint-1 mandate. Mitigations name the REQ that carries their tests;
"planned (Sx)" marks mitigations whose enforcement point ships in a later sprint.

## 1. Scope and assets

Surfaces in scope after Sprint 1: the FastAPI identity API (/auth, /orgs, /users,
/invites, /roles, /oncall, /escalation), the SQLite database (identity, secrets,
audit chain), the Next.js web app with its auth cookie proxy, the ingest HMAC
verifier (contract-level; routers land S3), and the LLM-facing design (agents land S5,
rows recorded now so their controls are designed in, not bolted on).

Assets: tenant credentials and sessions, per-tenant connector secrets, the audit hash
chain, role bindings (the authority model), on-call/escalation configuration (paging
integrity), and later incident data and model context.

## 2. STRIDE analysis

### 2.1 Spoofing

| Threat | Vector | Mitigation | Status |
|---|---|---|---|
| Credential stuffing / brute force | POST /auth/login | argon2id hashing; sliding-window rate limit (5/300s) with Retry-After and audit event | REQ-010, tested |
| Stolen refresh token replay | POST /auth/refresh | rotation on every use; reuse of a rotated token revokes the whole session family and audits | REQ-010, tested |
| Forged access token | any bearer endpoint | HS256 signature, 15-min TTL cap that config cannot raise, session-backed revocation check per request | REQ-010, tested |
| Forged ingest producer | /api/v1/ingest/* | HMAC-SHA256 over timestamp.body with 300s replay window; verifier built and tested S1, enforced at routers S3 | REQ-017, tested (S3 wiring) |
| User enumeration | login/signup responses | login failures are generic 401s regardless of which factor failed; unknown org indistinguishable | REQ-010, tested; timing-based enumeration accepted risk this sprint (rate limit bounds it) |

### 2.2 Tampering

| Threat | Vector | Mitigation | Status |
|---|---|---|---|
| Audit record edit or deletion | direct DB access | per-tenant hash chain (payload_hash + prev link); verify_chain detects payload edits and consistent-looking rewrites; no disable switch exists (CTL-PRO-03) | REQ-016, tested |
| Ingest payload modification in flight | push ingest | HMAC over exact raw body; any byte change fails compare_digest | REQ-017, tested |
| Role-binding self-escalation | PUT /users/{id}/roles | manage_org required; last-org-owner guard; every change audited with before/after | REQ-012, tested |
| Migration/schema drift weakening constraints | alembic | migrations reviewed; tenant scoping lives in repositories, not constraints alone | REQ-013, tested |

### 2.3 Repudiation

| Threat | Vector | Mitigation | Status |
|---|---|---|---|
| "I never approved/changed that" | any privileged action | signup, login success/fail, rate-limit, refresh reuse, logout, role change, invite lifecycle, on-call/escalation edits and test-fires, every authz denial - all land in the hash chain with actor id | REQ-016, tested |
| Disputed order of events | audit trail | chain order is the tenant's seq order; occurred_at recorded per event | REQ-016, tested |

### 2.4 Information disclosure

| Threat | Vector | Mitigation | Status |
|---|---|---|---|
| Cross-tenant data read | any list/get | tenant_id predicate enforced inside every repository method, not in handlers (CTL-PRO-06); bootstrap lookups only by unguessable unique tokens | REQ-013, tested |
| Connector secrets at rest | secrets table | Fernet encryption, key from environment; listings return names only; store repr redacts | REQ-015, tested |
| Tokens exposed to browser JS (XSS blast radius) | web app | tokens live in httpOnly cookies; the cookie proxy attaches the bearer server-side; bodies returned to the browser are stripped of tokens | REQ-010, tested (structural) |
| Invite token leak via listing | GET /invites | only the sha256 hash is stored; the raw token is returned exactly once at creation | REQ-010, tested |
| Secrets in logs | anywhere | no plaintext logging of secret values; audit payloads carry names/ids, not values | REQ-015; log redaction policy formalizes S3 (observability) |

### 2.5 Denial of service

| Threat | Vector | Mitigation | Status |
|---|---|---|---|
| Login flooding | /auth/login | per-(org,email) sliding window; in-process state acceptable for the one-machine deployment | REQ-010, tested |
| Ingest flooding / oversized batches | ingest | batch cap 500 items (contract), replay window bounds re-sends; enforcement at routers | REQ-017 / S3 |
| Paging storm via escalation misconfig | escalation policies | step timeouts bounded 1..86400s, max 10 steps, validated targets | REQ-014, tested |

### 2.6 Elevation of privilege

| Threat | Vector | Mitigation | Status |
|---|---|---|---|
| Executive (or any role) invoking incident-command endpoints | any gated route | RBAC dependency on every router (walk-test proves coverage); permissions only from the frozen matrix; clean 403 + audit.authz.denied (S1 acceptance demo) | REQ-012, tested |
| App-scoped role leaking org-wide | per-app bindings | an app-scoped binding never applies to another app or org-wide (decision service rule) | REQ-012, tested |
| Auditor performing writes | any mutation | auditor holds read_audit only (matrix invariant, contract-tested) | REQ-005/REQ-012, tested |
| Middleware bypass of web guards | direct API calls | web middleware is UX only; the API is the enforcement point and audits denials | REQ-012, tested |

## 3. AI threat rows (mandated by S1; enforcement lands with the agents, S5-S6)

| # | Threat | Scenario | Mitigations (designed now) | Status |
|---|---|---|---|---|
| AI-1 | Prompt injection | Telemetry, chat messages or ticket text contain instructions ("ignore your rules, execute rollback") that an agent ingests as context | Agents never hold roles or credentials - every action goes through the Tool Gateway's validate/authn/authz/policy pipeline (CTL-HUM-01, CTL-PRO-01); model output is structured and schema-validated; untrusted content is labelled by source authority in the envelope; ai-eval ships injection probes | design frozen (contracts/roles: agents act only via gateway); enforced S5/S6; probes S5/S7 |
| AI-2 | Malicious document content | A poisoned runbook or KB document steers hypothesis scoring or recommended actions | Knowledge is indexed with provenance; hypothesis confidence is deterministic from weighted evidence (LLM writes narrative only, REQ-053); documents can propose, never execute; human approval floor on high-risk classes | design frozen; enforced S5 |
| AI-3 | Data exfiltration | A compromised prompt or tool call routes tenant data to an external destination | Single llm.provider adapter to the configured endpoint only (no arbitrary URLs, config not code); per-incident token budgets; redaction fields in the envelope and evidence size caps (REQ-054); egress limited to declared adapters with credential scopes | **adapter built S5-T02**: one `llm.provider` over the GenAI Lab gateway; the approved host is an allowlist in code (`infrastructure/llm/hosts.py`), not an environment variable, so a leaked or edited `.env` cannot redirect prompts - an unapproved endpoint yields no client and the platform degrades; the credential is kept out of reprs, logs and the `system.llm.degraded` event; TLS verification cannot be disabled by configuration; regression-tested (REQ-051). Scopes S3 |
| AI-4 | Inappropriate tool invocation | An agent invokes a destructive or out-of-scope action (chaos outside a drill, mutation via read-only scope) | Policy floor: high-risk never auto-executes (CTL-PRO-01), read_only can never mutate (CTL-PRO-02), chaos requires drill scope (CTL-PRO-04, SPI-enforced); manifests declare risk class and approvers (REQ-003, tested S0); every gateway decision audited (CTL-AUD-02) | manifests + SPI tested; gateway S6 |

## 4. Residual risks accepted this sprint

- Timing side-channel on login (argon2 verify only runs when the user exists); bounded
  by rate limiting. Revisit with a dummy-verify in S3 hardening if measurable.
- JWT secret and Fernet key fall back to generated var/keys/ files on the single dev
  machine when env vars are absent; production posture requires the env vars.
- In-process rate limiter resets on process restart (single-machine deployment).
- No email delivery for invites: links are copied out-of-band by the org owner, which
  keeps the invite token out of any mail pipeline.

## 5. Test hooks

The security suite (artifact 18) lives in signalforge/tests/security/: authz matrix and
router-coverage walk (REQ-012), repository and API tenant isolation (REQ-013), token
expiry/rotation/reuse/revocation (REQ-010), HMAC tamper and replay-window (REQ-017),
rate limiting (REQ-010), audit chain tamper detection (REQ-016), secret store
encryption-at-rest and scoping (REQ-015). make rtm joins them to this model's REQ ids.
