"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError } from "@/lib/api/client";
import {
  Detection,
  Lifecycle,
  getDetection,
  getLifecycle,
} from "@/features/incidents/api";

export type LifecycleState =
  | { kind: "loading" }
  | { kind: "ready"; lifecycle: Lifecycle; detection: Detection | null; detectionError: string }
  | { kind: "error"; message: string };

export const POLL_MS = 5000;

/**
 * S5's app detail, polled (REQ-040, REQ-043).
 *
 * Five seconds because that is the detector's own evaluation cadence: polling
 * faster shows the same answer twice, polling slower means a screen lags the
 * thing it is describing.
 *
 * Detection is read *beside* the lifecycle rather than depended on. A node with
 * detection disabled, or a detector that is failing, still has to render an
 * incident somebody declared - and the moment the detector is broken is exactly
 * the moment somebody most needs the screen. So a failed detection read becomes
 * `detectionError` and the lifecycle renders regardless; only a failed
 * *lifecycle* read is an error state.
 */
export function useLifecycle(appId: string) {
  const [state, setState] = useState<LifecycleState>({ kind: "loading" });
  const live = useRef(true);

  const reload = useCallback(async () => {
    try {
      const lifecycle = await getLifecycle(appId);
      let detection: Detection | null = null;
      let detectionError = lifecycle.detection_error;
      try {
        detection = await getDetection(appId);
      } catch (error) {
        detectionError =
          error instanceof ApiError
            ? error.message
            : "detection could not be read";
      }
      if (live.current) {
        setState({ kind: "ready", lifecycle, detection, detectionError });
      }
    } catch (error) {
      if (!live.current) return;
      setState({
        kind: "error",
        message:
          error instanceof ApiError
            ? error.message
            : "Could not load this application",
      });
    }
  }, [appId]);

  useEffect(() => {
    live.current = true;
    void reload();
    const handle = setInterval(() => void reload(), POLL_MS);
    return () => {
      live.current = false;
      clearInterval(handle);
    };
  }, [reload]);

  return { state, reload };
}
