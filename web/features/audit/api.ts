/**
 * The sealed record (REQ-072, REQ-016), through the cookie proxy.
 *
 * Two reads, both behind `read_audit`, and both of them derived rather than
 * stored: the replay is reconstructed from the hash-chained audit log, and the
 * review is generated from the replay. Neither is a document somebody saved,
 * which is the whole guarantee - a stored copy is the thing that drifts.
 *
 * `chain` is the shape that matters most on this screen. It is not a boolean
 * dressed up: `valid`, how many entries were `checked`, and `first_broken_seq`
 * when it is not. A screen that rendered "verified ✓" without the count would be
 * claiming more than the check performed.
 */

import { proxyApi } from "@/lib/api/client";

export type ChainStatus = {
  valid: boolean;
  checked: number;
  first_broken_seq: number | null;
  broken_seqs: number[];
  reason: string;
};

export type ReplayEntry = {
  seq: number;
  event_id: string;
  topic: string;
  at: string;
  /** What happened. */
  headline: string;
  actor_type: string;
  actor_id: string;
  /** Who did it, by name where the platform knows one. */
  actor_label: string;
  /** What it led to. */
  consequence: string;
  /** What justified it - the policy rule, the permission, the corroboration. */
  justification: string[];
  /** What changed in the world. */
  effect: string;
  /** What can be opened from here. */
  references: string[];
  payload: Record<string, unknown>;
};

export type Seal = {
  seal_id: string;
  from_seq: number;
  to_seq: number;
  event_count: number;
  seal_hash: string;
  sealed_at: string;
  sealed_by: string;
};

export type Replay = {
  incident_id: string;
  generated_at: string;
  chain: ChainStatus;
  count: number;
  from_seq: number;
  to_seq: number;
  entries: ReplayEntry[];
  seals: Seal[];
};

export type Citation = { ref: string; kind: string; detail: string };

export type Lesson = {
  id: string;
  text: string;
  /** `verified` only when every citation resolves to a record this incident
   * produced. Anything else is `proposed`, carrying the refs that did not. */
  status: "verified" | "proposed" | string;
  source: string;
  citations: Citation[];
  unresolved: string[];
  verified_at: string | null;
};

export type PirSection = {
  key: string;
  title: string;
  lines: string[];
  citations: Citation[];
};

export type Pir = {
  incident_id: string;
  reference: string;
  title: string;
  app_id: string;
  generated_at: string;
  chain_valid: boolean;
  chain_detail: string;
  degraded: boolean;
  model: string;
  sections: PirSection[];
  lessons: Lesson[];
  verified_lesson_count: number;
  proposed_lesson_count: number;
  followups: string[];
  metadata: Record<string, unknown>;
};

export function getReplay(incidentId: string, untilSeq?: number) {
  const query = untilSeq === undefined ? "" : `?until_seq=${untilSeq}`;
  return proxyApi.get<Replay>(
    `/incidents/${encodeURIComponent(incidentId)}/replay${query}`,
  );
}

export function getPir(incidentId: string) {
  return proxyApi.get<Pir>(`/incidents/${encodeURIComponent(incidentId)}/pir`);
}

export function generatePir(incidentId: string) {
  return proxyApi.post<Pir>(`/incidents/${encodeURIComponent(incidentId)}/pir`, {});
}
