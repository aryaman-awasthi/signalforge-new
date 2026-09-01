"use client";

import { Detection, SignalState } from "@/features/incidents/api";

/**
 * S5 - the golden signal tiles.
 *
 * One tile per signal family, showing the worst region's state. Worst rather
 * than an average, because an average of "one region is down and one is fine"
 * is "half broken", which describes nothing that is actually happening and
 * hides the region a responder needs to look at.
 *
 * `unknown` is a distinct state everywhere on this screen and is never drawn as
 * healthy. "We could not measure this" and "this is fine" lead to opposite next
 * actions, and a grey tile that looks green is how a blind spot survives an
 * incident review.
 */

const FAMILIES = [
  "availability",
  "errors",
  "business",
  "latency",
  "saturation",
  "traffic",
] as const;

const STATUS_TONE: Record<string, string> = {
  ok: "border-ok/40 bg-ok/10 text-ok",
  breaching: "border-warn/40 bg-warn/10 text-warn",
  anomalous: "border-danger/40 bg-danger/10 text-danger",
  unknown: "border-line bg-surface text-ink-muted",
};

/** The worst state among a family's regions. Ordered by how much it should
 * worry somebody, with `unknown` deliberately above `ok`. */
const SEVERITY_ORDER = ["ok", "unknown", "breaching", "anomalous"];

function worst(states: SignalState[]): SignalState | null {
  if (states.length === 0) return null;
  return [...states].sort(
    (a, b) =>
      SEVERITY_ORDER.indexOf(b.status) - SEVERITY_ORDER.indexOf(a.status) ||
      b.deviation - a.deviation,
  )[0];
}

function reading(state: SignalState): string {
  if (state.value === null) return "no data";
  const value = Math.abs(state.value) >= 100 ? state.value.toFixed(0) : state.value.toFixed(2);
  if (state.threshold === null) return value;
  return `${value} (threshold ${state.threshold.toFixed(2)})`;
}

export function GoldenTiles({ detection }: { detection: Detection | null }) {
  const byFamily = new Map<string, SignalState[]>();
  for (const signal of detection?.signals ?? []) {
    byFamily.set(signal.family, [...(byFamily.get(signal.family) ?? []), signal]);
  }

  return (
    <section aria-label="Golden signals" className="space-y-2">
      <h2 className="text-sm font-semibold uppercase tracking-wide text-ink-muted">
        Golden signals
      </h2>
      <ul className="grid grid-cols-2 gap-2 md:grid-cols-3 lg:grid-cols-6">
        {FAMILIES.map((family) => {
          const states = byFamily.get(family) ?? [];
          const state = worst(states);
          const status = state?.status ?? "unknown";
          return (
            <li
              key={family}
              className={`rounded-card border px-3 py-2 ${STATUS_TONE[status] ?? STATUS_TONE.unknown}`}
            >
              <div className="text-[11px] uppercase tracking-wide opacity-80">
                {family}
              </div>
              <div className="text-sm font-semibold">{status}</div>
              {state ? (
                <div className="mt-1 space-y-0.5 text-[11px] opacity-90">
                  <div className="font-mono">{reading(state)}</div>
                  <div>
                    {state.region || "all regions"}
                    {states.length > 1 ? ` · worst of ${states.length}` : ""}
                  </div>
                  {state.streak > 0 && (
                    <div>
                      breaching {state.streak} eval{state.streak === 1 ? "" : "s"}
                    </div>
                  )}
                </div>
              ) : (
                <div className="mt-1 text-[11px] opacity-80">
                  not mapped for this application
                </div>
              )}
            </li>
          );
        })}
      </ul>
    </section>
  );
}

export function JourneyList({ detection }: { detection: Detection | null }) {
  const journeys = detection?.journeys ?? [];
  return (
    <section aria-label="Journeys" className="space-y-2">
      <h2 className="text-sm font-semibold uppercase tracking-wide text-ink-muted">
        Journeys
      </h2>
      {journeys.length === 0 ? (
        <p className="text-xs text-ink-muted">
          No journey has been probed yet. Journeys are declared during onboarding
          and measured by the probe runner.
        </p>
      ) : (
        <ul className="divide-y divide-line rounded-card border border-line bg-surface-raised text-sm">
          {journeys.map((journey, index) => (
            <li
              key={`${journey.journey_id ?? "unnamed"}-${journey.region}-${index}`}
              className="flex flex-wrap items-center gap-3 px-3 py-2"
            >
              <span className="min-w-40 flex-1 font-medium">
                {journey.journey_id ?? "unnamed journey"}
                {journey.tier !== null && (
                  <span className="ml-2 rounded-full border border-line px-2 py-0.5 text-[11px] text-ink-muted">
                    tier {journey.tier}
                  </span>
                )}
              </span>
              <span className="text-xs text-ink-muted">{journey.region}</span>
              <span className="text-xs">
                <span className="text-ok">{journey.passed} passed</span>
                {" · "}
                <span className={journey.failed ? "text-danger" : "text-ink-muted"}>
                  {journey.failed} failed
                </span>
                {/* Never folded into failures: "we could not check" and "it is
                    broken" lead a responder to opposite next actions. */}
                {journey.unknown > 0 && (
                  <>
                    {" · "}
                    <span className="text-warn">{journey.unknown} not checked</span>
                  </>
                )}
              </span>
              {journey.success_rate_pct !== null && (
                <span className="font-mono text-xs">
                  {journey.success_rate_pct.toFixed(1)}%
                </span>
              )}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
