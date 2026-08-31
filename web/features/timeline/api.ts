/**
 * Event timeline data access. Everything goes through the cookie proxy, so the
 * browser never holds a token and the API applies the tenant predicate.
 * The shapes mirror the frozen envelope contract exactly - the cockpit decodes
 * what the store holds rather than a view model invented for the screen.
 */

import { proxyApi } from "@/lib/api/client";

export type Plane = "signal" | "incident";

export type EventEnvelope = {
  schema_version: number;
  event_id: string;
  topic: string;
  tenant_id: string;
  app_id: string | null;
  incident_id: string | null;
  plane: Plane;
  occurred_at: string;
  recorded_at: string | null;
  seq: number | null;
  actor: { type: string; id: string; display: string | null };
  source: {
    system: string;
    kind: string | null;
    authority: string;
    freshness_s: number | null;
    simulated: boolean;
  };
  confidence: number | null;
  correlation_id: string | null;
  causation_id: string | null;
  supersedes: string | null;
  idempotency_key: string | null;
  redaction: { applied: boolean; fields: string[]; policy_id: string | null };
  payload: Record<string, unknown>;
  payload_hash: string | null;
  prev_hash: string | null;
};

export type EventPage = {
  events: EventEnvelope[];
  count: number;
  after_seq: number;
  next_after_seq: number;
  has_more: boolean;
};

export type CausalChain = {
  event_id: string;
  events: EventEnvelope[];
  count: number;
};

export type TimelineQuery = {
  afterSeq?: number;
  until?: number;
  topics?: string[];
  incidentId?: string;
  plane?: Plane;
  limit?: number;
};

export function timelineQueryString(query: TimelineQuery): string {
  const params = new URLSearchParams();
  if (query.afterSeq !== undefined) params.set("after_seq", String(query.afterSeq));
  if (query.until !== undefined) params.set("until", String(query.until));
  if (query.incidentId) params.set("incident_id", query.incidentId);
  if (query.plane) params.set("plane", query.plane);
  if (query.limit !== undefined) params.set("limit", String(query.limit));
  // Repeated key rather than a comma list: the API declares topics as a
  // repeated query parameter, and a topic may not contain a comma anyway.
  (query.topics ?? []).forEach((topic) => params.append("topics", topic));
  const encoded = params.toString();
  return encoded ? `?${encoded}` : "";
}

export const listEvents = (query: TimelineQuery = {}) =>
  proxyApi.get<EventPage>(`/events${timelineQueryString(query)}`);

export const causalChain = (eventId: string) =>
  proxyApi.get<CausalChain>(`/events/${eventId}/chain`);
