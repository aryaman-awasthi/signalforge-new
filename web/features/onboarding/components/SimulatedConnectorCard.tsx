"use client";

import { useState } from "react";

import { ApiError } from "@/lib/api/client";
import { StatusPill } from "@/features/platform/components/StatusPill";
import { SimulatedBadge } from "@/features/integrations/components/SimulatedBadge";
import {
  Adapter,
  Connector,
  createConnector,
  deleteConnector,
} from "@/features/integrations/api";

/**
 * A simulated integration: built in, nothing to configure, no credential to
 * hold. One button.
 *
 * The card exists as its own shape rather than as a pull card with the fields
 * hidden, because the honest thing to say about these is not "here is a shorter
 * form" but "this one is not real". The SIMULATED marker rides every row this
 * integration produces afterwards - in the health screen, on the event source
 * and in the delivery receipt - and it starts here.
 */
export function SimulatedConnectorCard({
  adapter,
  binding,
  appId,
  onChanged,
}: {
  adapter: Adapter;
  binding?: Connector;
  appId: string;
  onChanged: () => void;
}) {
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const run = async (action: () => Promise<void>) => {
    setBusy(true);
    setError(null);
    try {
      await action();
      onChanged();
    } catch (failure) {
      setError(failure instanceof ApiError ? failure.message : "That did not work");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-2 rounded-card border border-warn/30 bg-surface-raised p-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-medium">{adapter.name}</span>
        <span className="font-mono text-xs text-ink-muted">{adapter.id}</span>
        <SimulatedBadge simulated />
        <span className="border border-line px-2 py-0.5 text-[11px] uppercase text-ink-muted">
          built-in
        </span>
        {binding && <StatusPill status={binding.status} />}
      </div>
      <p className="max-w-prose text-xs text-ink-muted">{adapter.description}</p>
      <p className="text-[11px] text-ink-muted">
        No configuration and no credential: this integration runs inside
        SignalForge. Everything it produces is labelled simulated, end to end.
      </p>
      {error && (
        <p className="rounded-card border border-danger/40 bg-danger/10 px-2 py-1 text-xs text-danger">
          {error}
        </p>
      )}
      {binding ? (
        <button
          disabled={busy}
          onClick={() => void run(async () => void (await deleteConnector(binding.id)))}
          className="rounded-card border border-line px-2.5 py-1 text-xs text-danger hover:border-danger disabled:opacity-50"
        >
          Turn off
        </button>
      ) : (
        <button
          disabled={busy}
          onClick={() =>
            void run(async () => {
              await createConnector({
                app_id: appId,
                adapter_id: adapter.id,
                name: `${appId} ${adapter.id}`,
                credential_scope: adapter.credential_scopes[0],
                config: {},
                credentials: {},
              });
            })
          }
          className="rounded-card bg-accent px-3 py-1.5 text-xs font-medium text-surface-raised hover:bg-accent-strong disabled:opacity-60"
        >
          {busy ? "Enabling…" : "Enable"}
        </button>
      )}
    </div>
  );
}
