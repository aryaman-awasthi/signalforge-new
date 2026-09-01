"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError } from "@/lib/api/client";
import {
  Brain,
  BrainTopology,
  Guidance,
  getBrain,
  getBrainTopology,
  getGuidance,
} from "@/features/brain/api";
import { useLiveRoom } from "@/features/warroom/hooks/useLiveRoom";

export const BRAIN_POLL_MS = 15000;
export const BRAIN_LIVE_POLL_MS = 60000;

export type BrainState =
  | { kind: "loading" }
  | { kind: "ready"; brain: Brain; guidance: Guidance | null }
  | { kind: "error"; message: string };

/**
 * S10 and S14 (REQ-050, REQ-053).
 *
 * Pushed and polled, exactly as `useWarRoom` is, and through **the same
 * socket**: `useLiveRoom` is the one WebSocket store in this application, and
 * the server's cockpit consumer now subscribes to `brain.*` alongside
 * `incident.*` and `warroom.*`. A hypothesis that moved therefore reaches the
 * screen on the frame the platform was already sending, and this hook opens no
 * connection of its own.
 *
 * The poll is slower than the war room's on purpose. A reasoning pass is
 * budgeted and runs every twenty seconds at most; polling faster than the thing
 * can change is cost with no information in it. It never stops, only slows -
 * a cockpit whose socket is down falls back to a poll rather than to a stale
 * screen nobody notices.
 *
 * Guidance is read beside the brain and survives its own failure: a panel that
 * could take the whole reasoning view down with it would be the wrong trade for
 * a panel of advisory rows.
 */
export function useBrain(incidentId: string) {
  const [state, setState] = useState<BrainState>({ kind: "loading" });
  const live = useRef(true);

  const reload = useCallback(async () => {
    try {
      const brain = await getBrain(incidentId);
      const guidance = await getGuidance(incidentId).catch(() => null);
      if (live.current) setState({ kind: "ready", brain, guidance });
    } catch (error) {
      if (!live.current) return;
      setState({
        kind: "error",
        message:
          error instanceof ApiError
            ? error.message
            : "Could not load the incident brain",
      });
    }
  }, [incidentId]);

  const { connected } = useLiveRoom(incidentId, reload);

  useEffect(() => {
    live.current = true;
    void reload();
    const handle = setInterval(
      () => void reload(),
      connected ? BRAIN_LIVE_POLL_MS : BRAIN_POLL_MS,
    );
    return () => {
      live.current = false;
      clearInterval(handle);
    };
  }, [reload, connected]);

  return { state, reload, connected };
}

export type TopologyState =
  | { kind: "loading" }
  | { kind: "ready"; topology: BrainTopology }
  | { kind: "error"; message: string };

/**
 * S13 (REQ-050).
 *
 * `version` is state rather than a parameter of the incident, because the
 * question the version switcher answers - "what did the map look like when this
 * started?" - is one an operator asks *during* the incident and then goes back
 * from. Older versions are kept forever, so switching costs a read and nothing
 * else.
 */
export function useBrainTopology(incidentId: string, version?: string) {
  const [state, setState] = useState<TopologyState>({ kind: "loading" });
  const live = useRef(true);

  const reload = useCallback(async () => {
    try {
      const topology = await getBrainTopology(incidentId, version);
      if (live.current) setState({ kind: "ready", topology });
    } catch (error) {
      if (!live.current) return;
      setState({
        kind: "error",
        message:
          error instanceof ApiError ? error.message : "Could not load the topology",
      });
    }
  }, [incidentId, version]);

  useEffect(() => {
    live.current = true;
    void reload();
    return () => {
      live.current = false;
    };
  }, [reload]);

  return { state, reload };
}
