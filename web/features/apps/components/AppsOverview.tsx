"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useEffect, useState } from "react";

import { fetchMe } from "@/features/auth/api";
import { ReadinessBar } from "@/features/apps/components/ReadinessBar";
import { RegisterAppForm } from "@/features/apps/components/RegisterAppForm";
import { StanceChip } from "@/features/apps/components/StanceChip";
import { useApps } from "@/features/apps/hooks/useApps";
import { relativeTime } from "@/lib/format";

/**
 * Screen 4 - applications overview.
 *
 * One row per application the signed-in person's bindings cover, with its
 * readiness score and a way in. Readiness is the API's number, not one the
 * cockpit derives: the thirteen weighted checks and the blocking rule live in
 * the domain, so a screen cannot accidentally call something ready that the
 * platform would not.
 *
 * Every role can open this page (S10). The onboarding controls - register, the
 * wizard - are shown only to somebody holding `manage_apps`, because a button
 * that only ever answers 403 teaches people the product is broken; the "Open"
 * button is everybody's, because the application page is where an incident is
 * declared from and where a paged responder lands.
 */

const DENIED: Record<string, string> = {
  setup: "Organisation setup",
  settings: "Platform settings",
  "integration-health": "Integration health",
  onboard: "The onboarding wizard",
};

export function AppsOverview() {
  const { state, reload } = useApps();
  const params = useSearchParams();
  const denied = params.get("denied");
  const [adding, setAdding] = useState(false);
  const [canManage, setCanManage] = useState<boolean | null>(null);

  useEffect(() => {
    void fetchMe()
      .then((who) => setCanManage(who.permissions.includes("manage_apps")))
      .catch(() => setCanManage(false));
  }, []);

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
      {denied && (
        <p
          role="status"
          className="border border-warn bg-warn-bg px-3 py-2 text-dense text-warn-ink"
        >
          <strong className="font-semibold">
            {DENIED[denied] ?? "That page"} is for the organisation owner.
          </strong>{" "}
          Everything your role covers is here: open an application to see its
          health, declare an incident, or join a war room.
        </p>
      )}

      <div className="flex flex-wrap items-center gap-3">
        <h1 className="text-2xl font-semibold">Applications</h1>
        <span className="text-xs text-ink-muted">
          {state.apps.length} onboarded · {state.apps.filter((a) => a.ready).length}{" "}
          ready
        </span>
        <span className="flex-1" />
        {canManage && (
          <>
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
          </>
        )}
      </div>

      {adding && canManage && (
        <RegisterAppForm
          onRegistered={() => {
            setAdding(false);
            void reload();
          }}
        />
      )}

      {/* The prototype's card grid, with the dashed "add" card as a peer of the
          applications rather than a button somewhere else - an estate with one
          application in it should look like an estate with room for more. */}
      <ul className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {state.apps.map((app) => (
          <li key={app.app_id} className="flex flex-col gap-3 bg-surface-raised p-4">
            <div className="flex items-start justify-between gap-2">
              <h2 className="text-h4">{app.name}</h2>
              <div className="flex shrink-0 flex-wrap justify-end gap-1.5">
                {/* What the platform believes about it right now, beside how
                    far its onboarding got. The two answer different questions
                    and an estate screen needs both. */}
                {app.ready && <StanceChip appId={app.app_id} />}
                <span
                  className={`shrink-0 px-2 py-1 text-tiny font-semibold uppercase tracking-label ${
                    app.ready
                      ? "border border-ok bg-ok-bg text-ok"
                      : "border border-warn bg-warn-bg text-warn-ink"
                  }`}
                >
                  {app.ready ? "ready" : "onboarding"}
                </span>
              </div>
            </div>

            <div className="flex items-baseline gap-2">
              <span className="font-display text-hero font-extrabold leading-none tabular-nums">
                {app.readiness_score}
              </span>
              <span className="text-small text-ink-muted">readiness</span>
            </div>
            <ReadinessBar score={app.readiness_score} ready={app.ready} threshold={80} />

            <dl className="flex flex-col gap-1 border-t border-line pt-2 text-small">
              <div className="flex justify-between gap-2">
                <dt className="text-ink-muted">Identifier</dt>
                <dd className="font-mono">{app.app_id}</dd>
              </div>
              <div className="flex justify-between gap-2">
                <dt className="text-ink-muted">Tier</dt>
                <dd className="font-mono">
                  {app.tier.replace("_", " ")} · {app.environment}
                </dd>
              </div>
              <div className="flex justify-between gap-2">
                <dt className="text-ink-muted">Updated</dt>
                <dd className="font-mono">{relativeTime(app.updated_at)}</dd>
              </div>
            </dl>

            <p className="truncate text-micro text-ink-muted">
              {app.base_url ?? "no base URL - auto-discovery cannot run"}
              {app.owner_team ? ` · ${app.owner_team}` : ""}
            </p>

            <div className="mt-auto flex flex-wrap gap-2 pt-1">
              <Link
                href={`/apps/${encodeURIComponent(app.app_id)}`}
                className="bg-accent px-3 py-1.5 text-small font-semibold text-surface hover:bg-brand-600"
              >
                Open
              </Link>
              {canManage && (
                <Link
                  href={`/apps/${encodeURIComponent(app.app_id)}/onboard/1`}
                  className="border border-line px-3 py-1.5 text-small hover:bg-ink/[0.07]"
                >
                  {app.ready ? "Review onboarding" : "Continue onboarding"}
                </Link>
              )}
            </div>
          </li>
        ))}

        {canManage && (
          <li>
            <button
              type="button"
              onClick={() => setAdding((current) => !current)}
              className="flex h-full min-h-[220px] w-full items-center justify-center border border-dashed border-line p-4 text-dense font-semibold text-accent hover:bg-brand-100"
            >
              + Onboard an application
            </button>
          </li>
        )}
      </ul>

      {state.apps.length === 0 && (
        <p className="text-dense text-ink-muted">
          {canManage
            ? "No applications yet. Anything that serves the public observe contract - or posts to the ingest API - can be onboarded here; SignalForge needs no code of its own for it."
            : "No application is bound to your role yet. The organisation owner invites people per application, or org-wide; ask them to widen yours."}
        </p>
      )}
    </div>
  );
}
