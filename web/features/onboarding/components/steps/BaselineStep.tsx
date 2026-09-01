"use client";

import { useState } from "react";

import { ApiError } from "@/lib/api/client";
import { BaselineRun, Profile, learnBaseline } from "@/features/apps/api";
import { relativeTime } from "@/lib/format";

/**
 * Step 6 - Baseline.
 *
 * A run takes N scrapes and learns what normal looks like per mapped signal.
 * Partial completion is the interesting case and it is designed, not accidental:
 * a run that reaches three of five signals keeps the three, reports the other
 * two by name with the reason, and does not erase what an earlier run learned
 * for them. The screen shows both halves, because a silently short baseline is
 * a detection bug three weeks later.
 */
export function BaselineStep({
  appId,
  profile,
  onChanged,
}: {
  appId: string;
  profile: Profile;
  onChanged: () => void;
}) {
  const [samples, setSamples] = useState(5);
  const [run, setRun] = useState<BaselineRun | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function learn() {
    setBusy(true);
    setError(null);
    try {
      setRun(await learnBaseline(appId, samples));
      onChanged();
    } catch (failure) {
      setError(
        failure instanceof ApiError
          ? failure.message
          : "Could not learn a baseline",
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end gap-3">
        <label className="space-y-1">
          <span className="text-[11px] text-ink-muted">samples</span>
          <input
            type="number"
            min={3}
            max={30}
            value={samples}
            onChange={(event) => setSamples(Number.parseInt(event.target.value, 10))}
            className="block w-24 rounded-card border border-line bg-surface px-2 py-1 text-sm"
          />
        </label>
        <button
          onClick={() => void learn()}
          disabled={busy || profile.signals.length === 0}
          className="rounded-card bg-accent px-3 py-2 text-sm font-medium text-surface-raised hover:bg-accent-strong disabled:opacity-60"
        >
          {busy ? "Learning…" : "Learn baselines"}
        </button>
        {profile.signals.length === 0 && (
          <span className="text-xs text-warn">
            Map at least one golden signal first - there is nothing to measure.
          </span>
        )}
      </div>

      <p className="max-w-prose text-xs text-ink-muted">
        One scrape answers every signal, so every measurement in a sample is taken
        at the same instant. p95 is nearest-rank, so a baseline holds only numbers
        that were actually observed.
      </p>

      {error && (
        <p className="rounded-card border border-danger/40 bg-danger/10 px-3 py-2 text-sm text-danger">
          {error}
        </p>
      )}

      {run && run.missed.length > 0 && (
        <div className="space-y-1 rounded-card border border-warn/40 bg-warn/10 p-3 text-xs text-warn">
          <p className="font-medium">
            Partially complete: {run.learned.length} learned, {run.missed.length}{" "}
            missed. What was learned is stored; anything learned earlier for the
            missed signals is untouched.
          </p>
          <ul className="space-y-0.5">
            {run.missed.map((miss) => (
              <li key={miss.signal_key}>
                <span className="font-mono">{miss.signal_key}</span>: {miss.reason}
              </li>
            ))}
          </ul>
        </div>
      )}

      <div className="overflow-x-auto rounded-card border border-line bg-surface-raised">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-line text-left text-ink-muted">
              <th className="px-3 py-2 font-medium">Signal</th>
              <th className="px-3 py-2 font-medium">Mean</th>
              <th className="px-3 py-2 font-medium">p95</th>
              <th className="px-3 py-2 font-medium">Min / max</th>
              <th className="px-3 py-2 font-medium">Samples</th>
              <th className="px-3 py-2 font-medium">Learned</th>
            </tr>
          </thead>
          <tbody>
            {profile.baselines.map((baseline) => (
              <tr key={baseline.signal_key} className="border-b border-line/60">
                <td className="px-3 py-2 font-mono text-xs">{baseline.signal_key}</td>
                <td className="px-3 py-2 font-mono text-xs">
                  {baseline.mean.toFixed(3)} ± {baseline.stddev.toFixed(3)}
                </td>
                <td className="px-3 py-2 font-mono text-xs">{baseline.p95.toFixed(3)}</td>
                <td className="px-3 py-2 font-mono text-xs">
                  {baseline.minimum.toFixed(3)} / {baseline.maximum.toFixed(3)}
                </td>
                <td className="px-3 py-2 text-xs">
                  {baseline.samples} over {baseline.window_s.toFixed(0)}s ·{" "}
                  {baseline.series_count} series
                </td>
                <td className="px-3 py-2 text-xs text-ink-muted">
                  {relativeTime(baseline.learned_at)}
                </td>
              </tr>
            ))}
            {profile.baselines.length === 0 && (
              <tr>
                <td colSpan={6} className="px-3 py-6 text-ink-muted">
                  No baselines yet. Anomaly detection compares against these, so
                  until they exist there is nothing to be anomalous against.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
