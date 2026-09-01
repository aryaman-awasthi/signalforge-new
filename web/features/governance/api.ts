/**
 * Governance and safe execution (REQ-060..REQ-065), through the cookie proxy
 * like every other feature.
 *
 * Five fields the API publishes are carried through verbatim rather than
 * flattened, because flattening any of them would let the screen say something
 * the backend did not:
 *
 * - `rules_hit` on a decision, each entry naming whether it came from the
 *   platform `floor` or from `app_policy`. The card renders the reason; it does
 *   not compose one.
 * - `locked` on a policy row. The server computes it from the domain
 *   classification, so a high-risk row is uneditable because the floor says so -
 *   never because a browser decided to grey a control.
 * - `retry_blocked` beside `state` on an execution. They are one fact with two
 *   names, and a Retry button next to an uncertain outcome would be an offer to
 *   apply a rollback twice.
 * - `rationale_degraded` on an action candidate. A deterministic sentence and a
 *   model's sentence are different evidence and wear different badges.
 * - `closure_lock_reason` on validation - a sentence, not a code, because it is
 *   read under a greyed-out Close button at three in the morning.
 */

import { proxyApi } from "@/lib/api/client";

// --- policy -------------------------------------------------------------------

export type RuleHit = {
  rule_id: string;
  /** `floor`, `app_policy` or `manifest`. */
  source: string;
  effect: string;
  detail: string;
};

export type Decision = {
  /** `allow`, `approval_required` or `block`. */
  outcome: string;
  action_id: string;
  action_class: string;
  /** The platform's classification, not the application's declaration. */
  risk_class: string;
  reason: string;
  rules_hit: RuleHit[];
  approver_roles: string[];
  approval_ttl_s: number;
  policy_version: number;
  policy_ref: string;
  floor_version: string;
  reversible: boolean;
  reverse_action_id: string | null;
};

export type ActionRow = {
  action_id: string;
  name: string;
  action_class: string;
  manifest_risk: string;
  risk_override: string | null;
  execution_mode: string;
  enabled: boolean;
  reversible: boolean;
  idempotent: boolean;
  decision: Decision;
  /** True for the six high-risk classes. The row is the safety floor. */
  locked: boolean;
  locked_reason: string;
};

export type PolicyVersion = {
  version: number;
  created_at: string;
  author_id: string;
  author_label: string;
  note: string;
  checksum: string;
  approval_ttl_s: number;
};

export type FloorRule = { rule_id: string; statement: string };

export type Policy = {
  app_id: string;
  version: number;
  source: string;
  rules: Record<string, unknown>;
  actions: ActionRow[];
  versions: PolicyVersion[];
  floor: {
    floor_version: string;
    max_approval_ttl_s: number;
    min_approval_ttl_s: number;
    approve_high_risk_roles: string[];
    rules: FloorRule[];
  };
  open_incidents: string[];
};

export type PolicyChange = {
  action_id: string;
  name: string;
  from: string;
  to: string;
  from_ttl_s: number;
  to_ttl_s: number;
  reason: string;
};

export type DryRun = {
  accepted: boolean;
  error: string;
  floor_rule: string;
  changes: PolicyChange[];
  before: ActionRow[];
  after: ActionRow[];
};

// --- next best action -----------------------------------------------------------

export type Candidate = {
  action_id: string;
  name: string;
  action_class: string;
  risk_class: string;
  /** urgency x impact x information_gain - risk. Server-side arithmetic. */
  score: number;
  urgency: number;
  impact: number;
  information_gain: number;
  risk: number;
  eligible: boolean;
  eligibility: string;
  eligibility_reason: string;
  policy: Decision;
  requires_approval: boolean;
  approver_roles: string[];
  hypothesis_key: string;
  hypothesis_label: string;
  hypothesis_confidence: number;
  evidence_refs: string[];
  prerequisites: string[];
  unmet_prerequisites: string[];
  expected_validation_s: number;
  rollback_action_id: string | null;
  rollback_summary: string;
  reversible: boolean;
  rationale: string;
  /** True when the sentence is the platform's, not a model's. */
  rationale_degraded: boolean;
};

export type PlanItem = {
  action_id: string;
  name: string;
  /** `kept`, `paused` or `added`. */
  disposition: string;
  score: number;
  reason: string;
  changed_assumption: string;
  evidence_refs: string[];
};

export type Plan = {
  plan_id: string;
  incident_id: string;
  version: number;
  previous_version: number | null;
  created_at: string;
  created_by: string;
  items: PlanItem[];
  kept: string[];
  paused: string[];
  added: string[];
  changed_assumptions: string[];
  evidence_refs: string[];
  note: string;
};

export type Nba = {
  primary: Candidate | null;
  alternatives: Candidate[];
  /** Actions that cannot be performed now, with the reason. Never primary. */
  ineligible: Candidate[];
  fallback: Candidate | null;
  degraded: boolean;
  generated_at: string | null;
  policy_version: number;
  plan: Plan | null;
  previous_plan: Plan | null;
};

// --- the execution ledger ---------------------------------------------------------

export type Approval = {
  approval_id: string;
  execution_id: string;
  action_id: string;
  state: string;
  requested_by: string;
  requested_at: string;
  approver_user_id: string;
  approver_label: string;
  approver_roles: string[];
  permission: string | null;
  required_roles: string[];
  issued_at: string | null;
  expires_at: string | null;
  seconds_left: number;
  policy_version: number;
  note: string;
};

export type Execution = {
  execution_id: string;
  incident_id: string;
  app_id: string;
  action_id: string;
  action_class: string;
  risk_class: string;
  state: string;
  attempt: number;
  idempotency_key: string;
  requested_by: string;
  requested_at: string;
  started_at: string | null;
  finished_at: string | null;
  args: Record<string, unknown>;
  decision: string;
  decision_reason: string;
  rules_hit: RuleHit[];
  policy_version: number;
  policy_ref: string;
  floor_version: string;
  approval_id: string | null;
  /** No client may offer retry while this is true. Reconcile first. */
  retry_blocked: boolean;
  reconcile_outcome: string | null;
  reconciled_at: string | null;
  error: string;
  error_category: string;
  supersedes_execution_id: string | null;
  compensates_execution_id: string | null;
  compensated_by_execution_id: string | null;
  takeover_by: string;
  takeover_note: string;
  result_ref: string;
  terminal: boolean;
};

export type GatewayResult = {
  execution: Execution;
  decision: Decision;
  approval: Approval | null;
  duplicate: boolean;
};

export type Ledger = { executions: Execution[]; approvals: Approval[] };

// --- communications ---------------------------------------------------------------

export type Redline = {
  old: string;
  new: string;
  reason: string;
  evidence_refs: string[];
};

export type Communication = {
  communication_id: string;
  incident_id: string;
  audience: string;
  /** draft, approved, issued, contradicted, corrected, withdrawn. */
  state: string;
  subject: string;
  body: string;
  version: number;
  created_at: string;
  created_by: string;
  grounded_node_ids: string[];
  model: string;
  degraded: boolean;
  requires_permission: string | null;
  approved_by: string;
  approved_by_label: string;
  approved_at: string | null;
  issued_by: string;
  issued_at: string | null;
  contradicted_at: string | null;
  contradiction_reason: string;
  contradicting_node_ids: string[];
  corrects_id: string | null;
  corrected_by_id: string | null;
  redline: Redline[];
};

export type Comms = {
  communications: Communication[];
  contradicted: string[];
  corrections: string[];
};

// --- layered validation --------------------------------------------------------------

export type LayerCheck = {
  check_id: string;
  label: string;
  /** pass, fail, unknown or not_applicable. */
  verdict: string;
  /** The system that was read. Never the Incident Brain. */
  source: string;
  observed: string;
  expected: string;
  result_ref: string;
  detail: string;
};

export type Layer = {
  layer: string;
  verdict: string;
  summary: string;
  checks: LayerCheck[];
  failing: string[];
};

export type StabilityWindow = {
  required_s: number;
  required_samples: number;
  consecutive_passes: number;
  started_at: string | null;
  elapsed_s: number;
  remaining_s: number;
  satisfied: boolean;
  /** Any regression resets the window rather than decrementing it. */
  regressions: number;
  last_regression_at: string | null;
  last_regression_reason: string;
};

export type Validation = {
  validation_id: string;
  incident_id: string;
  app_id: string;
  cycle: number;
  started_at: string;
  finished_at: string | null;
  requested_by: string;
  /** recovered, stabilising or not_recovered. */
  verdict: string;
  layers: Layer[];
  window: StabilityWindow | null;
  closure_blocked: boolean;
  closure_lock_reason: string;
  escalated: boolean;
  returned_to_active: boolean;
  evidence_node_ids: string[];
  note: string;
};

export type ValidationView = {
  latest: Validation | null;
  history: Validation[];
  failed_cycles: number;
  closure_blocked: boolean;
  closure_lock_reason: string;
};

// --- calls -------------------------------------------------------------------

const inc = (id: string) => encodeURIComponent(id);

export const getPolicy = (appId: string) =>
  proxyApi.get<Policy>(`/apps/${encodeURIComponent(appId)}/policy`);

export const savePolicy = (
  appId: string,
  source: string,
  options: { note?: string; confirmActiveIncident?: boolean } = {},
) =>
  proxyApi.post<Policy>(`/apps/${encodeURIComponent(appId)}/policy`, {
    source,
    note: options.note ?? "",
    confirm_active_incident: options.confirmActiveIncident ?? false,
  });

export const dryRunPolicy = (appId: string, source: string) =>
  proxyApi.post<DryRun>(`/apps/${encodeURIComponent(appId)}/policy/dry-run`, {
    source,
  });

export const getNextBestActions = (incidentId: string, explain = true) =>
  proxyApi.get<Nba>(
    `/incidents/${inc(incidentId)}/actions?explain=${explain ? "true" : "false"}`,
  );

export const revisePlan = (
  incidentId: string,
  changedAssumptions: string[],
  note = "",
) =>
  proxyApi.post<Nba>(`/incidents/${inc(incidentId)}/plan`, {
    changed_assumptions: changedAssumptions,
    note,
  });

export const getLedger = (incidentId: string) =>
  proxyApi.get<Ledger>(`/incidents/${inc(incidentId)}/executions`);

export const requestExecution = (
  incidentId: string,
  body: { action_id: string; args?: Record<string, unknown>; note?: string },
) =>
  proxyApi.post<GatewayResult>(`/incidents/${inc(incidentId)}/executions`, {
    args: {},
    note: "",
    ...body,
  });

const executionAction = (
  incidentId: string,
  executionId: string,
  what: string,
  body: Record<string, unknown> = {},
) =>
  proxyApi.post<GatewayResult>(
    `/incidents/${inc(incidentId)}/executions/${inc(executionId)}/${what}`,
    body,
  );

export const approveExecution = (
  incidentId: string,
  executionId: string,
  note: string,
) => executionAction(incidentId, executionId, "approve", { note });

export const denyExecution = (
  incidentId: string,
  executionId: string,
  note: string,
) => executionAction(incidentId, executionId, "deny", { note });

export const runExecution = (incidentId: string, executionId: string) =>
  executionAction(incidentId, executionId, "run");

export const reconcileExecution = (incidentId: string, executionId: string) =>
  executionAction(incidentId, executionId, "reconcile");

export const retryExecution = (incidentId: string, executionId: string) =>
  executionAction(incidentId, executionId, "retry");

export const compensateExecution = (
  incidentId: string,
  executionId: string,
  note: string,
) => executionAction(incidentId, executionId, "compensate", { note });

export const cancelExecution = (
  incidentId: string,
  executionId: string,
  note: string,
) => executionAction(incidentId, executionId, "cancel", { note });

export const takeOverExecution = (
  incidentId: string,
  executionId: string,
  note: string,
) => executionAction(incidentId, executionId, "takeover", { note });

export const getCommunications = (incidentId: string) =>
  proxyApi.get<Comms>(`/incidents/${inc(incidentId)}/communications`);

export const draftCommunication = (
  incidentId: string,
  body: {
    audience: string;
    subject?: string;
    body?: string;
    node_ids?: string[];
    use_model?: boolean;
  },
) =>
  proxyApi.post<Communication>(`/incidents/${inc(incidentId)}/communications`, body);

const commsAction = (
  incidentId: string,
  communicationId: string,
  what: string,
  body: Record<string, unknown> = {},
) =>
  proxyApi.post<Communication>(
    `/incidents/${inc(incidentId)}/communications/${inc(communicationId)}/${what}`,
    body,
  );

export const approveCommunication = (
  incidentId: string,
  communicationId: string,
  note = "",
) => commsAction(incidentId, communicationId, "approve", { note });

export const issueCommunication = (
  incidentId: string,
  communicationId: string,
  note = "",
) => commsAction(incidentId, communicationId, "issue", { note });

export const draftCorrection = (
  incidentId: string,
  communicationId: string,
  body: { subject?: string; body?: string; reason?: string; use_model?: boolean },
) => commsAction(incidentId, communicationId, "correction", body);

export const getValidation = (incidentId: string) =>
  proxyApi.get<ValidationView>(`/incidents/${inc(incidentId)}/validation`);

export const runValidation = (incidentId: string, note = "") =>
  proxyApi.post<Validation>(`/incidents/${inc(incidentId)}/validation`, { note });
