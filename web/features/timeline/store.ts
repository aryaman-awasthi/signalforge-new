/**
 * Timeline state: an ordered, de-duplicated event log plus a seq anchor.
 *
 * Two sources will feed it - replay pages now, the WebSocket from Sprint 3 -
 * and both are at-least-once, so the store is written to survive the same event
 * arriving twice and out of order. seq is the ordering key because it is the
 * store's recording order; occurred_at is display time and, under a drill
 * clock, is not monotonic.
 *
 * The anchor is what makes a timeline readable during an incident: it pins the
 * view to one seq so new arrivals do not scroll the operator's place away.
 */

import type { EventEnvelope } from "@/features/timeline/api";

export type TimelineState = {
  events: EventEnvelope[];
  lastSeq: number;
  anchorSeq: number | null;
};

export const emptyTimeline: TimelineState = {
  events: [],
  lastSeq: 0,
  anchorSeq: null,
};

/** Insert-or-ignore by event_id, keeping the list sorted by seq. */
export function mergeEvents(
  state: TimelineState,
  incoming: EventEnvelope[],
): TimelineState {
  if (incoming.length === 0) return state;
  const byId = new Map(state.events.map((event) => [event.event_id, event]));
  let changed = false;
  for (const event of incoming) {
    if (!byId.has(event.event_id)) {
      byId.set(event.event_id, event);
      changed = true;
    }
  }
  if (!changed) return state;

  const events = Array.from(byId.values()).sort((a, b) => (a.seq ?? 0) - (b.seq ?? 0));
  return {
    ...state,
    events,
    lastSeq: Math.max(state.lastSeq, ...events.map((event) => event.seq ?? 0)),
  };
}

export function setAnchor(state: TimelineState, seq: number | null): TimelineState {
  return { ...state, anchorSeq: seq };
}

export function resetTimeline(): TimelineState {
  return emptyTimeline;
}

/** Topics present in the loaded window, for the filter chips. */
export function topicsOf(events: EventEnvelope[]): string[] {
  return Array.from(new Set(events.map((event) => event.topic))).sort();
}

/** The namespace a topic belongs to: "system.llm.degraded" -> "system". */
export function namespaceOf(topic: string): string {
  return topic.split(".")[0] ?? topic;
}

export function matchesTopics(topic: string, selected: string[]): boolean {
  if (selected.length === 0) return true;
  return selected.some(
    (pattern) =>
      pattern === topic ||
      (pattern.endsWith(".*") && topic.startsWith(pattern.slice(0, -1))),
  );
}

export function filterEvents(
  events: EventEnvelope[],
  selectedTopics: string[],
): EventEnvelope[] {
  return events.filter((event) => matchesTopics(event.topic, selectedTopics));
}

/** Index of the first event at or after the anchor, for scroll restoration. */
export function indexOfSeq(events: EventEnvelope[], seq: number): number {
  const index = events.findIndex((event) => (event.seq ?? 0) >= seq);
  return index === -1 ? Math.max(events.length - 1, 0) : index;
}
