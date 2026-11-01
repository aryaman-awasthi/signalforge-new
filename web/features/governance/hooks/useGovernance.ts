"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError } from "@/lib/api/client";
import {
  Comms,
  Ledger,
  Nba,
  ValidationView,
  getCommunications,
  getLedger,
  getNextBestActions,
  getValidation,
} from "@/features/governance/api";
import { useLiveRoom } from "@/features/warroom/hooks/useLiveRoom";

export const GOVERNANCE_POLL_MS = 10000;
export const GOVERNANCE_LIVE_POLL_MS = 45000;

export type GovernanceState =
  | { kind: "loading" }
  | {
      kind: "ready";
      nba: Nba | null;
      ledger: Ledger;
      comms: Comms | null;
      validation: ValidationView | null;
    }
  | { kind: "error"; message: string };

/**
 * S11, S12, S15 and S16, on one hook and **one socket**.
 *
 * `useLiveRoom` is the only WebSocket store in this application, and the
 * server's cockpit consumer subscribes to `execution.*`, `policy.*`, `comms.*`
 * and `validation.*` alongside the families Sprints 4 and 5 added. An execution
 * that has just landed `uncertain` therefore reaches the screen on the frame the
 * platform was already sending, and this hook opens no connection of its own.
 *
 * The poll is faster than the brain's and slower than the war room's, which is
 * the honest middle: an approval countdown is measured in minutes and an
 * uncertain execution is the one thing on this screen nobody should learn about
 * late. It never stops, only slows - a cockpit whose socket is down falls back
 * to a poll rather than to a stale screen nobody notices.
 *
 * The four reads are independent and each survives its own failure. A
 * communications endpoint that is erroring must not be able to take the
 * execution ledger off the screen: the ledger is the record of what is
 * happening to somebody's production estate, and it is the last thing to hide.
 * The ledger alone can fail the whole hook, because a governance panel with no
 * ledger has nothing to say.
 */
export function useGovernance(incidentId: string) {
  const [state, setState] = useState<GovernanceState>({ kind: "loading" });
  const live = useRef(true);

  const reload = useCallback(async () => {
    try {
      const ledger = await getLedger(incidentId);
      const [nba, comms, validation] = await Promise.all([
        getNextBestActions(incidentId).catch(() => null),
        getCommunications(incidentId).catch(() => null),
        getValidation(incidentId).catch(() => null),
      ]);
      if (live.current)
        setState({ kind: "ready", nba, ledger, comms, validation });
    } catch (error) {
      if (!live.current) return;
      setState({
        kind: "error",
        message:
          error instanceof ApiError
            ? error.message
            : "Could not load the execution ledger",
      });
    }
  }, [incidentId]);

  const { connected } = useLiveRoom(incidentId, reload);

  useEffect(() => {
    live.current = true;
    void reload();
    const handle = setInterval(
      () => void reload(),
      connected ? GOVERNANCE_LIVE_POLL_MS : GOVERNANCE_POLL_MS,
    );
    return () => {
      live.current = false;
      clearInterval(handle);
    };
  }, [reload, connected]);

  return { state, reload, connected };
}

/**
 * A countdown that ticks in the browser from the server's `seconds_left`.
 *
 * The number on the screen is advisory and the server is the authority: an
 * approval that reads 00:03 here is refused at execution the moment its
 * `expires_at` passes, whether or not any tab was open to watch it. It ticks
 * from the seconds the server said were left at the moment of the read, not
 * from `expires_at - Date.now()`: a laptop clock a few minutes off would show
 * an approval as expired while the gateway still honoured it, or as live after
 * the gateway had refused it. Every poll re-anchors, so drift between polls is
 * bounded and never cumulative (the same design as the pager's useCountdown).
 */
export function useSecondsLeft(secondsLeft: number | null | undefined): number {
  const anchor = secondsLeft ?? null;
  const [left, setLeft] = useState(() => Math.max(0, anchor ?? 0));

  useEffect(() => {
    setLeft(Math.max(0, anchor ?? 0));
    if (anchor === null || anchor <= 0) return;
    const handle = setInterval(
      () => setLeft((current) => (current > 0 ? current - 1 : 0)),
      1000,
    );
    return () => clearInterval(handle);
  }, [anchor]);

  return left;
}

export function formatCountdown(seconds: number): string {
  const minutes = Math.floor(seconds / 60);
  const rest = seconds % 60;
  return `${minutes}:${String(rest).padStart(2, "0")}`;
}
