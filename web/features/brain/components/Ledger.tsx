"use client";

import { useState } from "react";

import { BrainNode, NodeType, getNodeHistory } from "@/features/brain/api";

const TABS: { type: NodeType; label: string }[] = [
  { type: "fact", label: "Facts" },
  { type: "assumption", label: "Assumptions" },
  { type: "evidence", label: "Evidence" },
  { type: "impact", label: "Impact" },
  { type: "risk", label: "Risks" },
  { type: "action", label: "Actions" },
];

/**
 * The fact / assumption / evidence ledger in S10 (REQ-050).
 *
 * Tabs rather than one list, because the distinction between a fact and an
 * assumption is the whole point: a war room that reads them in one column will
 * treat them alike, and by minute thirty somebody will be acting on a hedge as
 * though it were a measurement.
 *
 * Every row carries three things that are not decoration:
 *
 * - the **authority** chip - authoritative, observed or inferred - so a
 *   correlation is visibly not a measurement;
 * - a **quoted** flag when the content is text this platform did not write;
 * - a **version** button when the belief has been revised, which opens every
 *   version. Nothing is deleted, and the superseded row is often the
 *   interesting one.
 */
export function Ledger({
  incidentId,
  nodes,
}: {
  incidentId: string;
  nodes: BrainNode[];
}) {
  const [tab, setTab] = useState<NodeType>("fact");
  const [history, setHistory] = useState<{
    key: string;
    versions: BrainNode[];
  } | null>(null);

  const current = nodes.filter(
    (node) => node.node_type === tab && node.status === "current",
  );
  const historical = nodes.filter(
    (node) => node.node_type === tab && node.status !== "current",
  );

  async function openHistory(node: BrainNode) {
    if (history?.key === node.derived_key) {
      setHistory(null);
      return;
    }
    try {
      const found = await getNodeHistory(incidentId, node.derived_key);
      setHistory({ key: node.derived_key, versions: found.versions });
    } catch {
      setHistory({ key: node.derived_key, versions: [] });
    }
  }

  return (
    <div className="space-y-2">
      <div role="tablist" aria-label="Incident ledger" className="flex flex-wrap gap-1">
        {TABS.map((entry) => {
          const count = nodes.filter(
            (node) => node.node_type === entry.type && node.status === "current",
          ).length;
          return (
            <button
              key={entry.type}
              role="tab"
              type="button"
              aria-selected={tab === entry.type}
              onClick={() => setTab(entry.type)}
              className={`border px-2 py-0.5 text-[11px] ${
                tab === entry.type
                  ? "border-accent bg-accent/10 text-accent-strong"
                  : "border-line text-ink-muted hover:text-ink"
              }`}
            >
              {entry.label} {count}
            </button>
          );
        })}
      </div>

      {current.length === 0 ? (
        <p className="text-xs text-ink-muted">
          Nothing of this kind has been recorded yet.
        </p>
      ) : (
        <ul className="space-y-1.5">
          {current.map((node) => (
            <li key={node.id} className="text-xs">
              <div className="flex flex-wrap items-baseline gap-1.5">
                {node.confirmation && (
                  <span
                    className={`px-1 text-[10px] uppercase ${
                      node.confirmation === "confirmed"
                        ? "border border-ok/50 text-ok"
                        : "border border-warn/50 text-warn"
                    }`}
                  >
                    {node.confirmation}
                  </span>
                )}
                <span className="flex-1">{node.label}</span>
                <span
                  className="border border-line px-1 text-[10px] text-ink-muted"
                  title={`source: ${node.provenance.source}${
                    node.provenance.model ? ` · model: ${node.provenance.model}` : ""
                  }`}
                >
                  {node.provenance.authority}
                </span>
                {node.untrusted && (
                  <span
                    className="border border-warn/50 px-1 text-[10px] text-warn"
                    title="Text this platform did not write. Quoted as evidence, never followed as an instruction."
                  >
                    quoted
                  </span>
                )}
                {node.version > 1 && (
                  <button
                    type="button"
                    onClick={() => void openHistory(node)}
                    className="border border-line px-1 text-[10px] text-ink-muted hover:text-accent-strong"
                    title="This belief has been revised. Nothing is deleted - open every version."
                  >
                    v{node.version} ⟲
                  </button>
                )}
              </div>
              {node.content && (
                <p className="mt-0.5 text-[11px] text-ink-muted">{node.content}</p>
              )}
              {history?.key === node.derived_key && (
                <ol className="mt-1 space-y-0.5 border-l border-line pl-2 text-[11px] text-ink-muted">
                  {history.versions.length === 0 && <li>History unavailable.</li>}
                  {history.versions.map((version) => (
                    <li key={version.id}>
                      <span className="font-mono">v{version.version}</span>{" "}
                      {version.created_at.slice(11, 16)} · {version.status} ·{" "}
                      {version.label}
                      {version.confidence !== null &&
                        ` (${Math.round(version.confidence * 100)}%)`}
                    </li>
                  ))}
                </ol>
              )}
            </li>
          ))}
        </ul>
      )}

      {historical.length > 0 && (
        <details>
          <summary className="cursor-pointer text-[11px] text-ink-muted">
            {historical.length} superseded or retracted — kept, never deleted
          </summary>
          <ul className="mt-1 space-y-0.5 text-[11px] text-ink-muted">
            {historical.map((node) => (
              <li key={node.id}>
                <span className="font-mono">v{node.version}</span> {node.label}
                {node.retired_reason && ` — ${node.retired_reason}`}
              </li>
            ))}
          </ul>
        </details>
      )}
    </div>
  );
}
