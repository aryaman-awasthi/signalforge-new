/**
 * Applications and the onboarding wizard (REQ-036), through the cookie proxy
 * like every other feature. The types track the API's response models field for
 * field: the wizard renders what the platform stores, never a view model
 * invented for a screen, so a step that saves something the domain refuses gets
 * the refusal back rather than drifting quietly out of sync.
 *
 * No credential value has a type in this module. A connection test says whether
 * a credential worked; it never says what it was.
 */

import { proxyApi } from "@/lib/api/client";

export type AppTier = "tier_0" | "tier_1" | "tier_2" | "tier_3";
export type Environment = "production" | "staging" | "development";

export type Application = {
  app_id: string;
  name: string;
  tier: AppTier;
  environment: Environment;
  base_url: string | null;
  description: string;
  owner_team: string;
  created_at: string | null;
  updated_at: string | null;
};

export type ApplicationSummary = Application & {
  readiness_score: number;
  ready: boolean;
};

export type GoldenSignal =
  | "availability"
  | "errors"
  | "latency"
  | "saturation"
  | "traffic"
  | "business";

export type Signal = {
  key: string;
  signal: GoldenSignal;
  selector: string;
  direction: "higher_is_worse" | "lower_is_worse";
  unit: string;
  description: string;
  business_counter: boolean;
};

export type Journey = {
  journey_id: string;
  name: string;
  tier: number;
  description: string;
};

export type Probe = {
  probe_id: string;
  path: string;
  method: string;
  expected_status: number;
  timeout_s: number;
  journey_id: string | null;
  per_region: boolean;
  synthetic_header: string;
};

export type Action = {
  action_id: string;
  name: string;
  action_class: string;
  manifest_risk: string;
  risk_override: string | null;
  approver_roles: string[];
  execution_mode: "manual" | "approval" | "auto";
  reversible: boolean;
  reverse_action_id: string | null;
  idempotent: boolean;
  enabled: boolean;
  description: string;
  imported_at: string | null;
  high_risk: boolean;
  effective_risk: string;
};

export type Slo = {
  slo_id: string;
  name: string;
  objective_pct: number;
  window_days: number;
  journey_id: string | null;
  indicator: string;
  fast_burn_multiplier: number;
  slow_burn_multiplier: number;
  error_budget_pct: number;
};

export type SeverityRule = {
  severity: string;
  max_journey_tier: number | null;
  min_regions_affected: number | null;
  success_rate_below_pct: number | null;
  customer_impacting: boolean | null;
  description: string;
};

export type Policy = {
  approval_ttl_s: number;
  stability_window_s: number;
  max_action_retries: number;
  auto_execute_low_risk: boolean;
  require_approval_classes: string[];
  confidence_threshold: number;
  revalidation_cycles_before_escalation: number;
};

export type Baseline = {
  signal_key: string;
  samples: number;
  window_s: number;
  mean: number;
  stddev: number;
  minimum: number;
  maximum: number;
  p95: number;
  series_count: number;
  learned_at: string | null;
  source: string;
};

export type TopologyRef = {
  version: string;
  captured_at: string | null;
  node_count: number;
  edge_count: number;
  imported_at: string | null;
  normalized_by: string;
  source: string;
};

export type TestSummary = {
  ran_at: string | null;
  passed: number;
  failed: number;
  pending: number;
  skipped: number;
};

export type Profile = {
  signals: Signal[];
  region_label: string | null;
  regions: string[];
  journeys: Journey[];
  probes: Probe[];
  actions: Action[];
  slos: Slo[];
  severity_matrix: { rules: SeverityRule[]; default: string };
  policy: Policy;
  baselines: Baseline[];
  topology: TopologyRef | null;
  last_test: TestSummary | null;
  can_corroborate: boolean;
  updated_at: string | null;
};

export type ReadinessCheck = {
  key: string;
  step: string;
  label: string;
  weight: number;
  passed: boolean;
  detail: string;
  blocking: boolean;
};

export type Readiness = {
  app_id: string;
  score: number;
  total_weight: number;
  threshold: number;
  ready: boolean;
  checks: ReadinessCheck[];
  outstanding: string[];
  /** step key -> [earned, available]; drives the stepper's per-step progress. */
  steps: Record<string, [number, number]>;
};

export type ApplicationDetail = {
  application: Application;
  profile: Profile;
  readiness: Readiness;
};

export type DiscoveryCandidate = {
  adapter_id: string;
  adapter_name: string;
  kind: string;
  mode: "pull" | "push" | "invoke";
  direction: string;
  availability: string;
  runnable: boolean;
  simulated: boolean;
  credential_scope: string;
  credential_fields: string[];
  suggested_config: Record<string, unknown>;
  action: string;
  already_bound: boolean;
  reason: string;
  stream: string | null;
  ingest_path: string | null;
};

export type DiscoveryFinding = {
  key: string;
  source: string;
  label: string;
  url: string;
  present: boolean;
  guarded: boolean;
  status_code: number | null;
  latency_ms: number;
  detail: string;
  candidates: DiscoveryCandidate[];
};

export type DiscoveryReport = {
  app_id: string;
  base_url: string | null;
  probed: number;
  found: number;
  findings: DiscoveryFinding[];
};

export type CheckOutcome = "passed" | "failed" | "pending" | "skipped";

export type ConnectionTest = {
  binding_id: string;
  adapter_id: string;
  adapter_name: string;
  kind: string | null;
  mode: string | null;
  direction: string | null;
  credential_scope: string;
  simulated: boolean;
  outcome: CheckOutcome;
  detail: string;
  latency_ms: number;
  checks: { name: string; outcome: CheckOutcome; detail: string; latency_ms: number }[];
};

export type ActionsImport = {
  app_id: string;
  adapter_id: string;
  binding_id: string;
  imported: number;
  refreshed: number;
  withdrawn: number;
  actions: Action[];
};

export type TopologyUpload = {
  app_id: string;
  version: string;
  /** True when the document is byte-for-byte the map already held: one version,
   * no event, nothing changed. The screen has to say so or an operator retries. */
  unchanged: boolean;
  node_count: number;
  edge_count: number;
  normalized_by: string;
  imported_at: string | null;
};

export type TopologyVersion = {
  version: string;
  node_count: number;
  edge_count: number;
  imported_at: string | null;
  source: string;
  normalized_by: string;
};

export type TopologyView = {
  app_id: string;
  version: string | null;
  snapshot: unknown | null;
  versions: TopologyVersion[];
};

export type BaselineRun = {
  app_id: string;
  binding_id: string;
  samples: number;
  window_s: number;
  learned: Baseline[];
  /** Partial completion is a first-class result, not an error: the signals a
   * run could not measure come back named, with the reason. */
  missed: { signal_key: string; reason: string }[];
};

export type ProbeRun = {
  probe_id: string;
  journey_id: string | null;
  target: string;
  outcome: CheckOutcome;
  status_code: number | null;
  latency_ms: number;
  detail: string;
};

export type TestPage = {
  app_id: string;
  summary: TestSummary;
  connectors: ConnectionTest[];
  probes: ProbeRun[];
  readiness: Readiness;
};

// --- calls -------------------------------------------------------------------

export const listApps = () => proxyApi.get<ApplicationSummary[]>("/apps");

export const getApp = (appId: string) =>
  proxyApi.get<ApplicationDetail>(`/apps/${encodeURIComponent(appId)}`);

export type RegisterAppBody = {
  app_id: string;
  name: string;
  tier: AppTier;
  environment: Environment;
  base_url?: string;
  description?: string;
  owner_team?: string;
};

export const registerApp = (body: RegisterAppBody) =>
  proxyApi.post<ApplicationDetail>("/apps", body);

export const patchApp = (appId: string, body: Record<string, unknown>) =>
  proxyApi.patch<ApplicationDetail>(`/apps/${encodeURIComponent(appId)}`, body);

export const removeApp = (appId: string) =>
  proxyApi.del<void>(`/apps/${encodeURIComponent(appId)}`);

export const discover = (appId: string) =>
  proxyApi.post<DiscoveryReport>(`/apps/${encodeURIComponent(appId)}/discover`, {});

export const runConnectionTests = (appId: string, bindingId?: string) =>
  proxyApi.post<ConnectionTest[]>(
    `/apps/${encodeURIComponent(appId)}/connection-tests`,
    bindingId ? { binding_id: bindingId } : {},
  );

export const putMapping = (appId: string, body: Record<string, unknown>) =>
  proxyApi.put<Profile>(`/apps/${encodeURIComponent(appId)}/mapping`, body);

export const importActions = (appId: string) =>
  proxyApi.post<ActionsImport>(`/apps/${encodeURIComponent(appId)}/actions/import`, {});

export const patchAction = (
  appId: string,
  actionId: string,
  body: Record<string, unknown>,
) =>
  proxyApi.patch<Profile>(
    `/apps/${encodeURIComponent(appId)}/actions/${encodeURIComponent(actionId)}`,
    body,
  );

export const putTopology = (appId: string, document: unknown) =>
  proxyApi.put<TopologyUpload>(`/apps/${encodeURIComponent(appId)}/topology`, {
    document,
  });

export const getTopology = (appId: string) =>
  proxyApi.get<TopologyView>(`/apps/${encodeURIComponent(appId)}/topology`);

export const putGovernance = (appId: string, body: Record<string, unknown>) =>
  proxyApi.put<Profile>(`/apps/${encodeURIComponent(appId)}/governance`, body);

export const learnBaseline = (appId: string, samples: number) =>
  proxyApi.post<BaselineRun>(`/apps/${encodeURIComponent(appId)}/baseline`, {
    samples,
  });

export const runTestPage = (appId: string) =>
  proxyApi.post<TestPage>(`/apps/${encodeURIComponent(appId)}/test`, {});

export const getReadiness = (appId: string) =>
  proxyApi.get<Readiness>(`/apps/${encodeURIComponent(appId)}/readiness`);
