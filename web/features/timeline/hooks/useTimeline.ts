"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

import { ApiError } from "@/lib/api/client";
import {
  EventEnvelope,
  TimelineQuery,
  listEvents,
} from "@/features/timeline/api";
import {
  TimelineState,
  emptyTimeline,
  filterEvents,
  mergeEvents,
  setAnchor,
  topicsOf,
} from "@/features/timeline/store";

const PAGE_SIZE = 200;

export type TimelineStatus = "loading" | "ready" | "error";

/**
 * Paged replay into the timeline store. Loading always resumes from lastSeq, so
 * pressing "load more" repeatedly cannot re-fetch or duplicate history, and the
 * same call is what a WebSocket reconnect will use to catch up in Sprint 3.
 */
export function useTimeline(query: Omit<TimelineQuery, "afterSeq" | "limit"> = {}) {
  const [state, setState] = useState<TimelineState>(emptyTimeline);
  const [status, setStatus] = useState<TimelineStatus>("loading");
  const [message, setMessage] = useState<string | null>(null);
  const [hasMore, setHasMore] = useState(false);
  const [selectedTopics, setSelectedTopics] = useState<string[]>([]);

  const { incidentId, plane, until, topics } = query;
  const topicsKey = (topics ?? []).join(",");

  const loadFrom = useCallback(
    async (afterSeq: number, replace: boolean) => {
      try {
        const page = await listEvents({
          afterSeq,
          until,
          incidentId,
          plane,
          topics: topicsKey ? topicsKey.split(",") : undefined,
          limit: PAGE_SIZE,
        });
        setState((current) =>
          mergeEvents(replace ? emptyTimeline : current, page.events),
        );
        setHasMore(page.has_more);
        setStatus("ready");
        setMessage(null);
      } catch (error) {
        setStatus("error");
        setMessage(
          error instanceof ApiError ? error.message : "Could not load the timeline",
        );
      }
    },
    [incidentId, plane, topicsKey, until],
  );

  useEffect(() => {
    setStatus("loading");
    void loadFrom(0, true);
  }, [loadFrom]);

  const loadMore = useCallback(() => loadFrom(state.lastSeq, false), [
    loadFrom,
    state.lastSeq,
  ]);

  const anchor = useCallback(
    (seq: number | null) => setState((current) => setAnchor(current, seq)),
    [],
  );

  const visible = useMemo(
    () => filterEvents(state.events, selectedTopics),
    [state.events, selectedTopics],
  );
  const availableTopics = useMemo(() => topicsOf(state.events), [state.events]);

  return {
    status,
    message,
    hasMore,
    events: visible as EventEnvelope[],
    total: state.events.length,
    lastSeq: state.lastSeq,
    anchorSeq: state.anchorSeq,
    availableTopics,
    selectedTopics,
    setSelectedTopics,
    anchor,
    loadMore,
    reload: () => loadFrom(0, true),
  };
}
