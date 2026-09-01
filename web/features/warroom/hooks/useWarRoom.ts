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

import { useLivePages, useLiveRoom } from "@/features/warroom/hooks/useLiveRoom";

export const ROOM_POLL_MS = 5000;
export const PAGER_POLL_MS = 10000;

/** The poll when the socket is up. Not zero: the socket is an accelerator, and
 * a screen that stopped reading entirely would go stale without noticing if a
 * nudge were ever dropped - which the hub does deliberately when one browser
 * falls behind. Thirty seconds is a safety net rather than a refresh rate. */
export const ROOM_LIVE_POLL_MS = 30000;
export const PAGER_LIVE_POLL_MS = 30000;

export type RoomState =
  | { kind: "loading" }
  | { kind: "ready"; room: WarRoom; brief: Brief | null; bridge: Bridge | null }
  | { kind: "error"; message: string };

/**
 * S9's war-room shell (REQ-045).
 *
 * Pushed *and* polled, in that order of preference, and both matter.
 *
 * The push is the socket (`useLiveRoom`): the server nudges when something in
 * this room moves and the screen re-reads immediately. The nudge carries no
 * data - the re-read below is still the only way anything reaches this screen,
 * so the authorised route is still the only read path.
 *
 * The poll never stops; it slows down. A cockpit whose socket is down, whose
 * ticket endpoint is unreachable or whose browser dropped a frame under back
 * pressure falls back to exactly the behaviour S4 shipped, without a broken
 * screen and without anybody noticing at the wrong moment. Nothing here is
 * computed in the browser - countdowns are the server's own numbers, see
 * `useCountdown` - so a stale screen is late and never wrong.
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

  const { connected } = useLiveRoom(incidentId, reload);

  useEffect(() => {
    live.current = true;
    void reload();
    const handle = setInterval(
      () => void reload(),
      connected ? ROOM_LIVE_POLL_MS : ROOM_POLL_MS,
    );
    return () => {
      live.current = false;
      clearInterval(handle);
    };
  }, [reload, connected]);

  return { state, reload, connected };
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
 *
 * This is the screen the socket was worth building for: a page raised at 03:00
 * used to take up to ten seconds to appear. Now it arrives on the notification
 * frame the platform was already sending, and the ten-second poll remains as
 * the floor under it - a phone that slept through the socket still catches up.
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

  const { connected } = useLivePages(reload);

  useEffect(() => {
    live.current = true;
    void reload();
    const handle = setInterval(
      () => void reload(),
      connected ? PAGER_LIVE_POLL_MS : PAGER_POLL_MS,
    );
    return () => {
      live.current = false;
      clearInterval(handle);
    };
  }, [reload, connected]);

  return { state, reload, connected };
}
