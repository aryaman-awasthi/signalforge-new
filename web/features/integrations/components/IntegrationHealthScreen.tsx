"use client";

import { useMemo, useState } from "react";

import { StatusPill } from "@/features/platform/components/StatusPill";
import { SimulatedBadge } from "@/features/integrations/components/SimulatedBadge";
import { useIntegrationHealth } from "@/features/integrations/hooks/useIntegrationHealth";
import type { ConnectorHealth, IngestKey } from "@/features/integrations/api";
import { freshness, relativeTime } from "@/lib/format";

/**
 * Screen 23 - integration health.
 *
 * One row per integration, and the columns are chosen so a row cannot lie about
 * its own direction. A pull integration is measured by when SignalForge last
 * read it. A push integration is measured by when something last *arrived*,
 * because SignalForge is the server for those and pinging itself would report a
 * dead sender as healthy. Both numbers are on the row and the direction column
 * says which one is the real one.
 *
 * Ingest keys that name no connector binding are integrations too - two of the
 * five frozen streams can never have a push adapter - so they get their own
 * rows rather than being invisible.
 */

type Row = {
  key: string;
  name: string;
  detail: string;
  statusDetail?: string | null;
  appId: string;
  direction: string;
  arrow: "in" | "out";
  status: ConnectorHealth["status"];
  lastPull: string;
  lastPush: string;
  freshnessLabel: string;
  errors: string | null;
  simulated: boolean;
  counters: string | null;
};

function connectorRow(row: ConnectorHealth): Row {
  const isPush = row.mode === "push";
  const isInvoke = row.mode === "invoke";
  return {
    key: `connector:${row.id}`,
    name: row.name,
    detail: `${row.adapter_name ?? row.adapter_id} · ${row.kind ?? "unknown kind"} · ${
      row.credential_scope
    }`,
    appId: row.app_id,
    direction: row.direction ?? "unknown",
    arrow: isInvoke ? "out" : "in",
    status: row.installed ? row.status : "down",
    lastPull: isPush ? "-" : relativeTime(row.last_ok_at),
    lastPush: isPush ? freshness(row.event_freshness_s) : "-",
    // The number the direction makes true. A push row that showed "checked 2s
    // ago" would be reporting that this server is up, which is not a question
    // anyone is asking on this screen.
    freshnessLabel: isPush
      ? freshness(row.event_freshness_s)
      : freshness(row.freshness_s),
    // An error and a status detail are different things and used to share this
    // column, so a perfectly healthy connector reported "reachable" in red. The
    // error is the error; the detail is what the last check actually said.
    errors: row.installed
      ? (isPush ? row.ingest_last_error : row.last_error) ?? null
      : `adapter "${row.adapter_id}" is no longer installed on this node`,
    statusDetail: row.status_detail,
    simulated: row.simulated,
    counters: isPush
      ? `${row.ingest_keys} key(s) · ${row.ingest_accepted} accepted · ${row.ingest_duplicates} duplicate · ${row.ingest_rejected} rejected`
      : row.consecutive_failures > 0
        ? `${row.consecutive_failures} consecutive failure(s)`
        : null,
  };
}

function keyRow(key: IngestKey): Row {
  return {
    key: `ingest:${key.id}`,
    name: key.name,
    detail: `ingest key ${key.token_prefix}… · ${key.stream} · ${key.path}`,
    appId: key.app_id,
    direction: "inbound (the app posts)",
    arrow: "in",
    // Nothing has arrived and nothing has been refused: SignalForge has no
    // evidence either way, and "unknown" is the honest word for that.
    status: key.last_event_at ? "up" : key.last_error ? "degraded" : "unknown",
    lastPull: "-",
    lastPush: freshness(key.freshness_s),
    freshnessLabel: freshness(key.freshness_s),
    errors: key.last_error,
    simulated: false,
    counters: `${key.accepted_count} accepted · ${key.duplicate_count} duplicate · ${key.rejected_count} rejected`,
  };
}

export function IntegrationHealthScreen({ appId }: { appId?: string }) {
  const { state, reload } = useIntegrationHealth(appId);
  const [onlyProblems, setOnlyProblems] = useState(false);

  const rows = useMemo(() => {
    if (state.kind !== "ready") return [];
    const bound = new Set(
      state.data.connectors.flatMap((connector) =>
        connector.ingest_keys > 0 ? [connector.id] : [],
      ),
    );
    const all = [
      ...state.data.connectors.map(connectorRow),
      // A key already counted on a binding row would otherwise be listed twice.
      ...state.data.keys
        .filter((key) => !key.binding_id || !bound.has(key.binding_id))
        .map(keyRow),
    ];
    return onlyProblems
      ? all.filter((row) => row.status === "down" || row.status === "degraded")
      : all;
  }, [state, onlyProblems]);

  if (state.kind === "loading") {
    return <p className="text-ink-muted">Loading integration health…</p>;
  }
  if (state.kind === "error") {
    return (
      <p className="rounded-card border border-danger/40 bg-danger/10 px-3 py-2 text-danger">
        {state.message}
      </p>
    );
  }

  const failing = rows.filter(
    (row) => row.status === "down" || row.status === "degraded",
  ).length;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <h1 className="text-2xl font-semibold">Integration health</h1>
        {failing > 0 && (
          <span className="border border-danger/40 bg-danger/10 px-2 py-0.5 text-xs text-danger">
            {failing} needing attention
          </span>
        )}
        <span className="flex-1" />
        <label className="flex items-center gap-2 text-xs text-ink-muted">
          <input
            type="checkbox"
            checked={onlyProblems}
            onChange={(event) => setOnlyProblems(event.target.checked)}
          />
          Problems only
        </label>
        <button
          onClick={() => void reload()}
          className="rounded-card border border-line px-2.5 py-1 text-xs hover:border-accent hover:text-accent-strong"
        >
          Refresh
        </button>
      </div>

      <p className="max-w-prose text-sm text-ink-muted">
        A pull integration is measured by when SignalForge last read it. A push
        integration is measured by when something last arrived - SignalForge is
        the server for those, so a reachability check would report a sender that
        died last week as healthy.
      </p>

      <div className="overflow-x-auto rounded-card border border-line bg-surface-raised">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-line text-left text-ink-muted">
              <th className="px-3 py-2 font-medium">Integration</th>
              <th className="px-3 py-2 font-medium">App</th>
              <th className="px-3 py-2 font-medium">Direction</th>
              <th className="px-3 py-2 font-medium">Status</th>
              <th className="px-3 py-2 font-medium">Last pull</th>
              <th className="px-3 py-2 font-medium">Last push</th>
              <th className="px-3 py-2 font-medium">Freshness</th>
              <th className="px-3 py-2 font-medium">Errors</th>
              <th className="px-3 py-2 font-medium">Simulated</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.key} className="border-b border-line/60 align-top">
                <td className="px-3 py-2">
                  <div className="font-medium">{row.name}</div>
                  <div className="text-xs text-ink-muted">{row.detail}</div>
                  {row.counters && (
                    <div className="text-xs text-ink-muted">{row.counters}</div>
                  )}
                </td>
                <td className="px-3 py-2 font-mono text-xs">{row.appId}</td>
                <td className="px-3 py-2 text-xs text-ink-muted">
                  <span className="mr-1 font-mono">
                    {row.arrow === "in" ? "→" : "←"}
                  </span>
                  {row.direction}
                </td>
                <td className="px-3 py-2">
                  <StatusPill status={row.status} />
                </td>
                <td className="px-3 py-2 text-xs">{row.lastPull}</td>
                <td className="px-3 py-2 text-xs">{row.lastPush}</td>
                <td className="px-3 py-2 text-xs">{row.freshnessLabel}</td>
                <td className="px-3 py-2 text-xs">
                  {row.errors ? (
                    <span className="text-accent-strong">{row.errors}</span>
                  ) : (
                    <span className="text-ink-muted">
                      {row.statusDetail || "—"}
                    </span>
                  )}
                </td>
                <td className="px-3 py-2">
                  <SimulatedBadge simulated={row.simulated} />
                </td>
              </tr>
            ))}
            {rows.length === 0 && (
              <tr>
                <td colSpan={9} className="px-3 py-6 text-ink-muted">
                  {onlyProblems
                    ? "Nothing is failing."
                    : "No integrations yet. Onboard an application to bind one."}
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
