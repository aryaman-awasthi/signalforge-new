"use client";

import { useState } from "react";

import { SimulatedBadge } from "@/features/integrations/components/SimulatedBadge";
import { useRegistry } from "@/features/integrations/hooks/useRegistry";
import type { Adapter } from "@/features/integrations/api";

/**
 * S21, adapter registry section. What integrations this installation has, what
 * each one can do, and - crucially - which manifests refused to load.
 *
 * A rejected manifest is shown with its path and its reason rather than being
 * dropped: a silently short registry is how a deployment ends up wondering
 * where its integration went. Interface-only adapters read "available" and are
 * never counted as coverage, so the matrix cannot flatter itself.
 */

const STATUS_TONE: Record<string, string> = {
  installed: "border-ok/40 bg-ok/10 text-ok",
  available: "border-warn/40 bg-warn/10 text-warn",
};

function statusTone(adapter: Adapter): string {
  return STATUS_TONE[adapter.status] ?? "border-line bg-surface text-ink-muted";
}

export function AdapterRegistryPanel() {
  const { state } = useRegistry();
  const [expanded, setExpanded] = useState<string | null>(null);

  if (state.kind === "loading") {
    return <p className="text-ink-muted">Loading adapters…</p>;
  }
  if (state.kind === "error") {
    return (
      <p className="rounded-card border border-danger/40 bg-danger/10 px-3 py-2 text-danger">
        {state.message}
      </p>
    );
  }

  const { registry, matrix } = state;
  return (
    <div className="space-y-5">
      <section className="space-y-2">
        <div className="flex items-center gap-3">
          <h2 className="text-lg font-medium">Adapter registry</h2>
          <span className="text-xs text-ink-muted">
            {registry.adapters.length} manifest(s) loaded
          </span>
        </div>

        {registry.errors.length > 0 && (
          <div className="space-y-1 rounded-card border border-danger/40 bg-danger/10 p-3 text-sm">
            <p className="font-medium text-danger">
              {registry.errors.length} manifest(s) would not load. The rest of the
              registry still works.
            </p>
            <ul className="space-y-0.5 font-mono text-xs text-danger">
              {registry.errors.map((error) => (
                <li key={error.path}>
                  {error.path}: {error.error}
                </li>
              ))}
            </ul>
          </div>
        )}

        <div className="overflow-x-auto rounded-card border border-line bg-surface-raised">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-line text-left text-ink-muted">
                <th className="px-3 py-2 font-medium">Adapter</th>
                <th className="px-3 py-2 font-medium">Kind</th>
                <th className="px-3 py-2 font-medium">Direction</th>
                <th className="px-3 py-2 font-medium">Scopes</th>
                <th className="px-3 py-2 font-medium">Status</th>
                <th className="px-3 py-2 font-medium">Simulated</th>
              </tr>
            </thead>
            <tbody>
              {registry.adapters.map((adapter) => (
                <tr
                  key={adapter.id}
                  onClick={() =>
                    setExpanded((current) =>
                      current === adapter.id ? null : adapter.id,
                    )
                  }
                  className="cursor-pointer border-b border-line/60 align-top hover:bg-surface"
                >
                  <td className="px-3 py-2">
                    <div className="font-medium">{adapter.name}</div>
                    <div className="font-mono text-xs text-ink-muted">
                      {adapter.id} v{adapter.version}
                    </div>
                    {expanded === adapter.id && (
                      <div className="mt-1 max-w-prose space-y-1 text-xs text-ink-muted">
                        <p>{adapter.description}</p>
                        <p>
                          <span className="font-medium">Capabilities:</span>{" "}
                          {adapter.capabilities.join(", ") || "none"}
                        </p>
                        {adapter.mutating_capabilities.length > 0 && (
                          <p className="text-warn">
                            Mutating: {adapter.mutating_capabilities.join(", ")}
                          </p>
                        )}
                        <p>
                          <span className="font-medium">Limits:</span>{" "}
                          {adapter.limits.timeout_s}s timeout ·{" "}
                          {adapter.limits.max_calls_per_minute}/min ·{" "}
                          {adapter.limits.max_rows} rows
                        </p>
                        {adapter.redaction.policy_id && (
                          <p>
                            <span className="font-medium">Redaction:</span>{" "}
                            {adapter.redaction.policy_id}
                          </p>
                        )}
                      </div>
                    )}
                  </td>
                  <td className="px-3 py-2 text-xs">{adapter.kind}</td>
                  <td className="px-3 py-2 text-xs text-ink-muted">
                    {adapter.direction}
                  </td>
                  <td className="px-3 py-2 text-xs">
                    {adapter.credential_scopes.join(", ")}
                  </td>
                  <td className="px-3 py-2">
                    <span
                      className={`border px-2 py-0.5 text-[11px] uppercase ${statusTone(
                        adapter,
                      )}`}
                    >
                      {adapter.status}
                    </span>
                  </td>
                  <td className="px-3 py-2">
                    <SimulatedBadge simulated={adapter.simulated} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <section className="space-y-2">
        <h3 className="text-sm font-medium">Capability coverage</h3>
        <ul className="divide-y divide-line rounded-card border border-line bg-surface-raised text-sm">
          {matrix.coverage.map((kind) => (
            <li key={kind.kind} className="flex flex-wrap items-center gap-3 px-3 py-2">
              <span className="w-32 shrink-0 font-medium">{kind.kind}</span>
              <span
                className={`border px-2 py-0.5 text-[11px] uppercase ${
                  kind.complete
                    ? "border-ok/40 bg-ok/10 text-ok"
                    : "border-warn/40 bg-warn/10 text-warn"
                }`}
              >
                {kind.complete ? "complete" : "partial"}
              </span>
              <span className="text-xs text-ink-muted">
                {kind.covered.length}/{kind.capabilities.length} capabilities ·{" "}
                {kind.adapters.length} adapter(s)
              </span>
              {kind.uncovered.length > 0 && (
                <span className="text-xs text-warn">
                  uncovered: {kind.uncovered.join(", ")}
                </span>
              )}
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}
