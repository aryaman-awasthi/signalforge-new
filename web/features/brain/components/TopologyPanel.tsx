"use client";

import { useMemo, useState } from "react";

import { BrainTopology, Impact } from "@/features/brain/api";
import { useBrainTopology } from "@/features/brain/hooks/useBrain";

/**
 * S13 — Affected Services (REQ-050).
 *
 * Replaces Sprint 4's flat list of service names with the two things that list
 * could not say: **what depends on what**, and **which of it was actually
 * measured**.
 *
 * The CONFIRMED / PREDICTED split is the panel's reason to exist and it is
 * carried three ways at once - two separate lists, two colours in the graph, and
 * a legend that spells out what each means in words. A predicted downstream
 * outage that looked like an observed one is how a war room pages four teams for
 * an outage that never happened, and once that has happened twice people stop
 * believing the blast radius at all.
 *
 * The graph is a deliberately plain layered SVG rather than a force simulation:
 * an estate of a few dozen services reads better in columns by dependency depth
 * than in a cloud that settles differently on every render, and a war room needs
 * the same picture twice.
 */
export function TopologyPanel({ incidentId }: { incidentId: string }) {
  const [version, setVersion] = useState<string | undefined>(undefined);
  const { state } = useBrainTopology(incidentId, version);

  if (state.kind === "loading") {
    return <p className="text-xs text-ink-muted">Reading the dependency map…</p>;
  }
  if (state.kind === "error") {
    return <p className="text-xs text-danger">{state.message}</p>;
  }

  const { topology } = state;
  if (!topology.version) {
    return (
      <p className="text-xs text-ink-muted">
        {topology.detail.reason ??
          "No dependency map is imported for this application."}
      </p>
    );
  }

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-2 text-[11px]">
        <label className="text-ink-muted" htmlFor="topology-version">
          Map version
        </label>
        <select
          id="topology-version"
          value={version ?? topology.version}
          onChange={(event) => setVersion(event.target.value)}
          className="border border-line bg-surface-raised px-1.5 py-0.5"
        >
          {topology.versions.map((candidate) => (
            <option key={candidate} value={candidate}>
              {candidate}
            </option>
          ))}
        </select>
        <span className="text-ink-muted">
          {topology.node_count} services · {topology.edge_count} dependencies
        </span>
      </div>

      <Legend topology={topology} />

      {topology.delta && !isEmptyDelta(topology.delta) && (
        <p className="border border-line px-2 py-1 text-[11px] text-ink-muted">
          <span className="font-medium">Changed since {topology.delta.from_version}: </span>
          {topology.delta.summary}
          {topology.delta.added_nodes.length > 0 && (
            <> · added {topology.delta.added_nodes.slice(0, 6).join(", ")}</>
          )}
          {topology.delta.removed_nodes.length > 0 && (
            <> · removed {topology.delta.removed_nodes.slice(0, 6).join(", ")}</>
          )}
        </p>
      )}

      <Graph topology={topology} />

      <div className="grid gap-2 sm:grid-cols-2">
        <ImpactList
          title="CONFIRMED"
          caption="something was measured on these"
          tone="danger"
          impacts={topology.impacts.filter((i) => i.confirmation === "confirmed")}
        />
        <ImpactList
          title="PREDICTED"
          caption="these depend on something confirmed; nothing has been measured on them"
          tone="warn"
          impacts={topology.impacts.filter((i) => i.confirmation === "predicted")}
        />
      </div>
    </div>
  );
}

function isEmptyDelta(delta: BrainTopology["delta"]): boolean {
  if (!delta) return true;
  return (
    delta.added_nodes.length === 0 &&
    delta.removed_nodes.length === 0 &&
    delta.added_edges.length === 0 &&
    delta.removed_edges.length === 0 &&
    delta.changed_nodes.length === 0
  );
}

function Legend({ topology }: { topology: BrainTopology }) {
  return (
    <ul className="flex flex-wrap gap-3 text-[11px]">
      <li className="flex items-center gap-1.5">
        <span
          aria-hidden
          className="h-2 w-2 bg-danger"
        />
        <span>
          <strong className="font-medium">CONFIRMED</strong> — measured (
          {topology.confirmed.length})
        </span>
      </li>
      <li className="flex items-center gap-1.5">
        <span
          aria-hidden
          className="h-2 w-2 border border-dashed border-warn"
        />
        <span>
          <strong className="font-medium">PREDICTED</strong> — depends on a
          confirmed fault, not measured ({topology.predicted.length})
        </span>
      </li>
      <li className="flex items-center gap-1.5">
        <span aria-hidden className="h-2 w-2 bg-line" />
        <span>unaffected by the evidence held</span>
      </li>
    </ul>
  );
}

function ImpactList({
  title,
  caption,
  tone,
  impacts,
}: {
  title: string;
  caption: string;
  tone: "danger" | "warn";
  impacts: Impact[];
}) {
  return (
    <section
      aria-label={title}
      className={`border px-2 py-1.5 ${
        tone === "danger" ? "border-danger/40" : "border-warn/40"
      }`}
    >
      <h4
        className={`text-[11px] font-semibold ${
          tone === "danger" ? "text-danger" : "text-warn"
        }`}
      >
        {title}
      </h4>
      <p className="text-[11px] text-ink-muted">{caption}</p>
      {impacts.length === 0 ? (
        <p className="mt-1 text-[11px] text-ink-muted">None.</p>
      ) : (
        <ul className="mt-1 space-y-0.5 text-[11px]">
          {impacts.map((impact) => (
            <li key={impact.service}>
              <span className="font-medium">{impact.service}</span>
              {impact.hops > 0 && (
                <span className="text-ink-muted">
                  {" "}
                  · {impact.hops} hop{impact.hops === 1 ? "" : "s"} ·{" "}
                  {Math.round(impact.likelihood * 100)}% likelihood
                </span>
              )}
              {impact.path.length > 1 && (
                <span className="block text-ink-muted">
                  {impact.path.join(" → ")}
                </span>
              )}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

const NODE_W = 116;
const NODE_H = 26;
const COL_GAP = 46;
const ROW_GAP = 12;

/**
 * A layered dependency graph.
 *
 * Depth is "how many hops from a confirmed fault", which puts the measured
 * services in the left column and everything predicted downstream of them in the
 * columns to the right - the direction a blast radius actually reads.
 */
function Graph({ topology }: { topology: BrainTopology }) {
  const layout = useMemo(() => {
    const state = new Map<string, Impact>();
    for (const impact of topology.impacts) state.set(impact.service, impact);

    const depth = new Map<string, number>();
    for (const node of topology.nodes) {
      const impact = state.get(node.name) ?? state.get(node.id);
      depth.set(node.id, impact ? impact.hops : 99);
    }
    const columns = new Map<number, string[]>();
    for (const node of topology.nodes) {
      const level = depth.get(node.id) ?? 99;
      const bucket = columns.get(level) ?? [];
      bucket.push(node.id);
      columns.set(level, bucket);
    }
    const ordered = Array.from(columns.keys()).sort((a, b) => a - b);
    const position = new Map<string, { x: number; y: number }>();
    ordered.forEach((level, columnIndex) => {
      (columns.get(level) ?? []).forEach((id, rowIndex) => {
        position.set(id, {
          x: columnIndex * (NODE_W + COL_GAP),
          y: rowIndex * (NODE_H + ROW_GAP),
        });
      });
    });
    const width =
      ordered.length * (NODE_W + COL_GAP) - COL_GAP + 4 || NODE_W;
    const height =
      Math.max(
        ...Array.from(columns.values()).map((bucket) => bucket.length),
        1,
      ) *
        (NODE_H + ROW_GAP) -
      ROW_GAP +
      4;
    return { position, width, height, state, depth };
  }, [topology]);

  if (topology.nodes.length === 0) {
    return <p className="text-[11px] text-ink-muted">The map has no services.</p>;
  }

  return (
    // The graph scrolls inside its own box; a war-room page that scrolled
    // sideways because of one wide estate would be unusable on every other one.
    <div className="overflow-x-auto border border-line bg-surface p-2">
      <svg
        width={layout.width}
        height={layout.height}
        viewBox={`0 0 ${layout.width} ${layout.height}`}
        role="img"
        aria-label={`Dependency map version ${topology.version}: ${topology.confirmed.length} confirmed, ${topology.predicted.length} predicted`}
      >
        {topology.edges.map((edge, index) => {
          const from = layout.position.get(edge.source);
          const to = layout.position.get(edge.target);
          if (!from || !to) return null;
          return (
            <line
              key={`${edge.source}-${edge.target}-${index}`}
              x1={from.x + NODE_W / 2}
              y1={from.y + NODE_H / 2}
              x2={to.x + NODE_W / 2}
              y2={to.y + NODE_H / 2}
              stroke="rgb(var(--sf-line))"
              strokeWidth={1}
            />
          );
        })}
        {topology.nodes.map((node) => {
          const at = layout.position.get(node.id);
          if (!at) return null;
          const impact =
            layout.state.get(node.name) ?? layout.state.get(node.id);
          const confirmed = impact?.confirmation === "confirmed";
          const predicted = impact?.confirmation === "predicted";
          return (
            <g key={node.id}>
              <rect
                x={at.x}
                y={at.y}
                width={NODE_W}
                height={NODE_H}
                rx={5}
                fill={
                  confirmed
                    ? "rgb(var(--sf-danger) / 0.15)"
                    : predicted
                      ? "rgb(var(--sf-warn) / 0.12)"
                      : "rgb(var(--sf-surface-raised))"
                }
                stroke={
                  confirmed
                    ? "rgb(var(--sf-danger))"
                    : predicted
                      ? "rgb(var(--sf-warn))"
                      : "rgb(var(--sf-line))"
                }
                strokeWidth={confirmed ? 1.5 : 1}
                strokeDasharray={predicted ? "3 2" : undefined}
              />
              <title>
                {impact
                  ? `${node.name}: ${impact.confirmation.toUpperCase()} — ${impact.reason}`
                  : `${node.name}: not implicated by the evidence held`}
              </title>
              <text
                x={at.x + NODE_W / 2}
                y={at.y + NODE_H / 2 + 3.5}
                textAnchor="middle"
                fontSize={10}
                fill="rgb(var(--sf-ink))"
              >
                {node.name.length > 16 ? `${node.name.slice(0, 15)}…` : node.name}
              </text>
            </g>
          );
        })}
      </svg>
    </div>
  );
}
