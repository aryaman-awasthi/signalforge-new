"use client";

import { Detection, Lifecycle } from "@/features/incidents/api";

/**
 * S6 - the Watch banner, and the quiet "AI is checking" list.
 *
 * **Quiet** is the requirement, not a stylistic note. `watch` means something is
 * being observed and nothing has been decided: no corroboration, no severity, no
 * incident. A banner that shouted at this stage would train people to ignore the
 * one that shouts when it matters - so this is a low-contrast strip with no
 * colour of alarm, no count-up, and no button that could be mistaken for a
 * decision.
 *
 * What it does show is *what* is being checked, by name. "The AI is looking into
 * it" with no list is the thing operators most reasonably distrust; naming the
 * signals under observation is what makes the quiet version credible.
 */
export function WatchBanner({
  lifecycle,
  detection,
}: {
  lifecycle: Lifecycle;
  detection: Detection | null;
}) {
  if (lifecycle.source !== "detection" || lifecycle.state !== "watch") return null;

  const checking = (detection?.signals ?? []).filter(
    (signal) => signal.status === "breaching" || signal.status === "anomalous",
  );
  const burning = (detection?.burns ?? []).filter(
    (burn) => burn.verdict !== "none",
  );

  return (
    <section
      aria-label="AI is checking"
      className="rounded-card border border-line bg-surface px-3 py-2.5"
    >
      <div className="flex flex-wrap items-center gap-2">
        <span className="h-1.5 w-1.5 bg-ink-muted" aria-hidden />
        <span className="text-sm font-medium text-ink-muted">AI is checking</span>
        <span className="text-xs text-ink-muted">
          Something is being observed. Nothing has been decided, and no incident
          exists.
        </span>
      </div>

      {checking.length > 0 && (
        <ul className="mt-2 space-y-1 text-xs text-ink-muted">
          {checking.map((signal) => (
            <li key={`${signal.signal_key}-${signal.region}`} className="flex flex-wrap gap-2">
              <span className="font-mono">{signal.signal_key}</span>
              <span>{signal.region}</span>
              <span>
                {signal.status} for {signal.streak} eval
                {signal.streak === 1 ? "" : "s"}
              </span>
              {signal.detail && <span className="opacity-80">{signal.detail}</span>}
            </li>
          ))}
        </ul>
      )}

      {burning.map((burn) => (
        <p key={burn.slo_id} className="mt-1 text-xs text-ink-muted">
          {burn.name}: {burn.verdict} burn against a {burn.objective_pct}% objective.
          {burn.detail ? ` ${burn.detail}` : ""}
        </p>
      ))}

      {checking.length === 0 && burning.length === 0 && (
        <p className="mt-1 text-xs text-ink-muted">
          {lifecycle.reasons[0] ??
            "A signal is being watched. The list will name it once it has been evaluated again."}
        </p>
      )}
    </section>
  );
}
