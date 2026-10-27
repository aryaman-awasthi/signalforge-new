"use client";

import { Detection, SignalState } from "@/features/incidents/api";
import { EmptyState } from "@/lib/ui/primitives";

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
 *
 * S8-T07 gave them the prototype's shape: a 3x2 grid, the family name as a small
 * uppercase label, and the value large enough to read from a doorway. The tile
 * ground stays neutral and only the *ink* and a top rule carry the state, which
 * is why six tiles side by side still read as one instrument panel rather than
 * six warnings.
 */

const FAMILIES = [
  "traffic",
  "errors",
  "latency",
  "saturation",
  "availability",
  "business",
] as const;

/** Only the reading is coloured. The tile itself never shouts.
 *
 * Keys are the detector's own `SignalStatus` values (`healthy`, `stale`,
 * `breaching`, `anomalous`, `unknown`). `stale` is a reading the poller has not
 * refreshed for a minute: not a breach, but not evidence of health either, so it
 * is muted rather than green. */
const STATUS_INK: Record<string, string> = {
  healthy: "text-ink",
  stale: "text-neutral-500",
  breaching: "text-warn-ink",
  anomalous: "text-accent-strong",
  unknown: "text-neutral-500",
};

const STATUS_RULE: Record<string, string> = {
  healthy: "",
  stale: "",
  breaching: "border-t-4 border-t-warn",
  anomalous: "border-t-4 border-t-accent",
  unknown: "",
};

/** The worst state among a family's regions. Ordered by how much it should
 * worry somebody, with `unknown` and `stale` deliberately above `healthy`. */
const SEVERITY_ORDER = ["healthy", "unknown", "stale", "breaching", "anomalous"];

function worst(states: SignalState[]): SignalState | null {
  if (states.length === 0) return null;
  return [...states].sort(
    (a, b) =>
      SEVERITY_ORDER.indexOf(b.status) - SEVERITY_ORDER.indexOf(a.status) ||
      b.deviation - a.deviation,
  )[0];
}

function value(state: SignalState): string {
  if (state.value === null) return "--";
  const magnitude = Math.abs(state.value);
  if (magnitude >= 1000) return `${(state.value / 1000).toFixed(1)}k`;
  if (magnitude >= 100) return state.value.toFixed(0);
  if (magnitude >= 1) return state.value.toFixed(1);
  return state.value.toFixed(3);
}

/**
 * There is no sparkline on these tiles, and the prototype has one.
 *
 * The prototype's is drawn from a time series. The detection route carries the
 * current reading per region, its baseline and its threshold - not a history -
 * so the only line this component could draw would be one point per *region*,
 * which renders as a trend and is not one. A chart that reads as "this metric is
 * rising" when it means "region-b is higher than region-a" is worse than no
 * chart. The tile shows the reading against its threshold instead, which is the
 * comparison the detector actually made, and the rollups route behind
 * `/apps/{id}/rollups` is where a history would come from if this earned one.
 */

export function GoldenTiles({ detection }: { detection: Detection | null }) {
  const byFamily = new Map<string, SignalState[]>();
  for (const signal of detection?.signals ?? []) {
    byFamily.set(signal.family, [...(byFamily.get(signal.family) ?? []), signal]);
  }

  return (
    <section aria-label="Golden signals" className="flex flex-col gap-2">
      <h2 className="text-h6 uppercase tracking-label text-ink-muted">Golden signals</h2>
      <ul className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
        {FAMILIES.map((family) => {
          const states = byFamily.get(family) ?? [];
          const state = worst(states);
          const status = state?.status ?? "unknown";
          return (
            <li
              key={family}
              className={`flex flex-col gap-1 bg-surface-raised px-4 py-3 ${STATUS_RULE[status] ?? ""}`}
            >
              <span className="text-tiny uppercase tracking-panel text-neutral-600">
                {family}
              </span>
              {state ? (
                <>
                  <span className="flex items-baseline gap-1.5">
                    <span
                      className={`font-display text-h2 font-extrabold leading-none tabular-nums ${STATUS_INK[status] ?? ""}`}
                    >
                      {value(state)}
                    </span>
                    {/* The status, not a unit. The detection route carries the
                        reading and its threshold but not the unit the operator
                        typed in the wizard, and inventing one here would be the
                        screen making a claim about what a number means. */}
                    <span className="text-small text-ink-muted">{status}</span>
                  </span>
                  <span className="mt-1 text-micro text-ink-muted">
                    {state.region || "all regions"}
                    {states.length > 1 ? ` · worst of ${states.length}` : ""}
                    {state.threshold !== null && (
                      <span className="font-mono">
                        {" · threshold "}
                        {state.threshold.toFixed(2)}
                      </span>
                    )}
                  </span>
                  {state.streak > 0 && (
                    <span className="text-micro font-semibold text-accent-strong">
                      breaching {state.streak} eval{state.streak === 1 ? "" : "s"}
                    </span>
                  )}
                </>
              ) : (
                <>
                  <span className="font-display text-h2 font-extrabold leading-none text-neutral-400">
                    --
                  </span>
                  <span className="text-micro text-ink-muted">
                    not mapped. Map a {family} signal in the wizard and this fills
                    from the application&rsquo;s own metrics.
                  </span>
                </>
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
    <section aria-label="Journeys" className="flex flex-col gap-2">
      <h2 className="text-h6 uppercase tracking-label text-ink-muted">Journeys</h2>
      {journeys.length === 0 ? (
        <EmptyState>
          No journey has been probed yet. Journeys are declared during onboarding
          and measured by the probe runner.
        </EmptyState>
      ) : (
        <table className="w-full text-dense">
          <thead>
            <tr>
              <th className="border-b-2 border-line px-2 py-2 text-left text-micro uppercase tracking-label text-ink-soft">
                Journey
              </th>
              <th className="border-b-2 border-line px-2 py-2 text-left text-micro uppercase tracking-label text-ink-soft">
                Region
              </th>
              <th className="border-b-2 border-line px-2 py-2 text-left text-micro uppercase tracking-label text-ink-soft">
                Passed
              </th>
              <th className="border-b-2 border-line px-2 py-2 text-left text-micro uppercase tracking-label text-ink-soft">
                Failed
              </th>
              <th className="border-b-2 border-line px-2 py-2 text-left text-micro uppercase tracking-label text-ink-soft">
                Not checked
              </th>
              <th className="border-b-2 border-line px-2 py-2 text-right text-micro uppercase tracking-label text-ink-soft">
                Success
              </th>
            </tr>
          </thead>
          <tbody>
            {journeys.map((journey, index) => (
              <tr
                key={`${journey.journey_id ?? "unnamed"}-${journey.region}-${index}`}
              >
                <td className="border-b border-line px-2 py-2">
                  <span className="font-semibold">
                    {journey.journey_id ?? "unnamed journey"}
                  </span>
                  {journey.tier !== null && (
                    <span className="ml-2 border border-line px-1.5 py-0.5 text-tiny text-ink-muted">
                      tier {journey.tier}
                    </span>
                  )}
                </td>
                <td className="border-b border-line px-2 py-2 font-mono text-small text-ink-muted">
                  {journey.region || "all"}
                </td>
                <td className="border-b border-line px-2 py-2 font-mono text-small text-ok">
                  {journey.passed}
                </td>
                <td
                  className={`border-b border-line px-2 py-2 font-mono text-small ${journey.failed ? "text-accent-strong" : "text-ink-muted"}`}
                >
                  {journey.failed}
                </td>
                {/* Never folded into failures: "we could not check" and "it is
                    broken" lead a responder to opposite next actions. */}
                <td
                  className={`border-b border-line px-2 py-2 font-mono text-small ${journey.unknown ? "text-warn-ink" : "text-ink-muted"}`}
                >
                  {journey.unknown}
                </td>
                <td className="border-b border-line px-2 py-2 text-right font-mono text-small tabular-nums">
                  {journey.success_rate_pct === null
                    ? "--"
                    : `${journey.success_rate_pct.toFixed(1)}%`}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}
