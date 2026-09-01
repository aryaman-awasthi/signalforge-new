"use client";

import { useEffect, useRef, useState } from "react";

import { WsStatus, WsFrame, wsStore } from "@/lib/ws/store";

/**
 * "Re-read when something moves" (REQ-033, REQ-045).
 *
 * The one place a screen touches the socket. It does not hand a component any
 * data: it calls the reload the component already had, so the authorised HTTP
 * read stays the only way anything reaches the screen and the socket cannot
 * become a second read path with its own authorisation to get wrong.
 *
 * `connected` is returned so a screen can say which mode it is in. That matters
 * more than it looks: a war room that is silently five seconds stale during a
 * P0 is fine, and a war room that a responder *believes* is live when it is not
 * is how somebody concludes nobody is answering.
 */
export function useLiveRoom(
  incidentId: string | null,
  reload: () => void | Promise<void>,
): { connected: boolean; status: WsStatus } {
  const [status, setStatus] = useState<WsStatus>(wsStore.status);
  // The latest reload, without making the socket subscription depend on it: a
  // new callback identity on every render would otherwise reconnect the socket
  // on every render.
  const latest = useRef(reload);
  latest.current = reload;

  useEffect(() => wsStore.onStatus(setStatus), []);

  useEffect(() => {
    if (!incidentId) return;
    const stop = wsStore.subscribe((frame: WsFrame) => {
      if (frame.type !== "room.changed") return;
      if (frame.incident_id !== incidentId) return;
      void latest.current();
    });
    const unwatch = wsStore.watch(incidentId);
    return () => {
      unwatch();
      stop();
    };
  }, [incidentId]);

  return { connected: status === "connected", status };
}

/**
 * The pager's half: a notification for *this person* means the page list moved.
 *
 * `/my/pages` has no recipient parameter, so there is nothing to filter by and
 * nothing to get wrong - any notification this session receives is one of its
 * own, and re-reading is the correct response to all of them.
 */
export function useLivePages(reload: () => void | Promise<void>): {
  connected: boolean;
  status: WsStatus;
} {
  const [status, setStatus] = useState<WsStatus>(wsStore.status);
  const latest = useRef(reload);
  latest.current = reload;

  useEffect(() => wsStore.onStatus(setStatus), []);

  useEffect(() => {
    return wsStore.subscribe((frame: WsFrame) => {
      if (frame.type !== "notification") return;
      void latest.current();
    });
  }, []);

  return { connected: status === "connected", status };
}
