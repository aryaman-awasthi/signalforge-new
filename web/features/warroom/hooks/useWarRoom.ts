"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError } from "@/lib/api/client";
import {
  Bridge,
  Brief,
  PageList,
  WarRoom,
  getBridge,
  getBrief,
  getWarRoom,
  myPages,
} from "@/features/warroom/api";

export const ROOM_POLL_MS = 5000;
export const PAGER_POLL_MS = 10000;

export type RoomState =
  | { kind: "loading" }
  | { kind: "ready"; room: WarRoom; brief: Brief | null; bridge: Bridge | null }
  | { kind: "error"; message: string };

/**
 * S9's war-room shell (REQ-045).
 *
 * Polled rather than pushed, and that is an honest limitation rather than a
 * choice: the cockpit's session token lives in an httpOnly cookie that browser
 * JavaScript cannot read, so a cross-origin WebSocket handshake has nothing to
 * authenticate with. Giving JavaScript the token to fix that would weaken the
 * S1 control that keeps it out of XSS reach, and it is not a trade worth making
 * for a refresh interval. A socket ticket endpoint is the real answer and is a
 * credential design in its own right.
 *
 * Polling is adequate here because nothing on this screen is computed in the
 * browser: countdowns are the server's own numbers (see `useCountdown`), so a
 * five-second refresh lags reality by at most five seconds and never disagrees
 * with it.
 *
 * The brief and the bridge are read beside the room and each survives its own
 * failure. A room with no collaboration connector has no bridge at all, and the
 * panel says so - a war room whose whole screen failed because there was nothing
 * to transcribe would be the wrong failure entirely.
 */
export function useWarRoom(incidentId: string) {
  const [state, setState] = useState<RoomState>({ kind: "loading" });
  const live = useRef(true);

  const reload = useCallback(async () => {
    try {
      const room = await getWarRoom(incidentId);
      const [brief, bridge] = await Promise.all([
        getBrief(incidentId).catch(() => null),
        getBridge(incidentId).catch(() => null),
      ]);
      if (live.current) setState({ kind: "ready", room, brief, bridge });
    } catch (error) {
      if (!live.current) return;
      setState({
        kind: "error",
        message:
          error instanceof ApiError ? error.message : "Could not load the war room",
      });
    }
  }, [incidentId]);

  useEffect(() => {
    live.current = true;
    void reload();
    const handle = setInterval(() => void reload(), ROOM_POLL_MS);
    return () => {
      live.current = false;
      clearInterval(handle);
    };
  }, [reload]);

  return { state, reload };
}

export type PagerState =
  | { kind: "loading" }
  | { kind: "ready"; pages: PageList }
  | { kind: "error"; message: string };

/**
 * S22's mobile pager (REQ-045).
 *
 * `/my/pages` is self-scoped by construction - it has no recipient parameter -
 * so this hook cannot be pointed at anybody else even by mistake. That is what
 * lets the route carry no permission, and it is why the pager is safe to leave
 * open on a phone.
 */
export function useMyPages(openOnly = true) {
  const [state, setState] = useState<PagerState>({ kind: "loading" });
  const live = useRef(true);

  const reload = useCallback(async () => {
    try {
      const pages = await myPages(openOnly);
      if (live.current) setState({ kind: "ready", pages });
    } catch (error) {
      if (!live.current) return;
      setState({
        kind: "error",
        message:
          error instanceof ApiError ? error.message : "Could not load your pages",
      });
    }
  }, [openOnly]);

  useEffect(() => {
    live.current = true;
    void reload();
    const handle = setInterval(() => void reload(), PAGER_POLL_MS);
    return () => {
      live.current = false;
      clearInterval(handle);
    };
  }, [reload]);

  return { state, reload };
}
