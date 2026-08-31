# 13 - Controls matrix v1 (Sprint 1)

Owner: engineering. Status: matrix v1 authored S1-T05; the machine-readable source of
truth is specs/controls.yaml (never redefined here). Policy-engine enforcement of the
full matrix lands S6; this document records, per control, where its enforcement point
lives (or will live) and what already tests it. Entries marked FLOOR are the hardcoded
policy floor: per-app policy can tighten them, never loosen them (CLAUDE.md rule 4).

## Allowed (autonomous)

| Control | Enforcement point | Status |
|---|---|---|
| CTL-ALW-01 read-only telemetry within budgets | adapter credential scopes (S3) + query budgets (S5) | planned |
| CTL-ALW-02 detection/correlation/scoring | detection services (S4/S5); no external mutation possible from these code paths | planned |
| CTL-ALW-03 drafting, never issuing | comms lifecycle keeps drafts until approver issues (S6); CTL-PRO-05 backs it | planned |
| CTL-ALW-04 low-risk idempotent diagnostics | action manifests declare risk + idempotency (REQ-003, schema-tested S0); gateway enforces S6 | manifests tested |
| CTL-ALW-05 paging per escalation policy | escalation policies with bounded, validated timers (REQ-014, tested S1); live paging S4 | timers tested |

## Human approved

| Control | Enforcement point | Status |
|---|---|---|
| CTL-HUM-01 FLOOR high-risk classes need named approvers | manifest schema rejects high-risk without approver roles (REQ-003, tested S0); gateway approval flow S6 | schema tested |
| CTL-HUM-02 remediation above read_only | credential scopes on bindings (S3); gateway checks (S6) | planned |
| CTL-HUM-03 issuing comms per audience approvers | approve_exec_comms / approve_customer_comms exist in the frozen matrix and RBAC service (REQ-012, tested S1); comms flow S6 | permissions live |
| CTL-HUM-04 FLOOR lifecycle transitions are human | declare/change_severity/close permissions in the matrix, held only by human roles - agents hold no roles at all (REQ-005, tested S0) | permissions live |
| CTL-HUM-05 policy edits during active incident confirm | edit_policies permission live; confirmation step S6 | planned |

## Prohibited (FLOOR unless noted)

| Control | Enforcement point | Status |
|---|---|---|
| CTL-PRO-01 no auto-execution of high-risk | gateway S6; manifests already refuse high-risk without approvers (REQ-003) | partial |
| CTL-PRO-02 no mutation via read_only scope | credential scopes S3, gateway S6 | planned |
| CTL-PRO-03 no disabling/truncating/rewriting audit | AuditChainWriter has no disable path (asserted by test); hash chain detects rewrites (REQ-016, tested S1) | **enforced** |
| CTL-PRO-04 no chaos outside drill scope | chaos SPI requires drill scope at the interface (REQ-002, tested S0); drill engine S7 | interface enforced |
| CTL-PRO-05 no auto-sending communications | comms lifecycle S6; nothing in S1 can send | n/a yet |
| CTL-PRO-06 no cross-tenant access | tenant_id predicate inside every repository method (REQ-013, tested S1 at repo and API level) | **enforced** |

## Escalation

| Control | Enforcement point | Status |
|---|---|---|
| CTL-ESC-01 unacked page escalates to next-in-rotation | rotation.next_in_rotation + cumulative policy timers (REQ-014, tested S1); live countdowns S4 | model tested |
| CTL-ESC-02 low confidence/contradiction surfaces to IC | agent suite S5 | planned |
| CTL-ESC-03 validation failure escalates after N cycles | closure gate S6 | planned |
| CTL-ESC-04 LLM failure degrades deterministically | llm.provider adapter S5 (REQ-051) | planned |
| CTL-ESC-05 UNCERTAIN blocks retry until reconciled | SPI reconcile-before-retry primitive frozen (REQ-002, tested S0); execution engine S6 | interface frozen |

## Auditability

| Control | Enforcement point | Status |
|---|---|---|
| CTL-AUD-01 FLOOR every event hash-chained | audit writer chains per tenant (REQ-016, tested S1); event-store sealing S2 | **enforced** for audit events |
| CTL-AUD-02 gateway decisions recorded | authz denials already audited with permission + reason (REQ-012, tested S1); full gateway ledger S6 | partial |
| CTL-AUD-03 approvals carry identity + expiry | gateway S6 | planned |
| CTL-AUD-04 agent runs traced | agent trace tables S5 | planned |
| CTL-AUD-05 audit access permissioned + logged | read_audit permission live in matrix (REQ-005); audit-read API lands S2 | permission live |
