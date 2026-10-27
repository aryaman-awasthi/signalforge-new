"use client";

import { useState } from "react";

import { ApiError } from "@/lib/api/client";
import { ActionsPanel } from "@/features/onboarding/components/ActionsPanel";
import {
  GoldenSignal,
  Journey,
  Probe,
  Profile,
  Signal,
  putMapping,
} from "@/features/apps/api";

const SIGNALS: GoldenSignal[] = [
  "availability",
  "errors",
  "latency",
  "saturation",
  "traffic",
  "business",
];

type SignalDraft = Omit<Signal, "description">;

/**
 * Step 3 - Map.
 *
 * Golden signals with their selectors, the region label, journeys, probes, and
 * the actions the application publishes in its own manifest.
 *
 * Business is a first-class signal family here rather than a special case,
 * because corroboration needs two independent families with one of them
 * customer-facing: an application mapped with only technical signals can be
 * observed but cannot be corroborated, and the profile says so.
 *
 * The whole mapping is one PUT: the aggregate's invariants cross the sections -
 * a probe names a journey, an SLO names a journey - so the domain needs the
 * whole thing in hand to check them.
 */
export function MapStep({
  appId,
  profile,
  onChanged,
}: {
  appId: string;
  profile: Profile;
  onChanged: () => void;
}) {
  const [signals, setSignals] = useState<SignalDraft[]>(
    profile.signals.map(({ description: _description, ...rest }) => rest),
  );
  const [regionLabel, setRegionLabel] = useState(profile.region_label ?? "");
  const [regions, setRegions] = useState(profile.regions.join(", "));
  const [journeys, setJourneys] = useState<Journey[]>(profile.journeys);
  const [probes, setProbes] = useState<Probe[]>(profile.probes);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const [busy, setBusy] = useState(false);

  async function save() {
    setBusy(true);
    setError(null);
    setSaved(false);
    try {
      await putMapping(appId, {
        signals,
        region_label: regionLabel.trim() || null,
        clear_region_label: regionLabel.trim() === "",
        regions: regions
          .split(",")
          .map((region) => region.trim())
          .filter(Boolean),
        journeys,
        probes,
      });
      setSaved(true);
      onChanged();
    } catch (failure) {
      setError(
        failure instanceof ApiError ? failure.message : "Could not save the mapping",
      );
    } finally {
      setBusy(false);
    }
  }

  const businessCount = signals.filter((signal) => signal.business_counter).length;

  // An application that publishes its own mapping - or an operator with one in
  // a file - should not have to retype six signals and eight probes into
  // fields. The import fills the *form*; nothing is saved until Save, so the
  // domain's validation and the operator's eyes both still stand between a
  // pasted document and the profile.
  const [importText, setImportText] = useState("");
  const [importOpen, setImportOpen] = useState(false);
  const [importError, setImportError] = useState<string | null>(null);

  function importMapping() {
    setImportError(null);
    let doc: Record<string, unknown>;
    try {
      doc = JSON.parse(importText) as Record<string, unknown>;
    } catch {
      setImportError("That is not valid JSON.");
      return;
    }
    const asList = (value: unknown): Record<string, unknown>[] =>
      Array.isArray(value) ? value.filter((item) => item && typeof item === "object") : [];
    const str = (value: unknown, fallback = ""): string =>
      typeof value === "string" ? value : fallback;
    const signalsIn = asList(doc.signals);
    const journeysIn = asList(doc.journeys);
    const probesIn = asList(doc.probes);
    if (signalsIn.length === 0 && journeysIn.length === 0 && probesIn.length === 0) {
      setImportError("Expected an object with signals, journeys and/or probes.");
      return;
    }
    if (signalsIn.length > 0) {
      setSignals(
        signalsIn.map((item) => ({
          key: str(item.key),
          signal: (SIGNALS.includes(str(item.signal) as GoldenSignal)
            ? str(item.signal)
            : "errors") as GoldenSignal,
          selector: str(item.selector),
          direction: item.direction === "lower_is_worse" ? "lower_is_worse" : "higher_is_worse",
          unit: str(item.unit),
          business_counter: item.business_counter === true,
        })),
      );
    }
    if (typeof doc.region_label === "string") setRegionLabel(doc.region_label);
    if (Array.isArray(doc.regions)) setRegions(doc.regions.map(String).join(", "));
    if (journeysIn.length > 0) {
      setJourneys(
        journeysIn.map((item) => ({
          journey_id: str(item.journey_id),
          name: str(item.name, str(item.journey_id)),
          tier: Number(item.tier) || 2,
          description: str(item.description),
        })),
      );
    }
    if (probesIn.length > 0) {
      setProbes(
        probesIn.map((item) => ({
          probe_id: str(item.probe_id),
          path: str(item.path),
          method: str(item.method, "GET").toUpperCase(),
          expected_status: Number(item.expected_status) || 200,
          timeout_s: Number(item.timeout_s) || 5,
          journey_id: typeof item.journey_id === "string" ? item.journey_id : null,
          per_region: item.per_region === true,
          synthetic_header: str(item.synthetic_header, "X-SignalForge-Synthetic"),
        })),
      );
    }
    setImportOpen(false);
    setImportText("");
    setSaved(false);
  }

  return (
    <div className="space-y-6">
      <section className="space-y-2">
        <div className="flex flex-wrap items-center gap-3">
          <button
            type="button"
            onClick={() => setImportOpen((current) => !current)}
            className="rounded-card border border-line px-2.5 py-1 text-xs hover:border-accent hover:text-accent-strong"
          >
            {importOpen ? "Cancel import" : "Import mapping from JSON…"}
          </button>
          <span className="text-xs text-ink-muted">
            Paste a document with <span className="font-mono">signals</span>,{" "}
            <span className="font-mono">region_label</span>,{" "}
            <span className="font-mono">regions</span>,{" "}
            <span className="font-mono">journeys</span> and{" "}
            <span className="font-mono">probes</span>. It fills the form below;
            Save is still yours to press.
          </span>
        </div>
        {importOpen && (
          <div className="space-y-2">
            <textarea
              value={importText}
              onChange={(event) => setImportText(event.target.value)}
              rows={8}
              spellCheck={false}
              placeholder='{"region_label": "region", "regions": ["region-a"], "signals": [...], "journeys": [...], "probes": [...]}'
              className="w-full rounded-card border border-line bg-surface px-2 py-1.5 font-mono text-xs"
            />
            {importError && <p className="text-xs text-danger">{importError}</p>}
            <button
              type="button"
              onClick={importMapping}
              disabled={importText.trim() === ""}
              className="rounded-card bg-accent px-3 py-1.5 text-sm font-medium text-surface-raised hover:bg-accent-strong disabled:opacity-50"
            >
              Load into the form
            </button>
          </div>
        )}
      </section>

      <section className="space-y-3">
        <div className="flex flex-wrap items-center gap-3">
          <h2 className="text-lg font-medium">Golden signals</h2>
          <span className="text-xs text-ink-muted">
            {signals.length} mapped · {businessCount} business counter(s)
          </span>
          <button
            onClick={() =>
              setSignals((current) => [
                ...current,
                {
                  key: "",
                  signal: "errors",
                  selector: "",
                  direction: "higher_is_worse",
                  unit: "",
                  business_counter: false,
                },
              ])
            }
            className="rounded-card border border-line px-2 py-0.5 text-xs hover:border-accent hover:text-accent-strong"
          >
            Add signal
          </button>
        </div>
        {businessCount === 0 && signals.length > 0 && (
          <p className="rounded-card border border-warn/40 bg-warn/10 px-3 py-2 text-xs text-warn">
            No business counter yet. Corroboration needs two independent signal
            families with one of them customer-facing, so without one this
            application can be watched but not corroborated.
          </p>
        )}
        <div className="space-y-2">
          {signals.map((signal, index) => (
            <div
              key={index}
              className="flex flex-wrap items-end gap-2 rounded-card border border-line bg-surface-raised p-2"
            >
              <label className="space-y-1">
                <span className="text-[11px] text-ink-muted">key</span>
                <input
                  value={signal.key}
                  onChange={(event) =>
                    setSignals((current) =>
                      current.map((item, i) =>
                        i === index ? { ...item, key: event.target.value } : item,
                      ),
                    )
                  }
                  className="block w-32 rounded-card border border-line bg-surface px-2 py-1 text-sm"
                />
              </label>
              <label className="space-y-1">
                <span className="text-[11px] text-ink-muted">family</span>
                <select
                  value={signal.signal}
                  onChange={(event) =>
                    setSignals((current) =>
                      current.map((item, i) =>
                        i === index
                          ? { ...item, signal: event.target.value as GoldenSignal }
                          : item,
                      ),
                    )
                  }
                  className="block rounded-card border border-line bg-surface px-2 py-1 text-sm"
                >
                  {SIGNALS.map((option) => (
                    <option key={option} value={option}>
                      {option}
                    </option>
                  ))}
                </select>
              </label>
              <label className="min-w-56 flex-1 space-y-1">
                <span className="text-[11px] text-ink-muted">
                  selector - name{'{'}label=&quot;value&quot;{'}'}
                </span>
                <input
                  value={signal.selector}
                  onChange={(event) =>
                    setSignals((current) =>
                      current.map((item, i) =>
                        i === index ? { ...item, selector: event.target.value } : item,
                      ),
                    )
                  }
                  className="block w-full rounded-card border border-line bg-surface px-2 py-1 font-mono text-xs"
                />
              </label>
              <label className="space-y-1">
                <span className="text-[11px] text-ink-muted">direction</span>
                <select
                  value={signal.direction}
                  onChange={(event) =>
                    setSignals((current) =>
                      current.map((item, i) =>
                        i === index
                          ? {
                              ...item,
                              direction: event.target.value as Signal["direction"],
                            }
                          : item,
                      ),
                    )
                  }
                  className="block rounded-card border border-line bg-surface px-2 py-1 text-sm"
                >
                  <option value="higher_is_worse">higher is worse</option>
                  <option value="lower_is_worse">lower is worse</option>
                </select>
              </label>
              <label className="space-y-1">
                <span className="text-[11px] text-ink-muted">unit</span>
                <input
                  value={signal.unit}
                  onChange={(event) =>
                    setSignals((current) =>
                      current.map((item, i) =>
                        i === index ? { ...item, unit: event.target.value } : item,
                      ),
                    )
                  }
                  className="block w-20 rounded-card border border-line bg-surface px-2 py-1 text-sm"
                />
              </label>
              <label className="flex items-center gap-1 text-xs">
                <input
                  type="checkbox"
                  checked={signal.business_counter}
                  onChange={(event) =>
                    setSignals((current) =>
                      current.map((item, i) =>
                        i === index
                          ? { ...item, business_counter: event.target.checked }
                          : item,
                      ),
                    )
                  }
                />
                business counter
              </label>
              <button
                onClick={() =>
                  setSignals((current) => current.filter((_, i) => i !== index))
                }
                className="text-xs text-danger hover:underline"
              >
                Remove
              </button>
            </div>
          ))}
        </div>
      </section>

      <section className="space-y-2">
        <h2 className="text-lg font-medium">Regions</h2>
        <div className="flex flex-wrap gap-3">
          <label className="space-y-1">
            <span className="text-[11px] text-ink-muted">
              region label (the metric label that carries the region)
            </span>
            <input
              value={regionLabel}
              onChange={(event) => setRegionLabel(event.target.value)}
              placeholder="region"
              className="block w-48 rounded-card border border-line bg-surface px-2 py-1 text-sm"
            />
          </label>
          <label className="min-w-56 flex-1 space-y-1">
            <span className="text-[11px] text-ink-muted">
              regions, comma separated - probes fan out across all of them
            </span>
            <input
              value={regions}
              onChange={(event) => setRegions(event.target.value)}
              placeholder="eu-west-1, us-east-1"
              className="block w-full rounded-card border border-line bg-surface px-2 py-1 text-sm"
            />
          </label>
        </div>
      </section>

      <section className="space-y-3">
        <div className="flex items-center gap-3">
          <h2 className="text-lg font-medium">Journeys</h2>
          <button
            onClick={() =>
              setJourneys((current) => [
                ...current,
                { journey_id: "", name: "", tier: 2, description: "" },
              ])
            }
            className="rounded-card border border-line px-2 py-0.5 text-xs hover:border-accent hover:text-accent-strong"
          >
            Add journey
          </button>
        </div>
        <div className="space-y-2">
          {journeys.map((journey, index) => (
            <div
              key={index}
              className="flex flex-wrap items-end gap-2 rounded-card border border-line bg-surface-raised p-2"
            >
              <input
                value={journey.journey_id}
                placeholder="journey id"
                onChange={(event) =>
                  setJourneys((current) =>
                    current.map((item, i) =>
                      i === index ? { ...item, journey_id: event.target.value } : item,
                    ),
                  )
                }
                className="w-40 rounded-card border border-line bg-surface px-2 py-1 text-sm"
              />
              <input
                value={journey.name}
                placeholder="Name"
                onChange={(event) =>
                  setJourneys((current) =>
                    current.map((item, i) =>
                      i === index ? { ...item, name: event.target.value } : item,
                    ),
                  )
                }
                className="min-w-40 flex-1 rounded-card border border-line bg-surface px-2 py-1 text-sm"
              />
              <label className="space-y-1">
                <span className="text-[11px] text-ink-muted">tier</span>
                <input
                  type="number"
                  min={0}
                  max={3}
                  value={journey.tier}
                  onChange={(event) =>
                    setJourneys((current) =>
                      current.map((item, i) =>
                        i === index
                          ? { ...item, tier: Number.parseInt(event.target.value, 10) }
                          : item,
                      ),
                    )
                  }
                  className="block w-16 rounded-card border border-line bg-surface px-2 py-1 text-sm"
                />
              </label>
              <button
                onClick={() =>
                  setJourneys((current) => current.filter((_, i) => i !== index))
                }
                className="text-xs text-danger hover:underline"
              >
                Remove
              </button>
            </div>
          ))}
        </div>
      </section>

      <section className="space-y-3">
        <div className="flex items-center gap-3">
          <h2 className="text-lg font-medium">Probe templates</h2>
          <button
            onClick={() =>
              setProbes((current) => [
                ...current,
                {
                  probe_id: "",
                  path: "/",
                  method: "GET",
                  expected_status: 200,
                  timeout_s: 5,
                  journey_id: journeys[0]?.journey_id ?? null,
                  per_region: true,
                  synthetic_header: "X-SignalForge-Synthetic",
                },
              ])
            }
            className="rounded-card border border-line px-2 py-0.5 text-xs hover:border-accent hover:text-accent-strong"
          >
            Add probe
          </button>
        </div>
        <p className="text-xs text-ink-muted">
          Each probe carries a synthetic header so the application can tell a
          synthetic request from a customer one, and fans out per region when
          asked to.
        </p>
        <div className="space-y-2">
          {probes.map((probe, index) => (
            <div
              key={index}
              className="flex flex-wrap items-end gap-2 rounded-card border border-line bg-surface-raised p-2"
            >
              <input
                value={probe.probe_id}
                placeholder="probe id"
                onChange={(event) =>
                  setProbes((current) =>
                    current.map((item, i) =>
                      i === index ? { ...item, probe_id: event.target.value } : item,
                    ),
                  )
                }
                className="w-40 rounded-card border border-line bg-surface px-2 py-1 text-sm"
              />
              <input
                value={probe.path}
                placeholder="/checkout"
                onChange={(event) =>
                  setProbes((current) =>
                    current.map((item, i) =>
                      i === index ? { ...item, path: event.target.value } : item,
                    ),
                  )
                }
                className="min-w-40 flex-1 rounded-card border border-line bg-surface px-2 py-1 font-mono text-xs"
              />
              <label className="space-y-1">
                <span className="text-[11px] text-ink-muted">expects</span>
                <input
                  type="number"
                  value={probe.expected_status}
                  onChange={(event) =>
                    setProbes((current) =>
                      current.map((item, i) =>
                        i === index
                          ? {
                              ...item,
                              expected_status: Number.parseInt(event.target.value, 10),
                            }
                          : item,
                      ),
                    )
                  }
                  className="block w-20 rounded-card border border-line bg-surface px-2 py-1 text-sm"
                />
              </label>
              <label className="space-y-1">
                <span className="text-[11px] text-ink-muted">journey</span>
                <select
                  value={probe.journey_id ?? ""}
                  onChange={(event) =>
                    setProbes((current) =>
                      current.map((item, i) =>
                        i === index
                          ? { ...item, journey_id: event.target.value || null }
                          : item,
                      ),
                    )
                  }
                  className="block rounded-card border border-line bg-surface px-2 py-1 text-sm"
                >
                  <option value="">-</option>
                  {journeys.map((journey) => (
                    <option key={journey.journey_id} value={journey.journey_id}>
                      {journey.journey_id}
                    </option>
                  ))}
                </select>
              </label>
              <label className="flex items-center gap-1 text-xs">
                <input
                  type="checkbox"
                  checked={probe.per_region}
                  onChange={(event) =>
                    setProbes((current) =>
                      current.map((item, i) =>
                        i === index
                          ? { ...item, per_region: event.target.checked }
                          : item,
                      ),
                    )
                  }
                />
                per region
              </label>
              <button
                onClick={() =>
                  setProbes((current) => current.filter((_, i) => i !== index))
                }
                className="text-xs text-danger hover:underline"
              >
                Remove
              </button>
            </div>
          ))}
        </div>
      </section>

      {error && (
        <p className="rounded-card border border-danger/40 bg-danger/10 px-3 py-2 text-sm text-danger">
          {error}
        </p>
      )}
      {saved && !error && (
        <p className="rounded-card border border-ok/40 bg-ok/10 px-3 py-2 text-sm text-ok">
          Mapping saved.
        </p>
      )}
      <button
        onClick={() => void save()}
        disabled={busy}
        className="rounded-card bg-accent px-3 py-2 text-sm font-medium text-surface-raised hover:bg-accent-strong disabled:opacity-60"
      >
        {busy ? "Saving…" : "Save mapping"}
      </button>

      <ActionsPanel appId={appId} actions={profile.actions} onChanged={onChanged} />
    </div>
  );
}
