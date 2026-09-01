"use client";

import { useCallback, useEffect, useState } from "react";

import { ApiError } from "@/lib/api/client";
import { ApplicationSummary, listApps } from "@/features/apps/api";

export type AppsState =
  | { kind: "loading" }
  | { kind: "ready"; apps: ApplicationSummary[] }
  | { kind: "error"; message: string };

/** S4. One read; the register form calls reload() after a successful POST. */
export function useApps() {
  const [state, setState] = useState<AppsState>({ kind: "loading" });

  const reload = useCallback(async () => {
    try {
      setState({ kind: "ready", apps: await listApps() });
    } catch (error) {
      setState({
        kind: "error",
        message:
          error instanceof ApiError ? error.message : "Could not load applications",
      });
    }
  }, []);

  useEffect(() => {
    void reload();
  }, [reload]);

  return { state, reload };
}
