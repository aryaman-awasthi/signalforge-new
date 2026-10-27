"use client";

import { useEffect, useRef, useState } from "react";

import { Detection, getDetection } from "@/features/incidents/api";

export const DETECTION_POLL_MS = 5000;
/** Points kept per signal. Forty reads at five seconds is a little over three
 * minutes - the stretch of a sparkline a commander glances at. */
export const SERIES_LENGTH = 40;

export type Series = Record<string, number[]>;

export type DetectionSeriesState = {
  detection: Detection | null;
  error: string;
  /** Per signal key, the readings this screen has seen, oldest first. */
  series: Series;
  /** The health score over the same reads. */
  scores: number[];
};

/**
 * The live reading the war room's telemetry panel draws (REQ-040, REQ-045).
 *
 * The detection route carries one reading per signal per region - the current
 * one - and no history. The rollups behind it carry a history, but on a route
 * that needs `manage_apps`, which the people in a war room do not hold. So the
 * sparkline is drawn from what *this screen* has watched: every five seconds
 * the current reading is appended per signal, and the line is the last few
 * minutes as seen from this browser. That is an honest line - it is exactly
 * the readings the detector produced while the room was open - and it is why
 * the panel says "since you opened this" rather than pretending to a history
 * it never read.
 *
 * Per signal *key*, not per region: the worst region is appended, which is the
 * number the tile shows, so the line and the number agree.
 */
export function useDetectionSeries(appId: string | null): DetectionSeriesState {
  const [state, setState] = useState<DetectionSeriesState>({
    detection: null,
    error: "",
    series: {},
    scores: [],
  });
  const live = useRef(true);

  useEffect(() => {
    live.current = true;
    if (!appId) return;
    async function read() {
      try {
        const detection = await getDetection(appId as string);
        if (!live.current) return;
        setState((current) => {
          const series: Series = { ...current.series };
          for (const [key, value] of worstPerSignal(detection)) {
            series[key] = [...(series[key] ?? []), value].slice(-SERIES_LENGTH);
          }
          const scores =
            detection.health.score === null
              ? current.scores
              : [...current.scores, detection.health.score].slice(-SERIES_LENGTH);
          return { detection, error: "", series, scores };
        });
      } catch (error) {
        if (!live.current) return;
        setState((current) => ({
          ...current,
          error: error instanceof Error ? error.message : "detection could not be read",
        }));
      }
    }
    void read();
    const handle = setInterval(() => void read(), DETECTION_POLL_MS);
    return () => {
      live.current = false;
      clearInterval(handle);
    };
  }, [appId]);

  return state;
}

const SEVERITY_ORDER = ["healthy", "unknown", "stale", "breaching", "anomalous"];

/** One reading per signal key: the region in the worst state, then the largest
 * deviation. The same choice the golden tiles make. */
function worstPerSignal(detection: Detection): [string, number][] {
  const best = new Map<string, Detection["signals"][number]>();
  for (const signal of detection.signals) {
    if (signal.value === null) continue;
    const held = best.get(signal.signal_key);
    if (
      !held ||
      SEVERITY_ORDER.indexOf(signal.status) > SEVERITY_ORDER.indexOf(held.status) ||
      (signal.status === held.status && signal.deviation > held.deviation)
    ) {
      best.set(signal.signal_key, signal);
    }
  }
  return Array.from(best.entries()).map(([key, signal]) => [key, signal.value as number]);
}
