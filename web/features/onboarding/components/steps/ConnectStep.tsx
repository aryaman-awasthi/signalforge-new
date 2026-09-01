"use client";

import { useMemo, useState } from "react";

import { ApiError } from "@/lib/api/client";
import { PullConnectorCard } from "@/features/onboarding/components/PullConnectorCard";
import { PushConnectorCard } from "@/features/onboarding/components/PushConnectorCard";
import { SimulatedConnectorCard } from "@/features/onboarding/components/SimulatedConnectorCard";
import type { WizardData } from "@/features/onboarding/hooks/useOnboarding";
import {
  ConnectionTest,
  DiscoveryReport,
  discover,
  runConnectionTests,
} from "@/features/apps/api";
import type { Adapter, IngestStream } from "@/features/integrations/api";

/**
 * Step 2 - Connect.
 *
 * Three card shapes, because there are three genuinely different things an
 * operator has to do:
 *
 * - **pull / invoke**: give SignalForge config and a credential so it can call
 *   out. Fields come from the adapter's own config_schema.
 * - **push**: hand a credential to whoever is sending, and give them a URL.
 *   Nothing to configure on this side beyond an optional translator.
 * - **simulated**: built in. One button, and a label that never comes off.
 *
 * Auto-discovery is on this step and not before it: it needs the base URL, it
 * writes no binding, and its findings pre-fill the cards rather than acting on
 * their own. A 401 or 403 counts as found - a guarded endpoint is an endpoint.
 */

/** Adapter kind -> the ingest stream a push adapter of that kind receives on.
 * The mapping is the frozen contract's, restated here only to route a card. */
const STREAM_FOR_KIND: Record<string, IngestStream> = {
  alerts: "alerts",
  changes: "changes",
  topology: "topology",
  collaboration: "messages",
  itsm: "tickets",
};

/** The five frozen ingest paths, for senders that already speak the canonical
 * shape - including collaboration and itsm, which can never have a push adapter
 * because the frozen SPI defines `normalize` only on alerts, changes and
 * topology. Without these rows two of the five paths would be unreachable. */
const CANONICAL_STREAMS: IngestStream[] = [
  "changes",
  "alerts",
  "topology",
  "messages",
  "tickets",
];

export function ConnectStep({
  data,
  onChanged,
}: {
  data: WizardData;
  onChanged: () => void;
}) {
  const appId = data.detail.application.app_id;
  const [report, setReport] = useState<DiscoveryReport | null>(null);
  const [tests, setTests] = useState<ConnectionTest[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<"discover" | "test" | null>(null);

  const bindings = useMemo(
    () => new Map(data.connectors.map((connector) => [connector.adapter_id, connector])),
    [data.connectors],
  );

  /** Config auto-discovery suggested for an adapter, by adapter id. */
  const suggested = useMemo(() => {
    const out = new Map<string, Record<string, unknown>>();
    for (const finding of report?.findings ?? []) {
      for (const candidate of finding.candidates) {
        if (!out.has(candidate.adapter_id)) {
          out.set(candidate.adapter_id, candidate.suggested_config);
        }
      }
    }
    return out;
  }, [report]);

  // Bindable means this deployment *ships* the adapter, not that its runtime
  // happens to be loaded on this node: a built-in adapter with no runtime here
  // can still be bound, and its card says so rather than hiding the option.
  // Interface-only entries go to the bottom section instead - binding one would
  // buy nothing but an UNKNOWN row.
  const shipped = data.adapters.filter(
    (adapter) => adapter.availability === "built_in",
  );
  const pull = shipped.filter(
    (adapter) => !adapter.simulated && adapter.mode !== "push",
  );
  const push = shipped.filter(
    (adapter) => !adapter.simulated && adapter.mode === "push",
  );
  const simulated = shipped.filter((adapter) => adapter.simulated);
  const interfaceOnly = data.adapters.filter(
    (adapter) => adapter.availability !== "built_in",
  );

  const streamOf = (adapter: Adapter): IngestStream =>
    STREAM_FOR_KIND[adapter.kind] ?? "alerts";

  const boundPushStreams = new Set(
    push.filter((adapter) => bindings.has(adapter.id)).map(streamOf),
  );

  const run = async (
    which: "discover" | "test",
    action: () => Promise<void>,
  ) => {
    setBusy(which);
    setError(null);
    try {
      await action();
      onChanged();
    } catch (failure) {
      setError(failure instanceof ApiError ? failure.message : "That did not work");
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="space-y-6">
      <section className="space-y-3">
        <div className="flex flex-wrap items-center gap-3">
          <h2 className="text-lg font-medium">Auto-discovery</h2>
          <button
            disabled={busy !== null || !data.detail.application.base_url}
            onClick={() =>
              void run("discover", async () => {
                setReport(await discover(appId));
              })
            }
            className="rounded-card border border-line px-2.5 py-1 text-xs hover:border-accent hover:text-accent-strong disabled:opacity-50"
          >
            {busy === "discover" ? "Probing…" : "Probe this application"}
          </button>
          <button
            disabled={busy !== null || data.connectors.length === 0}
            onClick={() =>
              void run("test", async () => {
                setTests(await runConnectionTests(appId));
              })
            }
            className="rounded-card border border-line px-2.5 py-1 text-xs hover:border-accent hover:text-accent-strong disabled:opacity-50"
          >
            {busy === "test" ? "Testing…" : "Test every connector"}
          </button>
        </div>
        {!data.detail.application.base_url && (
          <p className="text-xs text-warn">
            No base URL on this application, so there is nothing to probe. Set one
            in Basics.
          </p>
        )}
        {error && (
          <p className="rounded-card border border-danger/40 bg-danger/10 px-3 py-2 text-sm text-danger">
            {error}
          </p>
        )}
        {report && (
          <div className="space-y-1 rounded-card border border-line bg-surface-raised p-3 text-xs">
            <p className="text-ink-muted">
              {report.found} of {report.probed} probed endpoints answered. No
              credential was sent and no binding was written - findings only
              pre-fill the cards below.
            </p>
            <ul className="divide-y divide-line">
              {report.findings.map((finding) => (
                <li key={finding.key} className="flex flex-wrap items-center gap-2 py-1">
                  <span
                    className={`w-16 shrink-0 uppercase ${
                      finding.present ? "text-ok" : "text-ink-muted"
                    }`}
                  >
                    {finding.present ? (finding.guarded ? "guarded" : "found") : "absent"}
                  </span>
                  <span className="font-mono">{finding.url}</span>
                  <span className="text-ink-muted">
                    {finding.status_code ?? "-"} · {Math.round(finding.latency_ms)}ms ·{" "}
                    {finding.detail}
                  </span>
                  {finding.candidates.length > 0 && (
                    <span className="text-accent">
                      {finding.candidates.map((c) => c.adapter_id).join(", ")}
                    </span>
                  )}
                </li>
              ))}
            </ul>
          </div>
        )}
        {tests && (
          <ul className="space-y-1 rounded-card border border-line bg-surface-raised p-3 text-xs">
            {tests.map((test) => (
              <li key={test.binding_id} className="flex flex-wrap gap-2">
                <span
                  className={`w-16 shrink-0 uppercase ${
                    test.outcome === "passed"
                      ? "text-ok"
                      : test.outcome === "failed"
                        ? "text-danger"
                        : "text-warn"
                  }`}
                >
                  {test.outcome}
                </span>
                <span className="font-medium">{test.adapter_name}</span>
                <span className="text-ink-muted">
                  {test.direction} · {test.detail}
                </span>
              </li>
            ))}
            {tests.length === 0 && (
              <li className="text-ink-muted">Nothing bound yet.</li>
            )}
          </ul>
        )}
      </section>

      <section className="space-y-3">
        <h2 className="text-lg font-medium">Pull and invoke integrations</h2>
        <p className="text-xs text-ink-muted">
          SignalForge calls these. Fields are rendered from each adapter&apos;s own
          manifest, which is also what the API validates against.
        </p>
        <div className="grid gap-3 lg:grid-cols-2">
          {pull.map((adapter) => (
            <PullConnectorCard
              key={adapter.id}
              adapter={adapter}
              binding={bindings.get(adapter.id)}
              appId={appId}
              suggestedConfig={suggested.get(adapter.id)}
              onChanged={onChanged}
            />
          ))}
        </div>
      </section>

      <section className="space-y-3">
        <h2 className="text-lg font-medium">Push integrations</h2>
        <p className="text-xs text-ink-muted">
          The application posts to SignalForge. Each card gives you the endpoint
          and a credential to hand over; the token is shown once and is masked
          everywhere afterwards.
        </p>
        <div className="grid gap-3 lg:grid-cols-2">
          {push.map((adapter) => (
            <PushConnectorCard
              key={adapter.id}
              stream={streamOf(adapter)}
              adapter={adapter}
              binding={bindings.get(adapter.id)}
              keys={data.keys.filter(
                (key) => key.binding_id === bindings.get(adapter.id)?.id,
              )}
              appId={appId}
              onChanged={onChanged}
            />
          ))}
          {CANONICAL_STREAMS.filter((stream) => !boundPushStreams.has(stream)).map(
            (stream) => (
              <PushConnectorCard
                key={`canonical-${stream}`}
                stream={stream}
                keys={data.keys.filter(
                  (key) => key.stream === stream && !key.binding_id,
                )}
                appId={appId}
                onChanged={onChanged}
              />
            ),
          )}
        </div>
      </section>

      <section className="space-y-3">
        <h2 className="text-lg font-medium">Simulated integrations</h2>
        <p className="text-xs text-ink-muted">
          Built in. Nothing to configure, no credential to hold - and everything
          they produce carries the simulated marker for the rest of its life.
        </p>
        <div className="grid gap-3 lg:grid-cols-2">
          {simulated.map((adapter) => (
            <SimulatedConnectorCard
              key={adapter.id}
              adapter={adapter}
              binding={bindings.get(adapter.id)}
              appId={appId}
              onChanged={onChanged}
            />
          ))}
        </div>
      </section>

      {interfaceOnly.length > 0 && (
        <section className="space-y-2">
          <h2 className="text-lg font-medium">Available, not installed</h2>
          <p className="text-xs text-ink-muted">
            The interface exists and the capability matrix lists it, but no runtime
            ships on this node. A binding to one of these reports unknown rather
            than pretending.
          </p>
          <ul className="flex flex-wrap gap-2">
            {interfaceOnly.map((adapter) => (
              <li
                key={adapter.id}
                className="rounded-card border border-line px-2 py-1 text-xs text-ink-muted"
              >
                {adapter.name} · {adapter.kind} · {adapter.status}
              </li>
            ))}
          </ul>
        </section>
      )}

      {data.registryErrors.length > 0 && (
        <section className="space-y-1 rounded-card border border-danger/40 bg-danger/10 p-3 text-xs">
          <p className="font-medium text-danger">
            {data.registryErrors.length} adapter manifest(s) would not load, so they
            are missing from the lists above.
          </p>
          <ul className="font-mono text-danger">
            {data.registryErrors.map((registryError) => (
              <li key={registryError.path}>
                {registryError.path}: {registryError.error}
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}
