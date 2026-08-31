"use client";

import { API_BASE_URL } from "@/lib/api/client";
import { useApiHealth } from "@/features/health/hooks/useApiHealth";

export function HealthPanel() {
  const { state, refresh } = useApiHealth();

  return (
    <section className="rounded-card border border-line bg-surface-raised p-6">
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold">API health</h2>
        <button
          onClick={refresh}
          className="rounded-card border border-line px-3 py-1 text-sm text-ink-muted hover:border-accent hover:text-ink"
        >
          Refresh
        </button>
      </div>

      <dl className="mt-4 space-y-2 text-sm">
        <Row label="Endpoint" value={`${API_BASE_URL}/health`} mono />
        {state.kind === "loading" && <Row label="Status" value="checking..." />}
        {state.kind === "ok" && (
          <>
            <Row label="Status" value={state.data.status} tone="ok" />
            <Row label="Service" value={`${state.data.service} v${state.data.version}`} />
            <Row label="Latency" value={`${state.latencyMs} ms`} />
            <Row label="Server time" value={state.data.time} mono />
          </>
        )}
        {state.kind === "down" && <Row label="Status" value={state.detail} tone="danger" />}
      </dl>
    </section>
  );
}

function Row({
  label,
  value,
  tone,
  mono,
}: {
  label: string;
  value: string;
  tone?: "ok" | "danger";
  mono?: boolean;
}) {
  const toneClass = tone === "ok" ? "text-ok" : tone === "danger" ? "text-danger" : "";
  return (
    <div className="flex gap-3">
      <dt className="w-28 shrink-0 text-ink-muted">{label}</dt>
      <dd className={`${toneClass} ${mono ? "font-mono text-xs leading-5" : ""}`}>{value}</dd>
    </div>
  );
}
