"use client";

import { useState } from "react";

import { ApiError } from "@/lib/api/client";
import { refreshBrain } from "@/features/brain/api";
import { HypothesisCard } from "@/features/brain/components/HypothesisCard";
import { Ledger } from "@/features/brain/components/Ledger";
import { BrainState } from "@/features/brain/hooks/useBrain";

/**
 * S10 — AI Incident Brain (REQ-050, REQ-053).
 *
 * Two halves in fixed order: the hypotheses, because that is what an incident
 * commander opens this for, and then the ledger they were computed from.
 *
 * The DEGRADED banner is the first thing rendered when it applies, and it says
 * what still works rather than only what does not. "The model is unavailable" on
 * its own reads as "this panel is broken"; the useful sentence is that the
 * evidence, the confidences and the history below were all computed without one.
 *
 * Everything here is readable by everyone in the tenant. Running a pass is not:
 * it writes the platform's beliefs about a live incident, so it carries
 * `declare_incident` on the API (DR-101). The button is hidden rather than shown
 * and refused - a control that only answers 403 teaches people the product is
 * broken - and the panel says who can, so a paged SME knows what to ask for
 * rather than wondering where the button went.
 *
 * `compact` is the cockpit (S9): the prototype's four counters - what is known,
 * what is assumed, what is contradicted, what is measured - over the ranked
 * hypotheses as rows. No ledger, no agent runs; those are what "Expand the
 * brain" is for.
 */
export function BrainPanel({
  incidentId,
  state,
  canRefresh,
  onRefreshed,
  compact = false,
  contradictions = 0,
}: {
  incidentId: string;
  state: BrainState;
  canRefresh: boolean;
  onRefreshed: () => void;
  compact?: boolean;
  /** From the guidance route: statements the measurements disagree with. */
  contradictions?: number;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (state.kind === "loading") {
    return <p className="text-xs text-ink-muted">Reading the incident brain…</p>;
  }
  if (state.kind === "error") {
    return <p className="text-xs text-danger">{state.message}</p>;
  }

  const { brain } = state;

  async function think() {
    setBusy(true);
    setError(null);
    try {
      await refreshBrain(incidentId);
      onRefreshed();
    } catch (caught) {
      setError(
        caught instanceof ApiError ? caught.message : "Could not run a pass",
      );
    } finally {
      setBusy(false);
    }
  }

  const current = (type: string) =>
    brain.nodes.filter((node) => node.node_type === type && node.status === "current")
      .length;

  const refresh = canRefresh ? (
    <button
      type="button"
      disabled={busy}
      onClick={() => void think()}
      className="border border-line px-2 py-1 text-[11px] hover:border-accent hover:text-accent-strong disabled:opacity-60"
    >
      {busy ? "thinking…" : "re-reason now"}
    </button>
  ) : (
    <span className="text-[11px] text-ink-muted">incident command runs the reasoning</span>
  );

  if (compact) {
    return (
      <div className="flex flex-col gap-3">
        {brain.degraded && (
          <p role="status" className="border border-warn bg-warn-bg px-2 py-1.5 text-micro text-warn-ink">
            <strong className="font-semibold">AI degraded.</strong> The evidence,
            the confidences and the history are still computed; only the written
            narrative is missing.
          </p>
        )}

        {/* The prototype's counter strip. Facts and assumptions are the two
            columns of the ledger; contradictions are the one number that should
            be zero; evidence is how much was actually measured. */}
        <dl className="grid grid-cols-4 divide-x divide-line border-b-2 border-line">
          <Counter label="Verified facts" value={current("fact")} />
          <Counter label="Assumptions" value={current("assumption")} />
          <Counter
            label="Contradictions"
            value={contradictions}
            tone={contradictions > 0 ? "text-accent-strong" : undefined}
          />
          <Counter label="Evidence" value={current("evidence")} />
        </dl>

        {error && <p className="text-[11px] text-danger">{error}</p>}

        {brain.hypotheses.length === 0 ? (
          <div className="flex flex-wrap items-center gap-2">
            <p className="text-small text-ink-muted">
              No hypothesis has been raised yet.{" "}
              {canRefresh ? "Press “re-reason now”, or wait" : "Waiting"} for the
              next event to wake the brain.
            </p>
            <span className="ml-auto">{refresh}</span>
          </div>
        ) : (
          <div className="flex flex-col">
            {brain.hypotheses.map((hypothesis, index) => (
              <HypothesisCard
                key={hypothesis.node.id}
                hypothesis={hypothesis}
                rank={index + 1}
                compact
              />
            ))}
            <div className="flex items-center gap-2 border-t border-line pt-2 text-[11px] text-ink-muted">
              <span>confidence is arithmetic over weighted evidence, never written by a model</span>
              <span className="ml-auto">{refresh}</span>
            </div>
          </div>
        )}
      </div>
    );
  }

  return (
    <div className="space-y-3">
      {brain.degraded && (
        <p
          role="status"
          className="rounded-card border border-warn/40 bg-warn/10 px-2 py-1.5 text-[11px] text-warn"
        >
          <strong className="font-medium">AI degraded.</strong>{" "}
          {brain.degraded_reason ||
            "the model path is not answering"}
          . Everything below was still computed: the evidence is measured, the
          confidences are arithmetic over it, and the history explains every
          movement. Only the written narrative is missing.
        </p>
      )}

      <div className="flex flex-wrap items-center gap-2">
        <h3 className="text-xs font-semibold uppercase tracking-wide text-ink-muted">
          Competing hypotheses
        </h3>
        <span className="text-[11px] text-ink-muted">
          confidence is computed from weighted evidence, never written by a model
        </span>
        <span className="flex-1" />
        {refresh}
      </div>

      {error && <p className="text-[11px] text-danger">{error}</p>}

      {brain.hypotheses.length === 0 ? (
        <p className="text-xs text-ink-muted">
          No hypothesis has been raised yet.{" "}
          {canRefresh ? "Press “re-reason now”, or wait" : "Waiting"} for the
          next event to wake the brain.
        </p>
      ) : (
        // One hypothesis per row, ranked, as the prototype has it. Three cards
        // side by side made rank a matter of reading three headings; stacked,
        // the confidence bars line up under each other and the leading
        // hypothesis is obvious from the length of a bar before a word is read.
        <div className="flex flex-col gap-2.5">
          {brain.hypotheses.map((hypothesis, index) => (
            <HypothesisCard
              key={hypothesis.node.id}
              hypothesis={hypothesis}
              rank={index + 1}
            />
          ))}
        </div>
      )}

      <div className="border-t border-line pt-2">
        <h3 className="mb-1.5 text-xs font-semibold uppercase tracking-wide text-ink-muted">
          Ledger
        </h3>
        <Ledger incidentId={incidentId} nodes={brain.nodes} />
      </div>

      {brain.runs.length > 0 && (
        <details className="border-t border-line pt-2">
          <summary className="cursor-pointer text-[11px] text-ink-muted">
            Agent runs ({brain.runs.length}) — which agent ran, on what, with
            which model
          </summary>
          <ul className="mt-1 space-y-0.5 text-[11px] text-ink-muted">
            {brain.runs.map((run) => (
              <li key={run.id} className="flex flex-wrap gap-1.5">
                <span className="font-mono">{run.started_at.slice(11, 19)}</span>
                <span>{run.agent}</span>
                <span
                  className={
                    run.status === "ok"
                      ? "text-ok"
                      : run.status === "failed"
                        ? "text-danger"
                        : "text-warn"
                  }
                >
                  {run.status}
                </span>
                {run.model && <span>{run.model}</span>}
                {run.tokens > 0 && <span>{run.tokens} tokens</span>}
              </li>
            ))}
          </ul>
        </details>
      )}
    </div>
  );
}

function Counter({
  label,
  value,
  tone,
}: {
  label: string;
  value: number;
  tone?: string;
}) {
  return (
    <div className="flex flex-col gap-1 px-3 py-2 first:pl-0">
      <dd className={`font-display text-h3 font-extrabold leading-none tabular-nums ${tone ?? ""}`}>
        {value}
      </dd>
      <dt className="text-nano uppercase tracking-label text-neutral-600">{label}</dt>
    </div>
  );
}
