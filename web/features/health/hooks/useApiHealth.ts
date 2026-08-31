"use client";

import { useCallback, useEffect, useState } from "react";

import { ApiError } from "@/lib/api/client";
import { fetchHealth, type HealthResponse } from "@/features/health/api";

export type HealthState =
  | { kind: "loading" }
  | { kind: "ok"; data: HealthResponse; latencyMs: number }
  | { kind: "down"; detail: string };

export function useApiHealth(): { state: HealthState; refresh: () => void } {
  const [state, setState] = useState<HealthState>({ kind: "loading" });

  const refresh = useCallback(() => {
    setState({ kind: "loading" });
    const started = performance.now();
    fetchHealth()
      .then((data) =>
        setState({ kind: "ok", data, latencyMs: Math.round(performance.now() - started) }),
      )
      .catch((error: unknown) =>
        setState({
          kind: "down",
          detail: error instanceof ApiError ? error.message : "unexpected error",
        }),
      );
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  return { state, refresh };
}
