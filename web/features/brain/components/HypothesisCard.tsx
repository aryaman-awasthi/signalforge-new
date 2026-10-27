"use client";

import { useState } from "react";

import { BrainNode, Hypothesis } from "@/features/brain/api";
import { Sparkline } from "@/features/brain/components/Sparkline";

/**
 * One hypothesis card in S10 (REQ-053).
 *
 * The card exists to answer one question an incident commander asks out loud:
 * *"why did this move?"* So it shows, in this order, the number, the line it
 * traced, the deterministic explanation of the last delta, the evidence on both
 * sides, and the test that would move it next.
 *
 * Three things it deliberately does *not* do:
 *
 * - It does not compute anything. The percentage, the delta, the direction and
 *   the net weight all come from the server, which computed them from weighted
 *   evidence. A number computed in the browser would be a second opinion nobody
 *   audited.
 * - It does not hide the evidence against. That list is the half a commander
 *   reads second and acts on first, and collapsing it behind a toggle by default
 *   would make a confident-looking card out of a contested one.
 * - It does not present the model's narrative as the explanation. The
 *   deterministic sentence is the explanation and is always shown; the
 *   narrative sits beneath it, labelled, and is absent when the model was not
 *   available.
 */
export function HypothesisCard({
  hypothesis,
  rank,
}: {
  hypothesis: Hypothesis;
  rank: number;
}) {
  const [showEvidence, setShowEvidence] = useState(false);
  const latest = hypothesis.history[hypothesis.history.length - 1];
  const percent = Math.round(hypothesis.confidence * 100);
  // The RCA the brain wrote onto the node. Read as unknown and narrowed,
  // because `detail` is an open record and a missing field is the normal case
  // for a hypothesis the evidence has not yet made specific.
  const detail = hypothesis.node.detail ?? {};
  const rootCause = typeof detail.root_cause === "string" ? detail.root_cause : "";
  const mechanism =
    typeof detail.causal_mechanism === "string" ? detail.causal_mechanism : "";
  const missingEvidence = Array.isArray(detail.missing_evidence)
    ? detail.missing_evidence.filter((item): item is string => typeof item === "string")
    : [];

  return (
    <article
      aria-label={`Hypothesis ${rank}: ${hypothesis.node.label}`}
      className={`bg-surface px-3 py-2.5 ${rank === 1 ? "" : "border-t-2 border-line"}`}
    >
      {/* The prototype's row: ordinal, what it says, where it ranks, how sure -
          and then a bar whose length says the same thing again, because rank has
          to be legible from across a room before any number is read. */}
      <header className="grid grid-cols-[24px_1fr_auto_auto] items-start gap-2.5">
        <span className="pt-0.5 font-mono text-micro text-neutral-500">
          {String(rank).padStart(2, "0")}
        </span>
        <p className="min-w-0 font-display text-h5 font-extrabold leading-tight">
          {hypothesis.node.label}
        </p>
        {rank === 1 && (
          <span className="mt-0.5 bg-brand-100 px-1.5 py-0.5 text-nano font-bold uppercase tracking-label text-brand-800">
            leading
          </span>
        )}
        <span
          className="whitespace-nowrap text-right font-display text-h4 font-extrabold leading-none tabular-nums"
          title="Computed by SignalForge from weighted evidence, not written by a model"
        >
          {percent}%
        </span>
      </header>

      <div className="mt-1.5 h-2 w-full bg-neutral-200">
        <div
          className={`h-2 ${rank === 1 ? "bg-accent" : "bg-neutral-500"}`}
          style={{ width: `${percent}%` }}
        />
      </div>

      <div className="mt-2 flex flex-wrap items-center gap-3">
        <Sparkline
          values={hypothesis.sparkline}
          label={hypothesis.node.label}
        />
        {latest && (
          <p
            className={`text-[11px] ${
              latest.direction === "rose"
                ? "text-danger"
                : latest.direction === "fell"
                  ? "text-ok"
                  : "text-ink-muted"
            }`}
          >
            {latest.previous === null
              ? "first score"
              : `${Math.round(latest.previous * 100)}% → ${percent}% (${latest.direction})`}
          </p>
        )}
      </div>

      {/* The deterministic explanation. Always present, model or no model. */}
      <p className="mt-2 text-[11px] text-ink-muted">{hypothesis.explain}</p>
      {/* The root-cause analysis the brain wrote onto the node: what is broken
          and how it produces the symptom. Rendered from the node detail rather
          than re-derived, so the card and the next-best-action panel cannot
          disagree about the cause they are both reasoning from. */}
      {rootCause && (
        <p className="mt-1 text-[11px]">
          <span className="font-medium">Root cause:</span>{" "}
          <span className="font-mono">{rootCause.replace(/_/g, " ")}</span>
          {mechanism && (
            <>
              {" "}
              <span className="text-ink-muted">via</span>{" "}
              <span className="font-mono">{mechanism.replace(/_/g, " ")}</span>
            </>
          )}
        </p>
      )}
      {missingEvidence.length > 0 && (
        <p className="mt-1 text-[11px] text-ink-muted">
          <span className="font-medium">Would settle it:</span>{" "}
          {missingEvidence.join("; ")}
        </p>
      )}
      {latest?.narrative && (
        <p className="mt-1 border-l-2 border-line pl-2 text-[11px] italic text-ink-muted">
          {latest.narrative}{" "}
          <span className="not-italic">— written by a model</span>
        </p>
      )}

      <dl className="mt-2 grid grid-cols-2 gap-2 text-[11px]">
        <div>
          <dt className="font-medium text-ok">
            Evidence for ({hypothesis.evidence_for.length})
          </dt>
          <dd>
            <EvidenceList nodes={hypothesis.evidence_for} expanded={showEvidence} />
          </dd>
        </div>
        <div>
          <dt className="font-medium text-danger">
            Evidence against ({hypothesis.evidence_against.length})
          </dt>
          <dd>
            <EvidenceList
              nodes={hypothesis.evidence_against}
              expanded={showEvidence}
              empty="Nothing has been tried that could disprove this."
            />
          </dd>
        </div>
      </dl>

      {(hypothesis.evidence_for.length > 2 ||
        hypothesis.evidence_against.length > 2) && (
        <button
          type="button"
          onClick={() => setShowEvidence((shown) => !shown)}
          className="mt-1 text-[11px] text-ink-muted underline decoration-line hover:text-accent-strong"
        >
          {showEvidence ? "show less" : "show all evidence"}
        </button>
      )}

      {hypothesis.discriminating_test && (
        <p className="mt-2 border border-line bg-surface-raised px-2 py-1.5 text-[11px]">
          <span className="font-medium">Next test: </span>
          {hypothesis.discriminating_test}
        </p>
      )}

      {hypothesis.history.length > 1 && (
        <details className="mt-2">
          <summary className="cursor-pointer text-[11px] text-ink-muted">
            Confidence history ({hypothesis.history.length} entries)
          </summary>
          <ol className="mt-1 space-y-1 text-[11px]">
            {[...hypothesis.history].reverse().map((entry, index) => (
              <li key={`${entry.at}-${index}`} className="flex flex-wrap gap-1.5">
                <span className="font-mono text-ink-muted">
                  {entry.at.slice(11, 16)}
                </span>
                <span className="font-mono tabular-nums">
                  {entry.previous === null
                    ? "—"
                    : `${Math.round(entry.previous * 100)}%`}{" "}
                  → {Math.round(entry.current * 100)}%
                </span>
                <span className="text-ink-muted">{entry.explain}</span>
              </li>
            ))}
          </ol>
        </details>
      )}
    </article>
  );
}

function EvidenceList({
  nodes,
  expanded,
  empty = "None recorded.",
}: {
  nodes: BrainNode[];
  expanded: boolean;
  empty?: string;
}) {
  if (nodes.length === 0) {
    return <p className="text-ink-muted">{empty}</p>;
  }
  const shown = expanded ? nodes : nodes.slice(0, 2);
  return (
    <ul className="space-y-0.5">
      {shown.map((node) => (
        <li key={node.id} className="text-ink-muted">
          {node.label}
          {/* The provenance chip: where this came from and how much it counts. */}
          <span className="ml-1 border border-line px-1 text-[10px]">
            {node.provenance.authority}
          </span>
          {node.untrusted && (
            <span
              className="ml-1 border border-warn/50 px-1 text-[10px] text-warn"
              title="Text this platform did not write. Quoted as evidence, never followed as an instruction."
            >
              quoted
            </span>
          )}
        </li>
      ))}
      {!expanded && nodes.length > shown.length && (
        <li className="text-ink-muted">+{nodes.length - shown.length} more</li>
      )}
    </ul>
  );
}
