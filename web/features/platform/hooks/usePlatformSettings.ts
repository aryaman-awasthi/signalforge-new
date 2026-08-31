"use client";

import { useCallback, useEffect, useState } from "react";

import { ApiError } from "@/lib/api/client";
import {
  DeadLetter,
  PlatformStatus,
  listDeadLetters,
  platformStatus,
} from "@/features/platform/api";

export type SettingsData = { status: PlatformStatus; deadLetters: DeadLetter[] };

export type SettingsState =
  | { kind: "loading" }
  | { kind: "ready"; data: SettingsData }
  | { kind: "error"; message: string };

/**
 * One load for the settings screen, refreshed on a timer because platform
 * health is the one screen whose value is in being current. The interval is
 * generous: the numbers move on the worker's poll cadence, not on ours.
 */
export function usePlatformSettings(refreshMs = 15_000) {
  const [state, setState] = useState<SettingsState>({ kind: "loading" });

  const reload = useCallback(async () => {
    try {
      const [status, deadLetters] = await Promise.all([
        platformStatus(),
        listDeadLetters(),
      ]);
      setState({ kind: "ready", data: { status, deadLetters } });
    } catch (error) {
      setState({
        kind: "error",
        message:
          error instanceof ApiError ? error.message : "Could not load platform status",
      });
    }
  }, []);

  useEffect(() => {
    void reload();
    if (refreshMs <= 0) return;
    const timer = setInterval(() => void reload(), refreshMs);
    return () => clearInterval(timer);
  }, [reload, refreshMs]);

  return { state, reload };
}
