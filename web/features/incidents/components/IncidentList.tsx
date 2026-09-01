"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { ApiError } from "@/lib/api/client";
import { Incident, listIncidents } from "@/features/incidents/api";

/**
 * The tenant's incidents.
 *
 * Readable by every member of the tenant and behind no permission, because the
 * frozen roles matrix gives the SME - the role that answers a page - none. A
 * list an SME could not open would be a list that excluded the people an
 * incident exists to reach.
 *
 * Open incidents first, then closed, because during an incident nobody is
 * scrolling for history.
 */
export function IncidentList() {
  const [state, setState] = useState<
    | { kind: "loading" }
    | { kind: "ready"; incidents: Incident[] }
    | { kind: "error"; message: string }
  >({ kind: "loading" });

  useEffect(() => {
    let live = true;
    async function load() {
      try {
        const listing = await listIncidents();
        if (live) setState({ kind: "ready", incidents: listing.incidents });
      } catch (error) {
        if (live) {
          setState({
            kind: "error",
            message:
              error instanceof ApiError ? error.message : "Could not load incidents",
          });
        }
      }
    }
    void load();
    const handle = setInterval(() => void load(), 10000);
    return () => {
      live = false;
      clearInterval(handle);
    };
  }, []);

  if (state.kind === "loading") return <p className="text-ink-muted">Loading…</p>;
  if (state.kind === "error") {
    return (
      <p className="rounded-card border border-danger/40 bg-danger/10 px-3 py-2 text-danger">
        {state.message}
      </p>
    );
  }

  const open = state.incidents.filter((incident) => incident.open);
  const closed = state.incidents.filter((incident) => !incident.open);

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <h1 className="text-2xl font-semibold">Incidents</h1>
        <span className="text-xs text-ink-muted">
          {open.length} open · {closed.length} closed
        </span>
        <span className="flex-1" />
        <Link
          href="/my/pages"
          className="rounded-card border border-line px-2.5 py-1 text-xs hover:border-accent hover:text-accent-strong"
        >
          Your pages
        </Link>
      </div>

      {state.incidents.length === 0 ? (
        <p className="text-sm text-ink-muted">
          No incident has been declared. Detection reaches a proposal and stops
          there; an incident exists because a person declared one.
        </p>
      ) : (
        <ul className="divide-y divide-line rounded-card border border-line bg-surface-raised">
          {[...open, ...closed].map((incident) => (
            <li key={incident.id} className="flex flex-wrap items-center gap-3 px-3 py-2.5">
              <span className="font-mono text-sm font-semibold">
                {incident.severity}
              </span>
              <Link
                href={`/incidents/${incident.id}`}
                className="min-w-40 flex-1 text-sm font-medium hover:text-accent-strong"
              >
                {incident.title}
              </Link>
              <span className="font-mono text-xs text-ink-muted">
                {incident.reference}
              </span>
              <span className="text-xs text-ink-muted">{incident.app_id}</span>
              <span
                className={`rounded-full border px-2 py-0.5 text-xs ${
                  incident.state === "active"
                    ? "border-danger/40 bg-danger/10 text-danger"
                    : incident.state === "recovering"
                      ? "border-warn/40 bg-warn/10 text-warn"
                      : "border-line text-ink-muted"
                }`}
              >
                {incident.state}
              </span>
              <span className="text-xs text-ink-muted">
                by {incident.declared_by_display || incident.declared_by}
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
