/**
 * Detection and the incident lifecycle (REQ-040..REQ-046), through the cookie
 * proxy like every other feature.
 *
 * Two published constants are typed as literals rather than as `boolean`, and
 * that is the point of the file. `requires_human` on a severity proposal is
 * always `true` and `declared` is always `false`: detection reaches a proposal
 * and stops, and a cockpit that could render a proposal as a declaration would
 * be describing a product this deliberately is not. Typing them as literals
 * means a screen that tries to treat a proposal as an incident does not compile.
 *
 * The types track the API's response models field for field. The cockpit renders
 * what the platform computed - the health score, the corroboration verdict, the
 * severity the matrix proposed - and never recomputes any of it, because a second
 * implementation of the weighting would eventually disagree with the first
 * during an incident.
 */

import { proxyApi } from "@/lib/api/client";

export type Severity = "P0" | "P1" | "P2" | "P3";

/** The three stances detection owns, then the three states an incident has. */
export type LifecycleState =
  | "steady"
  | "watch"
  | "proposed"
  | "active"
  | "recovering"
  | "closed";

export type EvidenceRef = {
  kind: string;
  ref: string;
  detail: string;
  family: string | null;
  region: string;
  customer_facing: boolean;
  at: string | null;
};

export type SignalState = {
  signal_key: string;
  region: string;
  family: string;
  status: string;
  streak: number;
  basis: string;
  value: number | null;
  threshold: number | null;
  baseline_mean: number | null;
  baseline_stddev: number | null;
  baseline_source: string;
  baseline_samples: number;
  deviation: number;
  first_breach_at: string | null;
  anomalous_since: string | null;
  last_value_at: string | null;
  evaluated_at: string | null;
  detail: string;
};

export type FamilyScore = {
  family: string;
  weight: number;
  fraction: number | null;
  signals: string[];
  detail: string;
};

export type RegionHealth = {
  region: string;
  score: number | null;
  band: string;
  covered_weight: number;
  families: FamilyScore[];
};

export type HealthScore = {
  score: number | null;
  band: string;
  worst_region: string | null;
  covered_weight: number;
  regions: RegionHealth[];
  detail: string;
};

export type BurnWindow = {
  window: string;
  window_s: number;
  multiplier: number;
  samples: number;
  error_ratio: number | null;
  budget_ratio: number;
  burn_rate: number | null;
  breached: boolean;
  region: string;
  detail: string;
};

export type SloBurn = {
  slo_id: string;
  name: string;
  indicator: string;
  journey_id: string | null;
  objective_pct: number;
  verdict: string;
  fast: BurnWindow;
  slow: BurnWindow;
  detail: string;
};

export type JourneyHealth = {
  journey_id: string | null;
  region: string;
  tier: number | null;
  passed: number;
  failed: number;
  /** Probe runs that could not be performed. Rendered separately from failures
   * everywhere, because "we could not check" and "the journey is broken" lead a
   * responder to opposite next actions. */
  unknown: number;
  success_rate_pct: number | null;
  last_at: string | null;
  last_detail: string;
};

export type Corroboration = {
  corroborated: boolean;
  families: string[];
  customer_facing_families: string[];
  regions: string[];
  reason: string;
  evidence: EvidenceRef[];
};

export type Impact = {
  journey_tier: number | null;
  regions_affected: number;
  regions_total: number;
  success_rate_pct: number | null;
  customer_impacting: boolean;
  affected_regions: string[];
  affected_journeys: string[];
  detail: string;
};

export type SeverityProposal = {
  severity: Severity;
  rule: string;
  corroborated: boolean;
  proposed_at: string;
  impact: Impact;
  evidence: EvidenceRef[];
  detail: string;
  /** Literal `true`. Only a person declares an incident (REQ-043). */
  requires_human: true;
  /** Literal `false`. Detection proposes; it never declares. */
  declared: false;
};

export type AlertCluster = {
  cluster_id: string;
  root_alert_id: string;
  root_node_id: string | null;
  alert_ids: string[];
  node_ids: string[];
  names: string[];
  severity: string;
  size: number;
  suppressed_count: number;
  started_at: string;
  latest_at: string;
  detail: string;
};

export type Storm = {
  alerts: number;
  clusters: AlertCluster[];
  suppressed: number;
  window_s: number;
  storming: boolean;
};

export type Detection = {
  app_id: string;
  stance: string;
  evaluated_at: string;
  reasons: string[];
  signals: SignalState[];
  health: HealthScore;
  burns: SloBurn[];
  journeys: JourneyHealth[];
  corroboration: Corroboration;
  severity: SeverityProposal | null;
  storm: Storm;
};

// --- the change window --------------------------------------------------------

export type ChangeWindow = {
  change_id: string;
  change_type: string;
  service: string;
  status: string;
  started_at: string;
  completed_at: string | null;
  version: string | null;
  actor: string | null;
  diff_ref: string | null;
  source_system: string;
  simulated: boolean;
  announced_started: boolean;
  announced_completed: boolean;
  open: boolean;
  /** Open for longer than `max_age_s`. The chip goes out, because a chip that
   * stays lit for three days is a chip people stop seeing - but the row stays
   * listed, saying how long it has been open. It is never closed on a guess:
   * nobody knows whether that deploy landed. */
  stale: boolean;
  age_s: number;
  duration_s: number | null;
  detail: string;
};

export type ChangeWindowView = {
  app_id: string;
  at: string;
  /** **This is the chip.** A deployment announced its start and never announced
   * its completion. Read from the API rather than derived from a count, because
   * the platform - not the browser - decides when an unfinished change has been
   * open long enough to stop being news. */
  open: boolean;
  max_age_s: number;
  /** In flight now. */
  windows: ChangeWindow[];
  /** Open past the ceiling: the pipeline stopped talking. Listed, not hidden. */
  stale: ChangeWindow[];
  /** Finished, with which of the three endings it was. */
  recent: ChangeWindow[];
  detail: string;
};

// --- the lifecycle ------------------------------------------------------------

export type DeclaredProposal = {
  stance: string;
  severity: Severity | null;
  rule: string;
  corroborated: boolean;
  families: string[];
  health_score: number | null;
  health_band: string;
  regions_affected: number;
  regions_total: number;
  success_rate_pct: number | null;
  journey_tier: number | null;
  customer_impacting: boolean;
  reasons: string[];
  evidence: EvidenceRef[];
  detail: string;
  /** Empty when the detector answered. Anything else is why it could not, and a
   * person was still allowed to declare - the moment the detector is broken is
   * exactly the moment somebody most needs to. */
  unavailable: string;
};

export type TicketSync = {
  state: "not_configured" | "pending" | "synced" | "failed" | "uncertain";
  ticket_id: string | null;
  reference: string | null;
  status: string | null;
  adapter_id: string | null;
  attempts: number;
  detail: string;
  last_attempt_at: string | null;
  synced_at: string | null;
};

export type ChangeWindowLink = {
  change_id: string;
  change_type: string;
  service: string;
  version: string | null;
  started_at: string | null;
  actor: string | null;
};

export type Incident = {
  id: string;
  reference: string;
  app_id: string;
  title: string;
  summary: string;
  severity: Severity;
  severity_source: "proposed" | "chosen";
  state: LifecycleState;
  stance_at_declare: string;
  declared_at: string;
  declared_by: string;
  declared_by_display: string;
  updated_at: string;
  recovering_at: string | null;
  closed_at: string | null;
  closed_by: string | null;
  closure_reason: string;
  age_s: number;
  open: boolean;
  overrode_detection: boolean;
  /** Literal `true` on every incident. There is no code path that produces one
   * any other way, and a UI showing "declared by SignalForge" would be
   * describing a product this is deliberately not. */
  declared_by_human: true;
  ticket: TicketSync;
  change_windows: ChangeWindowLink[];
  proposal: DeclaredProposal | null;
};

export type IncidentTransition = {
  from_state: LifecycleState;
  to_state: LifecycleState;
  at: string;
  actor_type: string;
  actor_id: string;
  reason: string;
  severity_before: Severity | null;
  severity_after: Severity | null;
  kind: string;
};

export type IncidentDetail = Incident & {
  transitions: IncidentTransition[];
};

export type IncidentList = {
  count: number;
  incidents: Incident[];
};

export type Lifecycle = {
  app_id: string;
  state: LifecycleState;
  /** `incident` when a person decided this, `detection` when it is a stance the
   * detector recomputed seconds ago. The same six words name both, and the
   * screen has to say which it is reading. */
  source: "incident" | "detection";
  at: string;
  stance: string;
  severity: Severity | null;
  incident: Incident | null;
  change_window: ChangeWindowView | null;
  detection_error: string;
  health_score: number | null;
  health_band: string;
  corroborated: boolean;
  proposed_severity: Severity | null;
  reasons: string[];
  /** Every move the lifecycle allows from here, whoever makes it. */
  next_states: string[];
  /** The subset a *person* may make. Published separately so a client cannot
   * render a machine transition as a button. */
  human_moves: string[];
  declare_requires_human: true;
};

// --- calls -------------------------------------------------------------------

const app = (appId: string) => encodeURIComponent(appId);

export const getDetection = (appId: string) =>
  proxyApi.get<Detection>(`/apps/${app(appId)}/detection`);

export const evaluateNow = (appId: string) =>
  proxyApi.post<Detection>(`/apps/${app(appId)}/detection/evaluate`, {});

export const getLifecycle = (appId: string) =>
  proxyApi.get<Lifecycle>(`/apps/${app(appId)}/lifecycle`);

export const getChangeWindow = (appId: string) =>
  proxyApi.get<ChangeWindowView>(`/apps/${app(appId)}/change-window`);

export const listIncidents = (params?: { app_id?: string; state?: string }) => {
  const query = new URLSearchParams();
  if (params?.app_id) query.set("app_id", params.app_id);
  if (params?.state) query.set("state", params.state);
  const suffix = query.toString();
  return proxyApi.get<IncidentList>(`/incidents${suffix ? `?${suffix}` : ""}`);
};

export const getIncident = (incidentId: string) =>
  proxyApi.get<IncidentDetail>(`/incidents/${encodeURIComponent(incidentId)}`);

export type DeclareBody = {
  title?: string;
  summary?: string;
  /** Left out, the severity the matrix proposed is accepted - still a human act,
   * because a person pressed Declare. Supplied, it is recorded as `chosen`. */
  severity?: Severity;
  reason?: string;
};

export const declareIncident = (appId: string, body: DeclareBody) =>
  proxyApi.post<IncidentDetail>(`/incidents?app_id=${app(appId)}`, body);

export const changeSeverity = (
  incidentId: string,
  severity: Severity,
  reason?: string,
) =>
  proxyApi.patch<Incident>(
    `/incidents/${encodeURIComponent(incidentId)}/severity`,
    { severity, reason: reason ?? "" },
  );

export const setIncidentState = (
  incidentId: string,
  state: "active" | "recovering",
  reason?: string,
) =>
  proxyApi.post<Incident>(`/incidents/${encodeURIComponent(incidentId)}/state`, {
    state,
    reason: reason ?? "",
  });

export const closeIncident = (incidentId: string, reason?: string) =>
  proxyApi.post<Incident>(`/incidents/${encodeURIComponent(incidentId)}/close`, {
    reason: reason ?? "",
  });
