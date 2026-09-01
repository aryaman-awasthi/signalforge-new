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
 */
export function BrainPanel({
  incidentId,
  state,
  canRefresh,
  onRefreshed,
}: {
  incidentId: string;
  state: BrainState;
  canRefresh: boolean;
  onRefreshed: () => void;
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
        {canRefresh ? (
          <button
            type="button"
            disabled={busy}
            onClick={() => void think()}
            className="rounded-card border border-line px-2 py-1 text-[11px] hover:border-accent hover:text-accent-strong disabled:opacity-60"
          >
            {busy ? "thinking…" : "re-reason now"}
          </button>
        ) : (
          <span className="text-[11px] text-ink-muted">
            incident command runs the reasoning
          </span>
        )}
      </div>

      {error && <p className="text-[11px] text-danger">{error}</p>}

      {brain.hypotheses.length === 0 ? (
        <p className="text-xs text-ink-muted">
          No hypothesis has been raised yet.{" "}
          {canRefresh ? "Press “re-reason now”, or wait" : "Waiting"} for the
          next event to wake the brain.
        </p>
      ) : (
        <div className="grid gap-2 md:grid-cols-2 xl:grid-cols-3">
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
