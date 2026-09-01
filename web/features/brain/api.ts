/**
 * The Incident Brain (REQ-050, REQ-053, REQ-055), through the cookie proxy like
 * every other feature.
 *
 * Four distinctions the API publishes are carried through verbatim rather than
 * flattened, because flattening any of them is the failure this sprint exists to
 * prevent:
 *
 * - `confirmation` on an impact: `confirmed` means something was measured on
 *   that service; `predicted` means it merely depends on something that was. The
 *   cockpit renders them under two legends and never merges the lists.
 * - `confidence` beside `history` on a hypothesis. The number is the server's
 *   arithmetic and the history is why it moved, entry by entry, so the card can
 *   answer "why did #2 go from 65% to 32%" without another round trip.
 * - `status` and `version` on every node. Nothing is deleted; a client that only
 *   showed `current` would hide the half a review reads.
 * - `verdict` on a copilot answer: four states an operator learns once, rather
 *   than a paragraph they have to read to find out whether the assistant knew.
 */

import { proxyApi } from "@/lib/api/client";

export type NodeType =
  | "fact"
  | "assumption"
  | "hypothesis"
  | "evidence"
  | "impact"
  | "risk"
  | "action"
  | "approval"
  | "communication";

export type NodeStatus = "current" | "superseded" | "retracted";

/** `confirmed` was measured. `predicted` was not measured at all. */
export type Confirmation = "confirmed" | "predicted";

export type Provenance = {
  source: string;
  actor_type: string;
  /** `authoritative`, `observed` or `inferred`. Anything a model concluded is
   * inferred - the brain correlates, it does not measure. */
  authority: string;
  model: string;
  model_class: string;
  degraded: boolean;
  /** Where the underlying data can be read in full. The drill-down handle. */
  result_ref: string;
};

export type BrainNode = {
  id: string;
  node_type: NodeType;
  derived_key: string;
  label: string;
  content: string;
  version: number;
  status: NodeStatus;
  seq: number;
  created_at: string;
  confidence: number | null;
  confirmation: Confirmation | null;
  /** True when the content is text this platform did not write. */
  untrusted: boolean;
  supersedes: string | null;
  superseded_by: string | null;
  retired_reason: string;
  tags: string[];
  provenance: Provenance;
  detail: Record<string, unknown>;
};

export type BrainEdge = {
  source_id: string;
  target_id: string;
  relation: string;
  weight: number;
  note: string;
};

export type ConfidenceEntry = {
  at: string;
  previous: number | null;
  current: number;
  delta: number;
  direction: "rose" | "fell" | "steady";
  net_weight: number;
  supporting: string[];
  refuting: string[];
  added: string[];
  trigger: string;
  /** The deterministic explanation, always populated. */
  explain: string;
  /** A sentence a model may write beside it. Never the source of a number. */
  narrative: string;
  narrative_degraded: boolean;
};

export type Hypothesis = {
  node: BrainNode;
  confidence: number;
  discriminating_test: string;
  explain: string;
  evidence_for: BrainNode[];
  evidence_against: BrainNode[];
  history: ConfidenceEntry[];
  sparkline: number[];
};

export type Snapshot = {
  id: string;
  taken_at: string;
  reason: string;
  fingerprint: string;
  topology_version: string;
  node_count: number;
  hypotheses: Record<string, number>;
  degraded: boolean;
};

export type AgentRun = {
  id: string;
  agent: string;
  status: string;
  trigger_topic: string;
  started_at: string;
  finished_at: string | null;
  model: string;
  tokens: number;
  error: string;
};

export type Brain = {
  incident_id: string;
  reference: string;
  app_id: string;
  degraded: boolean;
  degraded_reason: string;
  model_configured: boolean;
  counts: Record<string, number>;
  hypotheses: Hypothesis[];
  nodes: BrainNode[];
  edges: BrainEdge[];
  snapshots: Snapshot[];
  runs: AgentRun[];
};

export type NodeHistory = { derived_key: string; versions: BrainNode[] };

export type Impact = {
  service: string;
  confirmation: Confirmation;
  reason: string;
  hops: number;
  likelihood: number;
  path: string[];
  region: string;
};

export type TopologyDelta = {
  from_version: string;
  to_version: string;
  summary: string;
  added_nodes: string[];
  removed_nodes: string[];
  added_edges: string[][];
  removed_edges: string[][];
  changed_nodes: string[];
};

export type BrainTopology = {
  app_id: string;
  version: string;
  versions: string[];
  imported_at: string;
  node_count: number;
  edge_count: number;
  nodes: { id: string; name: string; type: string; region: string }[];
  edges: { source: string; target: string; type: string }[];
  delta: TopologyDelta | null;
  confirmed: string[];
  predicted: string[];
  impacts: Impact[];
  detail: Record<string, string>;
};

export type Guidance = {
  caveat: string;
  similar: BrainNode[];
  warnings: BrainNode[];
  contradictions: BrainNode[];
  injections: BrainNode[];
  degraded: boolean;
};

export type Answer = {
  question: string;
  answer: string;
  verdict: "grounded" | "insufficient_evidence" | "ungrounded" | "degraded";
  grounded: boolean;
  citations: string[];
  caveats: string[];
  considered: number;
  injection_observed: boolean;
  degraded: boolean;
  degraded_reason: string;
  model: string;
};

export type Refresh = {
  incident_id: string;
  ran: string[];
  nodes_written: number;
  versions_written: number;
  edges_written: number;
  moved: string[];
  hypotheses: Record<string, number>;
  degraded: boolean;
  degraded_reasons: string[];
  queries: number;
  tokens: number;
  snapshot_id: string;
  skipped: string;
};

// --- calls -------------------------------------------------------------------

const inc = (id: string) => encodeURIComponent(id);

export const getBrain = (incidentId: string) =>
  proxyApi.get<Brain>(`/incidents/${inc(incidentId)}/brain`);

export const getNodeHistory = (incidentId: string, derivedKey: string) =>
  proxyApi.get<NodeHistory>(
    `/incidents/${inc(incidentId)}/brain/nodes/${derivedKey
      .split("/")
      .map(encodeURIComponent)
      .join("/")}`,
  );

export const getBrainTopology = (incidentId: string, version?: string) =>
  proxyApi.get<BrainTopology>(
    `/incidents/${inc(incidentId)}/brain/topology${
      version ? `?version=${encodeURIComponent(version)}` : ""
    }`,
  );

export const getGuidance = (incidentId: string) =>
  proxyApi.get<Guidance>(`/incidents/${inc(incidentId)}/brain/guidance`);

export const askCopilot = (incidentId: string, question: string) =>
  proxyApi.post<Answer>(`/incidents/${inc(incidentId)}/brain/ask`, { question });

export const refreshBrain = (incidentId: string) =>
  proxyApi.post<Refresh>(`/incidents/${inc(incidentId)}/brain/refresh`, {});
