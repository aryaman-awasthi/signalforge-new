"use client";

import { StatusPill } from "@/features/platform/components/StatusPill";
import type { PlatformStatus } from "@/features/platform/api";

function relative(iso: string | null): string {
  if (!iso) return "never";
  const seconds = Math.round((Date.now() - new Date(iso).getTime()) / 1000);
  if (!Number.isFinite(seconds)) return iso;
  if (seconds < 60) return `${Math.max(seconds, 0)}s ago`;
  if (seconds < 3600) return `${Math.round(seconds / 60)}m ago`;
  return `${Math.round(seconds / 3600)}h ago`;
}

export function PlatformHealthPanel({ status }: { status: PlatformStatus }) {
  return (
    <div className="space-y-6">
      <section className="space-y-2">
        <div className="flex items-center gap-3">
          <h2 className="text-lg font-medium">Platform health</h2>
          <StatusPill status={status.status} />
          <span className="text-xs text-ink-muted">
            v{status.version} · checked {relative(status.checked_at)}
          </span>
        </div>
        <ul className="divide-y divide-line rounded-card border border-line bg-surface-raised">
          {status.components.map((component) => (
            <li
              key={component.component}
              className="flex items-center gap-3 px-3 py-2 text-sm"
            >
              <span className="w-32 shrink-0 font-medium">{component.component}</span>
              <StatusPill status={component.status} />
              <span className="min-w-0 flex-1 truncate text-ink-muted">
                {component.detail}
              </span>
            </li>
          ))}
        </ul>
      </section>

      <section className="grid gap-4 sm:grid-cols-2">
        <div className="rounded-card border border-line bg-surface-raised p-3">
          <h3 className="mb-2 text-sm font-medium">Event bus</h3>
          <dl className="space-y-1 text-sm text-ink-muted">
            <div className="flex justify-between">
              <dt>Head sequence</dt>
              <dd className="font-mono text-ink">#{status.bus.head_seq}</dd>
            </div>
            {Object.entries(status.bus.groups).map(([group, lag]) => (
              <div key={group} className="flex justify-between">
                <dt>Lag · {group}</dt>
                <dd className={`font-mono ${lag > 0 ? "text-warn" : "text-ink"}`}>{lag}</dd>
              </div>
            ))}
            {Object.entries(status.bus.dead_letters).map(([state, count]) => (
              <div key={state} className="flex justify-between">
                <dt>Dead letters · {state}</dt>
                <dd
                  className={`font-mono ${
                    state === "dead" && count > 0 ? "text-danger" : "text-ink"
                  }`}
                >
                  {count}
                </dd>
              </div>
            ))}
          </dl>
        </div>

        <div className="rounded-card border border-line bg-surface-raised p-3">
          <h3 className="mb-2 text-sm font-medium">
            Clock
            {status.clock.source === "virtual" && (
              <span className="ml-2 border border-warn/40 bg-warn/10 px-1.5 py-0.5 text-[10px] uppercase text-warn">
                drill clock
              </span>
            )}
          </h3>
          <dl className="space-y-1 text-sm text-ink-muted">
            <div className="flex justify-between">
              <dt>Source</dt>
              <dd className="text-ink">{status.clock.source}</dd>
            </div>
            <div className="flex justify-between">
              <dt>Now</dt>
              <dd className="font-mono text-xs text-ink">{status.clock.now}</dd>
            </div>
            <div className="flex justify-between">
              <dt>Speed</dt>
              <dd className="font-mono text-ink">
                {status.clock.speed}×{status.clock.paused ? " (paused)" : ""}
              </dd>
            </div>
          </dl>
        </div>
      </section>

      <section>
        <h3 className="mb-2 text-sm font-medium">
          Worker jobs {status.worker.running ? "" : "· not running"}
        </h3>
        <ul className="divide-y divide-line rounded-card border border-line bg-surface-raised">
          {status.worker.jobs.map((job) => (
            <li key={job.name} className="flex items-center gap-3 px-3 py-2 text-sm">
              <span className="w-40 shrink-0 font-medium">{job.name}</span>
              <StatusPill status={job.healthy ? "up" : "degraded"} />
              <span className="text-xs text-ink-muted">every {job.interval_s}s</span>
              <span className="text-xs text-ink-muted">
                {job.runs} runs · {job.failures} failures · {relative(job.last_run_at)}
              </span>
              {job.last_error && (
                <span className="min-w-0 flex-1 truncate text-xs text-danger">
                  {job.last_error}
                </span>
              )}
            </li>
          ))}
        </ul>
      </section>

      {status.retention.length > 0 && (
        <section>
          <h3 className="mb-2 text-sm font-medium">Retention</h3>
          <ul className="divide-y divide-line rounded-card border border-line bg-surface-raised text-sm">
            {status.retention.map((row) => (
              <li key={row.plane} className="flex items-center gap-3 px-3 py-2">
                <span className="w-24 font-medium">{row.plane} plane</span>
                <span className="text-ink-muted">
                  {row.retention_days}-day window · {row.pruned_count} events aged out
                  through #{row.pruned_through_seq} · last {relative(row.pruned_at)}
                </span>
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}
