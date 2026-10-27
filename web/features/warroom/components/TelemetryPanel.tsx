"use client";

import Link from "next/link";

import { useDetectionSeries } from "@/features/incidents/hooks/useDetectionSeries";
import type { Detection, SignalState } from "@/features/incidents/api";
import { Sparkline, StatePill } from "@/lib/ui/primitives";

/**
 * Health & Telemetry - the prototype's right-hand instrument (S9, REQ-040).
 *
 * One big number, then one row per golden-signal family: the family's name, its
 * worst current reading, and a sparkline of what this screen has watched. The
 * score at declare time sits under the live one as a delta, so "is it getting
 * better" is answered without opening the application page.
 *
 * Everything here is read from the detector, never from the brain: the number a
 * commander measures recovery against must not be the platform's opinion of it.
 */

const FAMILY_LABEL: Record<string, string> = {
  errors: "Error rate",
  latency: "Latency",
  traffic: "Throughput",
  saturation: "Saturation",
  availability: "Availability",
  business: "Business",
};

/** The order a commander reads them in. Business last: it moves slowest. */
const FAMILIES = ["errors", "latency", "traffic", "saturation", "availability", "business"];

const SEVERITY_ORDER = ["healthy", "unknown", "stale", "breaching", "anomalous"];

function worst(states: SignalState[]): SignalState | null {
  if (states.length === 0) return null;
  return [...states].sort(
    (a, b) =>
      SEVERITY_ORDER.indexOf(b.status) - SEVERITY_ORDER.indexOf(a.status) ||
      b.deviation - a.deviation,
  )[0];
}

function reading(state: SignalState): string {
  if (state.value === null) return "--";
  const magnitude = Math.abs(state.value);
  if (magnitude >= 1000) return `${(state.value / 1000).toFixed(1)}k`;
  if (magnitude >= 100) return state.value.toFixed(0);
  if (magnitude >= 1) return state.value.toFixed(1);
  return state.value.toFixed(3);
}

const STATUS_INK: Record<string, string> = {
  healthy: "text-ink",
  stale: "text-neutral-500",
  breaching: "text-warn-ink",
  anomalous: "text-accent-strong",
  unknown: "text-neutral-500",
};

export function TelemetryPanel({
  appId,
  declaredScore,
}: {
  appId: string;
  /** The health score captured when Declare was pressed, if there was one. */
  declaredScore: number | null;
}) {
  const { detection, error, series, scores } = useDetectionSeries(appId);
  const health = detection?.health ?? null;
  const byFamily = groupByFamily(detection);
  const delta =
    health?.score !== null && health?.score !== undefined && declaredScore !== null
      ? Math.round(health.score - declaredScore)
      : null;

  return (
    <div className="flex flex-col">
      <div className="flex items-center gap-4 border-b border-line pb-3">
        <span className="font-display text-hero font-extrabold leading-none tabular-nums">
          {health?.score === null || health?.score === undefined
            ? "--"
            : health.score.toFixed(0)}
        </span>
        <span className="flex min-w-0 flex-col gap-1">
          <span className="text-nano uppercase tracking-label text-neutral-600">
            Service health score
          </span>
          <span className="flex flex-wrap items-center gap-2">
            {health && <StatePill state={health.band || "unknown"} size="tiny" />}
            {delta !== null && (
              <span
                className={`text-micro font-bold ${
                  delta > 0 ? "text-ok" : delta < 0 ? "text-accent-strong" : "text-neutral-600"
                }`}
                title={`${declaredScore} when the incident was declared`}
              >
                {delta > 0 ? "▲" : delta < 0 ? "▼" : "="} {Math.abs(delta)} since declare
              </span>
            )}
          </span>
        </span>
        {scores.length > 1 && (
          <span className="ml-auto">
            <Sparkline points={scores} tone="context" width={72} height={22} />
          </span>
        )}
      </div>

      {error ? (
        <p className="pt-3 text-small text-warn-ink">
          The detector could not be read: {error}. The numbers above are the last
          ones this screen saw.
        </p>
      ) : !detection ? (
        <p className="pt-3 text-small text-ink-muted">Reading the detector…</p>
      ) : (
        <ul className="flex flex-col">
          {FAMILIES.map((family) => {
            const state = worst(byFamily.get(family) ?? []);
            if (!state) return null;
            const points = series[state.signal_key] ?? [];
            const problem = state.status === "anomalous" || state.status === "breaching";
            return (
              <li
                key={family}
                className="flex items-center gap-3 border-b border-line py-2.5 last:border-b-0"
              >
                <span className="flex min-w-0 flex-1 flex-col">
                  <span className="text-nano uppercase tracking-label text-neutral-600">
                    {FAMILY_LABEL[family] ?? family}
                  </span>
                  <span className="truncate font-mono text-tiny text-ink-muted" title={state.signal_key}>
                    {state.signal_key}
                    {state.region ? ` · ${state.region}` : ""}
                  </span>
                </span>
                <Sparkline
                  points={points}
                  tone={problem ? "problem" : "context"}
                  width={88}
                  height={18}
                />
                <span className="flex w-16 flex-col items-end">
                  <span
                    className={`font-display text-h5 font-extrabold leading-none tabular-nums ${
                      STATUS_INK[state.status] ?? ""
                    }`}
                  >
                    {reading(state)}
                  </span>
                  <span className="text-nano uppercase tracking-label text-neutral-500">
                    {state.status}
                  </span>
                </span>
              </li>
            );
          })}
        </ul>
      )}

      <p className="mt-2 flex flex-wrap items-baseline gap-x-2 text-tiny text-ink-muted">
        <span>Lines show readings since you opened this screen.</span>
        <Link
          href={`/apps/${encodeURIComponent(appId)}`}
          className="font-semibold text-accent hover:underline"
        >
          Application page →
        </Link>
      </p>
    </div>
  );
}

function groupByFamily(detection: Detection | null): Map<string, SignalState[]> {
  const byFamily = new Map<string, SignalState[]>();
  for (const signal of detection?.signals ?? []) {
    byFamily.set(signal.family, [...(byFamily.get(signal.family) ?? []), signal]);
  }
  return byFamily;
}
