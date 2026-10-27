"use client";

import { useState } from "react";

import { Detection } from "@/features/incidents/api";

/**
 * S5 - the health panel, with a SignalForge / Grafana toggle and a fallback.
 *
 * The fallback is the point of the component. An embedded Grafana is somebody
 * else's server, on somebody else's port, behind somebody else's session - it
 * can be down, unconfigured, or refusing to be framed by `X-Frame-Options`, and
 * a browser gives an iframe no usable error for any of those. So the panel never
 * pretends: the Grafana tab always renders the reason it might be blank *beside*
 * the frame, and one click returns to the SignalForge view, which is computed
 * from data this platform holds and cannot fail independently.
 *
 * The SignalForge view is the default for the same reason. During an incident,
 * a panel that is empty because of a frame policy is indistinguishable from a
 * panel that is empty because the metrics stopped - and those need opposite
 * responses.
 */

const GRAFANA_BASE =
  process.env.NEXT_PUBLIC_GRAFANA_URL ?? "http://localhost:3000";

// Keys are the domain's `HealthBand` values; `unhealthy` is the worst band the
// scorer emits, there is no `critical`.
const BAND_TONE: Record<string, string> = {
  healthy: "text-ok",
  degraded: "text-warn",
  unhealthy: "text-danger",
  unknown: "text-ink-muted",
};

export type HealthView = "signalforge" | "grafana";

export function HealthPanel({
  appId,
  detection,
  detectionError,
}: {
  appId: string;
  detection: Detection | null;
  detectionError: string;
}) {
  const [view, setView] = useState<HealthView>("signalforge");
  const health = detection?.health;

  return (
    <section aria-label="Health score and metrics" className="space-y-2">
      <div className="flex flex-wrap items-center gap-2">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-ink-muted">
          Health Score and Metrics
        </h2>
        <span className="flex-1" />
        <div className="flex overflow-hidden rounded-card border border-line text-xs">
          {(["signalforge", "grafana"] as const).map((option) => (
            <button
              key={option}
              type="button"
              onClick={() => setView(option)}
              aria-pressed={view === option}
              className={
                view === option
                  ? "bg-accent px-2.5 py-1 font-medium text-surface-raised"
                  : "px-2.5 py-1 text-ink-muted hover:text-accent-strong"
              }
            >
              {option === "signalforge" ? "SignalForge" : "Grafana"}
            </button>
          ))}
        </div>
      </div>

      {view === "signalforge" ? (
        <div className="rounded-card border border-line bg-surface-raised p-3">
          {detectionError ? (
            <p className="text-sm text-warn">
              The detector could not be read: {detectionError}. Nothing below is
              current, and an incident can still be declared - the moment the
              detector is broken is exactly when somebody may need to.
            </p>
          ) : health ? (
            <>
              <div className="flex flex-wrap items-baseline gap-3">
                <span className="font-mono text-3xl font-semibold">
                  {health.score === null ? "--" : health.score.toFixed(0)}
                </span>
                <span
                  className={`text-sm font-medium ${BAND_TONE[health.band] ?? BAND_TONE.unknown}`}
                >
                  {health.band}
                </span>
                {health.worst_region && (
                  <span className="text-xs text-ink-muted">
                    worst region: {health.worst_region}
                  </span>
                )}
                <span className="text-xs text-ink-muted">
                  {health.covered_weight} of 100 weight covered
                </span>
              </div>
              {health.covered_weight < 100 && (
                <p className="mt-1 text-xs text-warn">
                  The score is renormalised over the weight actually covered.
                  Signals that are not mapped are not counted as healthy.
                </p>
              )}
              <ul className="mt-3 space-y-2">
                {health.regions.map((region) => (
                  <li key={region.region} className="rounded-card border border-line p-2">
                    <div className="flex items-center gap-2 text-sm">
                      <span className="font-medium">{region.region || "all"}</span>
                      <span className={BAND_TONE[region.band] ?? BAND_TONE.unknown}>
                        {region.score === null ? "--" : region.score.toFixed(0)} ·{" "}
                        {region.band}
                      </span>
                    </div>
                    <ul className="mt-1 flex flex-wrap gap-2 text-[11px] text-ink-muted">
                      {region.families.map((family) => (
                        <li
                          key={family.family}
                          className="border border-line px-2 py-0.5"
                        >
                          {family.family} {family.weight}
                          {family.fraction === null
                            ? " · not covered"
                            : ` · ${(family.fraction * 100).toFixed(0)}%`}
                        </li>
                      ))}
                    </ul>
                  </li>
                ))}
              </ul>
            </>
          ) : (
            <p className="text-sm text-ink-muted">
              No health score yet. Detection needs a bound metrics connector and
              at least one evaluation.
            </p>
          )}
        </div>
      ) : (
        <div className="space-y-2">
          {/* Always rendered, never conditionally: a browser gives an iframe no
              usable error, so the only honest thing is to say in advance what a
              blank frame would mean. */}
          <p className="rounded-card border border-warn/40 bg-warn/10 px-3 py-2 text-xs text-warn">
            Grafana is a separate system. If this frame is blank, it is down, not
            configured at {GRAFANA_BASE}, or refusing to be embedded by its own
            frame policy - a browser cannot tell those apart, and neither can this
            screen. The SignalForge view is computed from data this platform holds
            and does not depend on it.
          </p>
          <iframe
            title="Grafana dashboard"
            src={`${GRAFANA_BASE}/d/${encodeURIComponent(appId)}?kiosk&theme=light`}
            className="h-96 w-full rounded-card border border-line bg-surface-raised"
            sandbox="allow-scripts allow-same-origin"
          />
        </div>
      )}
    </section>
  );
}
