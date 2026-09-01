# 13 - Controls matrix v2 (Sprint 6: enforced)

Owner: engineering. Status: matrix v1 authored S1-T05; **enforcement completed S6-T01..T05**.
The machine-readable source of truth is specs/controls.yaml (never redefined here); this
document records, per control, where its enforcement point lives and what tests it.
Entries marked FLOOR are the hardcoded policy floor: per-app policy can tighten them,
never loosen them (CLAUDE.md rule 4).

## Where the floor actually lives (S6)

Every FLOOR entry below resolves to one of these, and none of them is configuration:

| Where | What it refuses |
|---|---|
| `domain/governance/policy.py` `PolicyDocument.__post_init__` + `assert_against_actions` | a policy document that would let a high-risk class auto-execute, lower a declared risk, name an ineligible approver, exceed the TTL cap, or name an audit setting |
| `domain/governance/policy.py` `PolicyEngine.decide` | an execution decision that is not ALLOW / APPROVAL_REQUIRED / BLOCK with its `rules_hit` |
| `domain/governance/ledger.py` `ExecutionRecord` + `TRANSITIONS` | a move from `uncertain` to anything but `reconciling`; `retry_blocked` is set at construction |
| `domain/governance/approvals.py` `assert_valid_now` | an expired, spent, revoked, re-fingerprinted or de-permissioned approval |
| `domain/governance/validation.py` `LayerCheck.__post_init__` + `assert_closable` | a validation check sourced from the Incident Brain; a closure while a gating layer fails |
| `domain/governance/comms.py` `TRANSITIONS` | any move to ISSUED that is not a person's; any edit of issued text |

## Allowed (autonomous)

| Control | Enforcement point | Status |
|---|---|---|
| CTL-ALW-01 read-only telemetry within budgets | adapter credential scopes (S3) + `QueryBudget` per hypothesis and per pass, plus the per-incident token ledger, both refusing before the work starts (REQ-054, tested S5) | **enforced** |
| CTL-ALW-02 detection/correlation/scoring | detection services (S4) + the agent roster (S5); no agent holds an invoker, a repository or an HTTP client, asserted by reading their source (REQ-052, tested S5) | **enforced** |
| CTL-ALW-03 drafting, never issuing | `CommunicationService` has no send, no scheduler and no auto-issue transition; `TRANSITIONS` admits ISSUED only from APPROVED, which only `issue()` performs with a person's permission (REQ-064, tested S6) | **enforced** |
| CTL-ALW-04 low-risk idempotent diagnostics | action manifests declare risk + idempotency (REQ-003, tested S0); `classify()` reads DIAGNOSTIC + low as `read_only` and the gateway runs it on request (REQ-060, tested S6) | **enforced** |
| CTL-ALW-05 paging per escalation policy | escalation policies with bounded, validated timers (REQ-014, tested S1); live paging (REQ-044, tested S4) | **enforced** |

## Human approved

| Control | Enforcement point | Status |
|---|---|---|
| CTL-HUM-01 FLOOR high-risk classes need named approvers | manifest schema rejects high-risk without approver roles (REQ-003, tested S0); `_approvers_for` intersects every candidate list with the roles that hold `approve_high_risk`, and a high-risk action with none left is BLOCKED (REQ-060, tested S6) | **enforced** |
| CTL-HUM-02 remediation above read_only | credential scopes on bindings (S3), re-checked by `assert_can_invoke` on every call; the Tool Gateway is the only caller of the invoker for an action (REQ-061, tested S6) | **enforced** |
| CTL-HUM-03 issuing comms per audience approvers | `AUDIENCE_PERMISSION` maps each audience to a permission from the frozen matrix, checked in `CommunicationService.approve` where the audience is known - not on the route, which has not read it yet (REQ-064, tested S6) | **enforced** |
| CTL-HUM-04 FLOOR lifecycle transitions are human | declare/change_severity/close permissions in the matrix, held only by human roles - agents hold no roles at all (REQ-005, tested S0); `HUMAN_ONLY` in the lifecycle (REQ-043, tested S4) | **enforced** |
| CTL-HUM-05 policy edits during active incident confirm | `PolicyService.edit` raises `ActiveIncidentConfirmationRequired` unless confirmed, audits the confirmed edit as `policy.edit.mid_incident`, and the new version invalidates outstanding approvals via `Approval.assert_valid_now` (REQ-060, tested S6) | **enforced** |

## Prohibited (FLOOR unless noted)

| Control | Enforcement point | Status |
|---|---|---|
| CTL-PRO-01 no auto-execution of high-risk | `classify()` returns HIGH_RISK for the six classes whatever was declared or overridden; `PolicyEngine.decide` cannot return ALLOW for one, and `intent=AUTO` returns a recorded BLOCK naming `FLOOR-AUTO-EXECUTE-REFUSED`. A policy document setting `auto_execute: true` on one cannot be constructed, and the attempt is audited as `policy.floor.rejected` (REQ-060, REQ-061, tested S6) | **enforced** |
| CTL-PRO-02 no mutation via read_only scope | `assert_can_invoke` at call time (S3) plus `FLOOR-READ-ONLY-NO-MUTATE`: only DIAGNOSTIC may classify read-only, and `RiskClass.READ_ONLY.mutating` is False (REQ-060, tested S6) | **enforced** |
| CTL-PRO-03 no disabling/truncating/rewriting audit | AuditChainWriter has no disable path (asserted by test); hash chain detects rewrites (REQ-016, tested S1); `FORBIDDEN_POLICY_KEYS` refuses a document that so much as names an audit setting rather than ignoring it (REQ-060, tested S6) | **enforced** |
| CTL-PRO-04 no chaos outside drill scope | chaos SPI requires drill scope at the interface (REQ-002, tested S0); drill engine S7 | interface enforced |
| CTL-PRO-05 no auto-sending communications | as CTL-ALW-03; additionally the cockpit has no send affordance and the API surface has no send/transmit/broadcast operation, asserted by a test that walks the published OpenAPI (REQ-064, tested S6) | **enforced** |
| CTL-PRO-06 no cross-tenant access | tenant_id predicate inside every repository method (REQ-013, tested S1 at repo and API level); the S6 stores follow it and the boundary suite walks the governance surface (tested S6) | **enforced** |

## Escalation

| Control | Enforcement point | Status |
|---|---|---|
| CTL-ESC-01 unacked page escalates to next-in-rotation | rotation.next_in_rotation + cumulative policy timers (REQ-014, tested S1); live countdowns (REQ-044, tested S4) | **enforced** |
| CTL-ESC-02 low confidence/contradiction surfaces to IC | contradictions become a `RISK` node, a `brain.contradiction.detected` event and a row on S14; every hypothesis carries its computed confidence and the test that would move it (REQ-052, REQ-053, tested S5) | **enforced** |
| CTL-ESC-03 validation failure escalates after N cycles | `ValidationService._handle_failure` counts failed cycles and emits `validation.escalated` at the app policy's `revalidation_cycles_before_escalation`; the incident returns to Active and the failure becomes graph evidence (REQ-065, tested S6) | **enforced** |
| CTL-ESC-04 LLM failure degrades deterministically | `LlmProvider` never raises for a model failure; every call site branches on the result. The S6 call sites - NBA rationale, communication draft, correction - each write the deterministic text first and keep it on any failure (REQ-051, REQ-055, REQ-063, REQ-064, tested S5 and S6) | **enforced** |
| CTL-ESC-05 UNCERTAIN blocks retry until reconciled | `ExecutionRecord.__post_init__` sets `retry_blocked` on UNCERTAIN; `TRANSITIONS` has no `uncertain -> running`; `assert_retryable` raises inside `retry_of` so a caller who skipped the check cannot get past it; the refusal is audited as `execution.retry.blocked` (REQ-062, tested S6) | **enforced** |

## Auditability

| Control | Enforcement point | Status |
|---|---|---|
| CTL-AUD-01 FLOOR every event hash-chained | audit writer chains per tenant (REQ-016, tested S1); event-store sealing S2 | **enforced** for audit events |
| CTL-AUD-02 gateway decisions recorded | authz denials audited with permission + reason (REQ-012, tested S1); every gateway transition - requested, decided, blocked, approval requested/granted/denied/revoked/rejected, started, succeeded, failed, uncertain, reconciled, duplicate prevented, retry blocked, compensating, cancelled, takeover - is an audit entry and an incident-plane event carrying `rules_hit`, the policy version and the floor version (REQ-061, tested S6) | **enforced** |
| CTL-AUD-03 approvals carry identity + expiry | `ApprovalRow` stores approver, roles, permission, issued_at, expires_at, policy version and the decision fingerprint; all of it is re-checked at execution (REQ-061, tested S6) | **enforced** |
| CTL-AUD-04 agent runs traced | `agent_runs` + `agent_run_steps` written by the governor for every agent: which agent, on what, which model, tokens, degraded or failed - and no prompt or answer, because content lives on the graph nodes with their own provenance (REQ-052, tested S5) | **enforced** |
| CTL-AUD-05 audit access permissioned + logged | read_audit permission live in matrix (REQ-005); policy version history carries `read_audit` rather than `edit_policies`, because an audit trail only the editor can see is not one (REQ-060, tested S6); audit-read API S7 | **enforced** for policy history |

## What Sprint 6 does *not* claim

- **The drill engine's chaos scope (CTL-PRO-04)** is still enforced only at the SPI interface.
  The drill engine that would exercise it lands S7.
- **A general audit-read API (CTL-AUD-05)** does not exist yet; the chain is written and
  verifiable, and the policy-history route is the first permissioned read of it.
- **Compensation is best-effort by design.** It runs the application's own declared reverse
  action through the same policy decision and approval gate. An action that declares no
  reverse action cannot be compensated by the platform, and the ledger says so and offers
  human takeover instead of inventing an undo.
