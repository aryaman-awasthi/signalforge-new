"use client";

import { useCallback, useEffect, useState } from "react";

import { ApiError } from "@/lib/api/client";
import {
  CapabilityMatrix,
  Registry,
  capabilityMatrix,
  listAdapters,
} from "@/features/integrations/api";

export type RegistryState =
  | { kind: "loading" }
  | { kind: "ready"; registry: Registry; matrix: CapabilityMatrix }
  | { kind: "error"; message: string };

/** The adapter registry section of S21. Static per deployment, so no timer. */
export function useRegistry() {
  const [state, setState] = useState<RegistryState>({ kind: "loading" });

  const reload = useCallback(async () => {
    try {
      const [registry, matrix] = await Promise.all([
        listAdapters(),
        capabilityMatrix(),
      ]);
      setState({ kind: "ready", registry, matrix });
    } catch (error) {
      setState({
        kind: "error",
        message:
          error instanceof ApiError ? error.message : "Could not load the registry",
      });
    }
  }, []);

  useEffect(() => {
    void reload();
  }, [reload]);

  return { state, reload };
}
