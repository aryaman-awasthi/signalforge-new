"use client";

import { useCallback, useEffect, useState } from "react";

import { ApiError } from "@/lib/api/client";
import {
  ConnectorHealth,
  IngestKey,
  connectorHealth,
  listIngestKeys,
} from "@/features/integrations/api";

export type IntegrationHealthData = {
  connectors: ConnectorHealth[];
  /** Keys with no binding are integrations in their own right: two of the five
   * frozen streams can never have a push adapter, so a key is the whole
   * integration and would otherwise be invisible on this screen. */
  keys: IngestKey[];
};

export type IntegrationHealthState =
  | { kind: "loading" }
  | { kind: "ready"; data: IntegrationHealthData }
  | { kind: "error"; message: string };

/**
 * S23. Two reads, refreshed on a timer, because the value of this screen is in
 * being current: the connector loop writes a verdict every 30 seconds and a
 * push row's freshness moves whenever a sender posts.
 */
export function useIntegrationHealth(appId?: string, refreshMs = 15_000) {
  const [state, setState] = useState<IntegrationHealthState>({ kind: "loading" });

  const reload = useCallback(async () => {
    try {
      const [connectors, keys] = await Promise.all([
        connectorHealth(appId),
        listIngestKeys(appId),
      ]);
      setState({ kind: "ready", data: { connectors, keys } });
    } catch (error) {
      setState({
        kind: "error",
        message:
          error instanceof ApiError
            ? error.message
            : "Could not load integration health",
      });
    }
  }, [appId]);

  useEffect(() => {
    void reload();
    if (refreshMs <= 0) return;
    const timer = setInterval(() => void reload(), refreshMs);
    return () => clearInterval(timer);
  }, [reload, refreshMs]);

  return { state, reload };
}
