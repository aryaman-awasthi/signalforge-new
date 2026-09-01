"use client";

import { useCallback, useEffect, useState } from "react";

import { ApiError } from "@/lib/api/client";
import { ApplicationDetail, getApp } from "@/features/apps/api";
import {
  Adapter,
  Connector,
  IngestKey,
  Registry,
  listAdapters,
  listConnectors,
  listIngestKeys,
} from "@/features/integrations/api";

export type WizardData = {
  detail: ApplicationDetail;
  adapters: Adapter[];
  /** Manifests that would not load, carried through so the Connect step can say
   * why an integration an operator expected is not in the list. */
  registryErrors: Registry["errors"];
  connectors: Connector[];
  keys: IngestKey[];
};

export type WizardState =
  | { kind: "loading" }
  | { kind: "ready"; data: WizardData }
  | { kind: "error"; message: string; status: number | null };

/**
 * One load for the whole wizard; every step calls reload() after it writes.
 *
 * Reloading rather than patching local state is deliberate. Each step's write
 * is validated by the domain and can be partially refused - a risk override
 * that a re-import raised, a policy edit the floor rejected, a topology upload
 * that turned out to be unchanged - so the truth after a write is whatever the
 * platform now holds, not what the form thought it was sending.
 */
export function useOnboarding(appId: string) {
  const [state, setState] = useState<WizardState>({ kind: "loading" });

  const reload = useCallback(async () => {
    try {
      const [detail, registry, connectors, keys] = await Promise.all([
        getApp(appId),
        listAdapters(),
        listConnectors(appId),
        listIngestKeys(appId),
      ]);
      setState({
        kind: "ready",
        data: {
          detail,
          adapters: registry.adapters,
          registryErrors: registry.errors,
          connectors,
          keys,
        },
      });
    } catch (error) {
      // A permission narrowed mid-flow, or an application removed in another
      // tab, must land as a readable message on the step rather than a blank
      // screen. The status is kept so the shell can say which of the two it is.
      setState({
        kind: "error",
        message:
          error instanceof ApiError
            ? error.message
            : "Could not load this application",
        status: error instanceof ApiError ? error.status : null,
      });
    }
  }, [appId]);

  useEffect(() => {
    void reload();
  }, [reload]);

  return { state, reload };
}
