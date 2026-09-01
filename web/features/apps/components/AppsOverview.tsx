"use client";

import Link from "next/link";
import { useState } from "react";

import { ReadinessBar } from "@/features/apps/components/ReadinessBar";
import { RegisterAppForm } from "@/features/apps/components/RegisterAppForm";
import { useApps } from "@/features/apps/hooks/useApps";
import { relativeTime } from "@/lib/format";

/**
 * Screen 4 - applications overview.
 *
 * One row per onboarded application with its readiness score, and a way in to
 * the wizard. Readiness is the API's number, not one the cockpit derives: the
 * thirteen weighted checks and the blocking rule live in the domain, so a
 * screen cannot accidentally call something ready that the platform would not.
 */
export function AppsOverview() {
  const { state, reload } = useApps();
  const [adding, setAdding] = useState(false);

  if (state.kind === "loading") {
    return <p className="text-ink-muted">Loading applications…</p>;
  }
  if (state.kind === "error") {
    return (
      <p className="rounded-card border border-danger/40 bg-danger/10 px-3 py-2 text-danger">
        {state.message}
      </p>
    );
  }

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center gap-3">
        <h1 className="text-2xl font-semibold">Applications</h1>
        <span className="text-xs text-ink-muted">
          {state.apps.length} onboarded · {state.apps.filter((a) => a.ready).length}{" "}
          ready
        </span>
        <span className="flex-1" />
        <Link
          href="/integration-health"
          className="rounded-card border border-line px-2.5 py-1 text-xs hover:border-accent hover:text-accent-strong"
        >
          Integration health
        </Link>
        <button
          onClick={() => setAdding((current) => !current)}
          className="rounded-card bg-accent px-3 py-1.5 text-sm font-medium text-surface-raised hover:bg-accent-strong"
        >
          {adding ? "Cancel" : "Onboard an application"}
        </button>
      </div>

      {adding && (
        <RegisterAppForm
          onRegistered={() => {
            setAdding(false);
            void reload();
          }}
        />
      )}

      <ul className="divide-y divide-line rounded-card border border-line bg-surface-raised">
        {state.apps.map((app) => (
          <li key={app.app_id} className="flex flex-wrap items-center gap-4 px-3 py-3">
            <div className="min-w-56 flex-1">
              <div className="flex items-center gap-2">
                <span className="font-medium">{app.name}</span>
                <span className="font-mono text-xs text-ink-muted">{app.app_id}</span>
                <span className="rounded-full border border-line px-2 py-0.5 text-[11px] uppercase text-ink-muted">
                  {app.tier.replace("_", " ")}
                </span>
                <span className="rounded-full border border-line px-2 py-0.5 text-[11px] uppercase text-ink-muted">
                  {app.environment}
                </span>
              </div>
              <p className="text-xs text-ink-muted">
                {app.base_url ?? "no base URL - auto-discovery cannot run"}
                {app.owner_team ? ` · ${app.owner_team}` : ""} · updated{" "}
                {relativeTime(app.updated_at)}
              </p>
            </div>
            <div className="w-48">
              <ReadinessBar score={app.readiness_score} ready={app.ready} threshold={80} />
            </div>
            <Link
              href={`/apps/${encodeURIComponent(app.app_id)}/onboard/1`}
              className="rounded-card border border-line px-2.5 py-1 text-xs hover:border-accent hover:text-accent-strong"
            >
              {app.ready ? "Review onboarding" : "Continue onboarding"}
            </Link>
          </li>
        ))}
        {state.apps.length === 0 && (
          <li className="px-3 py-6 text-sm text-ink-muted">
            No applications yet. Anything that serves the public observe contract -
            or posts to the ingest API - can be onboarded here; SignalForge needs
            no code of its own for it.
          </li>
        )}
      </ul>
    </div>
  );
}
