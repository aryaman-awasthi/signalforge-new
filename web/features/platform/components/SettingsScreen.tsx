"use client";

import { useState } from "react";

import { AdapterRegistryPanel } from "@/features/integrations/components/AdapterRegistryPanel";
import { IngestKeysPanel } from "@/features/integrations/components/IngestKeysPanel";
import { DeadLettersPanel } from "@/features/platform/components/DeadLettersPanel";
import { PlatformHealthPanel } from "@/features/platform/components/PlatformHealthPanel";
import { usePlatformSettings } from "@/features/platform/hooks/usePlatformSettings";
import { EventTimeline } from "@/features/timeline/components/EventTimeline";

const TABS = [
  "Platform health",
  "Adapters",
  "Ingest",
  "Dead letters",
  "Event timeline",
] as const;
type Tab = (typeof TABS)[number];

/**
 * Screen 21. Sprint 2 filled the platform panes; sprint 3 adds the adapter
 * registry (what this installation can integrate with, and which manifests
 * refused to load) and the ingest section (every credential a sending
 * application holds, with rotation and revocation). The policy sections join
 * the same shell in a later sprint.
 */
export function SettingsScreen() {
  const { state, reload } = usePlatformSettings();
  const [tab, setTab] = useState<Tab>("Platform health");

  if (state.kind === "loading") {
    return <p className="text-ink-muted">Loading platform status…</p>;
  }
  if (state.kind === "error") {
    return (
      <p className="rounded-card border border-danger/40 bg-danger/10 px-3 py-2 text-danger">
        {state.message}
      </p>
    );
  }

  const dead = state.data.deadLetters.filter((row) => row.state === "dead").length;
  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-semibold">Settings</h1>
      <nav className="flex gap-1 border-b border-line">
        {TABS.map((candidate) => (
          <button
            key={candidate}
            onClick={() => setTab(candidate)}
            className={`px-4 py-2 text-sm ${
              tab === candidate
                ? "border-b-2 border-accent font-medium text-ink"
                : "text-ink-muted hover:text-ink"
            }`}
          >
            {candidate}
            {candidate === "Dead letters" && dead > 0 && (
              <span className="ml-2 rounded-full bg-danger/15 px-1.5 py-0.5 text-[11px] text-danger">
                {dead}
              </span>
            )}
          </button>
        ))}
      </nav>

      {tab === "Platform health" && <PlatformHealthPanel status={state.data.status} />}
      {tab === "Adapters" && <AdapterRegistryPanel />}
      {tab === "Ingest" && <IngestKeysPanel />}
      {tab === "Dead letters" && (
        <DeadLettersPanel
          deadLetters={state.data.deadLetters}
          onChanged={() => void reload()}
        />
      )}
      {tab === "Event timeline" && <EventTimeline />}
    </div>
  );
}
