"use client";

import Link from "next/link";

import {
  ChangeWindowChip,
  RecentChanges,
} from "@/features/incidents/components/ChangeWindow";
import { GoldenTiles, JourneyList } from "@/features/incidents/components/GoldenTiles";
import { HealthPanel } from "@/features/incidents/components/HealthPanel";
import { ProposalSheet } from "@/features/incidents/components/ProposalSheet";
import { WatchBanner } from "@/features/incidents/components/WatchBanner";
import { useLifecycle } from "@/features/incidents/hooks/useLifecycle";

/**
 * Screen 5 - application detail.
 *
 * The header answers one question before anything else: is this a *stance* the
 * detector recomputed five seconds ago, or a *decision* somebody made? The same
 * six words name both halves of the lifecycle, and the screen says which it is
 * reading rather than leaving a reader to infer it from the colour. That is the
 * single most important thing on this page: acting on "the AI says P0" and
 * acting on "Ida declared a P0" are different acts.
 *
 * Everything below is the platform's own numbers. The health score, the
 * corroboration verdict and the severity proposal are all computed in the
 * domain; nothing here recomputes any of them, because a second implementation
 * of the weighting would eventually disagree with the first during an incident.
 */

const STATE_TONE: Record<string, string> = {
  steady: "border-ok/40 bg-ok/10 text-ok",
  watch: "border-line bg-surface text-ink-muted",
  proposed: "border-accent/50 bg-accent/10 text-accent-strong",
  active: "border-danger/40 bg-danger/10 text-danger",
  recovering: "border-warn/40 bg-warn/10 text-warn",
  closed: "border-line bg-surface text-ink-muted",
};

export function AppDetail({ appId }: { appId: string }) {
  const { state, reload } = useLifecycle(appId);

  if (state.kind === "loading") {
    return <p className="text-ink-muted">Loading {appId}…</p>;
  }
  if (state.kind === "error") {
    return (
      <p className="rounded-card border border-danger/40 bg-danger/10 px-3 py-2 text-danger">
        {state.message}
      </p>
    );
  }

  const { lifecycle, detection, detectionError } = state;
  const incident = lifecycle.incident;

  return (
    <div className="space-y-5">
      <header className="flex flex-wrap items-center gap-3">
        <h1 className="text-2xl font-semibold">{appId}</h1>
        <span
          className={`rounded-full border px-2.5 py-1 text-xs font-medium ${
            STATE_TONE[lifecycle.state] ?? STATE_TONE.steady
          }`}
        >
          {lifecycle.state}
        </span>
        {/* The honest half. A stance is derived and moves on its own; an
            incident is a decision and does not. */}
        <span className="text-xs text-ink-muted">
          {lifecycle.source === "incident"
            ? "declared by a person"
            : "a detection stance, recomputed every 5s"}
        </span>
        <ChangeWindowChip view={lifecycle.change_window} />
        <span className="flex-1" />
        <Link
          href={`/apps/${encodeURIComponent(appId)}/onboard/connect`}
          className="rounded-card border border-line px-2.5 py-1 text-xs hover:border-accent hover:text-accent-strong"
        >
          Onboarding
        </Link>
        {/* S6-T06. Beside Onboarding rather than inside it: what this
            application publishes is a fact the wizard imports, and what the
            platform will let anyone do with it is a decision somebody makes
            afterwards and changes again during an incident. */}
        <Link
          href={`/apps/${encodeURIComponent(appId)}/policy`}
          className="rounded-card border border-line px-2.5 py-1 text-xs hover:border-accent hover:text-accent-strong"
        >
          Execution policy
        </Link>
        {incident && (
          <Link
            href={`/incidents/${incident.id}`}
            className="rounded-card bg-danger px-3 py-1.5 text-sm font-medium text-surface-raised"
          >
            Open war room · {incident.reference}
          </Link>
        )}
      </header>

      {incident && (
        <section
          aria-label="Open incident"
          className="rounded-card border border-danger/40 bg-danger/5 px-3 py-2"
        >
          <div className="flex flex-wrap items-center gap-2 text-sm">
            <span className="font-mono font-semibold">{incident.severity}</span>
            <span className="font-medium">{incident.title}</span>
            <span className="text-xs text-ink-muted">
              {incident.reference} · declared by{" "}
              {incident.declared_by_display || incident.declared_by}
            </span>
            {incident.overrode_detection && (
              <span className="rounded-full border border-line px-2 py-0.5 text-[11px] text-ink-muted">
                severity chosen, not proposed
              </span>
            )}
          </div>
        </section>
      )}

      <WatchBanner lifecycle={lifecycle} detection={detection} />
      <ProposalSheet
        appId={appId}
        lifecycle={lifecycle}
        detection={detection}
        onDeclared={reload}
      />

      <GoldenTiles detection={detection} />

      <div className="grid gap-5 lg:grid-cols-2">
        <HealthPanel
          appId={appId}
          detection={detection}
          detectionError={detectionError}
        />
        <div className="space-y-5">
          <JourneyList detection={detection} />
          <RecentChanges view={lifecycle.change_window} />
        </div>
      </div>
    </div>
  );
}
