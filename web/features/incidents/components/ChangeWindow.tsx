"use client";

import { ChangeWindowView } from "@/features/incidents/api";

/**
 * S5 - the "Change window open" chip, and the recent-changes list beneath it.
 *
 * The chip is lit by `chip` from the API, not by a count the browser derives.
 * The distinction matters because a window that has been open for days is *not*
 * a change in flight - the pipeline died between `started` and `completed` and
 * nobody knows whether the deploy landed. The platform marks those `stale` and
 * takes the chip out, because a chip that stays lit for three days is a chip
 * people stop seeing, and then the one that matters is missed too.
 *
 * A stale window is still listed. It is not closed on a guess: "started 3.1h ago
 * and never announced completion" is a fact somebody may need during an
 * incident, and hiding it would be worse than leaving the chip lit.
 */

function hours(seconds: number | null): string {
  if (seconds === null) return "";
  if (seconds < 90) return `${Math.round(seconds)}s ago`;
  if (seconds < 5400) return `${Math.round(seconds / 60)}m ago`;
  return `${(seconds / 3600).toFixed(1)}h ago`;
}

export function ChangeWindowChip({ view }: { view: ChangeWindowView | null }) {
  if (!view || !view.chip) return null;
  return (
    <span
      className="rounded-full border border-warn/50 bg-warn/10 px-2.5 py-1 text-xs font-medium text-warn"
      title="A deployment was announced as started and never announced as completed"
    >
      Change window open
      {view.open_count > 1 ? ` · ${view.open_count}` : ""}
    </span>
  );
}

export function RecentChanges({ view }: { view: ChangeWindowView | null }) {
  const windows = view?.windows ?? [];
  return (
    <section aria-label="Recent changes" className="space-y-2">
      <h2 className="text-sm font-semibold uppercase tracking-wide text-ink-muted">
        Recent changes
      </h2>
      {windows.length === 0 ? (
        <p className="text-xs text-ink-muted">
          No deployment has been announced for this application. Changes arrive by
          push, from the deployment pipeline&apos;s own hook.
        </p>
      ) : (
        <ul className="divide-y divide-line rounded-card border border-line bg-surface-raised text-sm">
          {windows.map((change) => (
            <li key={change.change_id} className="flex flex-wrap items-center gap-3 px-3 py-2">
              <span className="min-w-40 flex-1">
                <span className="font-medium">{change.service || change.change_id}</span>
                {change.version && (
                  <span className="ml-2 font-mono text-xs text-ink-muted">
                    {change.version}
                  </span>
                )}
                {change.simulated && (
                  <span className="ml-2 rounded-full border border-line px-2 py-0.5 text-[10px] uppercase text-ink-muted">
                    simulated
                  </span>
                )}
              </span>
              <span className="text-xs text-ink-muted">{change.change_type}</span>
              {change.open ? (
                change.stale ? (
                  <span
                    className="rounded-full border border-line px-2 py-0.5 text-xs text-ink-muted"
                    title="Open past the platform's ceiling. Not closed on a guess - nobody knows whether the deploy landed."
                  >
                    started {hours(change.age_s)}, never announced completion
                  </span>
                ) : (
                  <span className="rounded-full border border-warn/50 bg-warn/10 px-2 py-0.5 text-xs text-warn">
                    in flight · started {hours(change.age_s)}
                  </span>
                )
              ) : (
                <span
                  className={`rounded-full border px-2 py-0.5 text-xs ${
                    change.status === "rolled_back" || change.status === "failed"
                      ? "border-danger/40 bg-danger/10 text-danger"
                      : "border-ok/40 bg-ok/10 text-ok"
                  }`}
                >
                  {/* Which of the three endings it was, not just "closed": a
                      rolled-back deployment is more interesting as evidence. */}
                  {change.status}
                </span>
              )}
              {change.actor && (
                <span className="text-xs text-ink-muted">by {change.actor}</span>
              )}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
