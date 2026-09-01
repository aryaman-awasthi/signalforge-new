"use client";

import { useState } from "react";

import { ApiError } from "@/lib/api/client";
import { ValidationView, runValidation } from "@/features/governance/api";
import { Chip, Tone } from "@/features/governance/components/Chips";

/**
 * S16 - the four validation layers, the stability ring and the closure lock.
 *
 * Four tiles rather than one score, because the four fail separately and the gap
 * between them is the expensive thing: technical green and service green while
 * customers still cannot pay is what a platform that closes on infrastructure
 * health looks like from the outside.
 *
 * Every check names the system it was read from, and the panel says so out loud.
 * None of them is the Incident Brain - the server refuses to construct a check
 * whose source names it - because asking the reasoning engine whether the system
 * is healthy is asking the platform to mark its own homework.
 *
 * The ring fills with the stability window. A regression empties it rather than
 * winding it back, which is what the number under it says.
 */

const VERDICT: Record<string, { tone: Tone; label: string }> = {
  pass: { tone: "ok", label: "pass" },
  fail: { tone: "danger", label: "fail" },
  unknown: { tone: "warn", label: "could not read" },
  not_applicable: { tone: "neutral", label: "not applicable" },
};

const LAYER_QUESTION: Record<string, string> = {
  technical: "Is it running and configured?",
  service: "Is it serving?",
  customer: "Are people getting what they came for?",
  business: "Did the money start moving again?",
};

export function ValidationPanel({
  incidentId,
  validation,
  canCommand,
  onChanged,
}: {
  incidentId: string;
  validation: ValidationView | null;
  canCommand: boolean;
  onChanged: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const latest = validation?.latest ?? null;

  async function run() {
    setBusy(true);
    setError(null);
    try {
      await runValidation(incidentId);
      onChanged();
    } catch (caught) {
      setError(
        caught instanceof ApiError ? caught.message : "Validation could not run",
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-2">
      {error && (
        <p className="rounded-card border border-danger/40 bg-danger/10 px-2 py-1.5 text-xs text-danger">
          {error}
        </p>
      )}

      <div className="flex flex-wrap items-center gap-2">
        {latest && (
          <>
            <Chip
              tone={
                latest.verdict === "recovered"
                  ? "ok"
                  : latest.verdict === "stabilising"
                    ? "warn"
                    : "danger"
              }
            >
              {latest.verdict.replace("_", " ")}
            </Chip>
            <span className="text-[11px] text-ink-muted">
              cycle {latest.cycle}
              {validation && validation.failed_cycles > 0 &&
                ` · ${validation.failed_cycles} failed`}
              {latest.escalated && " · escalated"}
            </span>
          </>
        )}
        <span className="flex-1" />
        {canCommand && (
          <button
            type="button"
            disabled={busy}
            onClick={() => void run()}
            className="rounded-card border border-line px-2.5 py-1 text-xs hover:border-accent disabled:opacity-50"
          >
            {busy ? "Checking…" : "Validate recovery"}
          </button>
        )}
      </div>

      {!latest ? (
        <p className="text-xs text-ink-muted">
          Recovery has not been validated. Validation reads the application
          itself - health, rollups, journey probes and business counters - and
          never the incident brain.
        </p>
      ) : (
        <>
          <div className="grid gap-1.5 sm:grid-cols-2">
            {latest.layers.map((layer) => {
              const style = VERDICT[layer.verdict] ?? VERDICT.unknown;
              return (
                <div
                  key={layer.layer}
                  className={`rounded-card border px-2 py-1.5 ${
                    layer.verdict === "fail"
                      ? "border-danger/50 bg-danger/10"
                      : "border-line"
                  }`}
                >
                  <div className="flex items-center gap-2">
                    <span className="text-xs font-semibold capitalize">
                      {layer.layer}
                    </span>
                    <Chip tone={style.tone}>{style.label}</Chip>
                  </div>
                  <p className="mt-0.5 text-[10px] text-ink-muted">
                    {LAYER_QUESTION[layer.layer]}
                  </p>
                  <p className="mt-0.5 text-[11px]">{layer.summary}</p>
                  <ul className="mt-1 space-y-0.5">
                    {layer.checks.slice(0, 4).map((check) => (
                      <li
                        key={check.check_id}
                        className="flex flex-wrap items-center gap-1 text-[10px]"
                      >
                        <span
                          aria-hidden
                          className={`h-1.5 w-1.5 rounded-full ${
                            check.verdict === "pass"
                              ? "bg-ok"
                              : check.verdict === "fail"
                                ? "bg-danger"
                                : "bg-line"
                          }`}
                        />
                        <span>{check.label}</span>
                        <span className="text-ink-muted" title={check.result_ref}>
                          read from {check.source}
                        </span>
                      </li>
                    ))}
                  </ul>
                </div>
              );
            })}
          </div>

          {latest.window && <Ring window={latest.window} />}

          {latest.closure_blocked && (
            <p className="rounded-card border border-danger/40 bg-danger/10 px-2 py-1.5 text-[11px] text-danger">
              🔒 Closure is locked. {latest.closure_lock_reason}
            </p>
          )}
          {latest.returned_to_active && (
            <p className="text-[11px] text-warn">
              This incident was moved back to Active because the validation did
              not confirm recovery.
            </p>
          )}
          {latest.evidence_node_ids.length > 0 && (
            <p className="text-[11px] text-ink-muted">
              The failure was written back into the incident&apos;s record as
              evidence ({latest.evidence_node_ids.length} node
              {latest.evidence_node_ids.length === 1 ? "" : "s"}), so the
              hypotheses can move on it.
            </p>
          )}
        </>
      )}
    </div>
  );
}

function Ring({
  window: stability,
}: {
  window: NonNullable<ValidationView["latest"]>["window"];
}) {
  if (!stability) return null;
  const fraction =
    stability.required_s > 0
      ? Math.min(1, stability.elapsed_s / stability.required_s)
      : 1;
  return (
    <div className="flex flex-wrap items-center gap-2 rounded-card border border-line px-2 py-1.5">
      <span className="text-[10px] font-semibold uppercase tracking-wide text-ink-muted">
        Stability
      </span>
      <span className="h-1.5 w-32 rounded-full bg-line">
        <span
          style={{ width: `${Math.round(fraction * 100)}%` }}
          className={`block h-1.5 rounded-full ${stability.satisfied ? "bg-ok" : "bg-warn"}`}
        />
      </span>
      <span className="text-[11px] text-ink-muted">
        {stability.consecutive_passes} of {stability.required_samples} samples ·{" "}
        {stability.remaining_s > 0
          ? `${Math.ceil(stability.remaining_s / 60)} min to go`
          : "window held"}
      </span>
      {stability.regressions > 0 && (
        <span className="text-[11px] text-warn">
          reset {stability.regressions}×
          {stability.last_regression_reason &&
            ` · last: ${stability.last_regression_reason}`}
        </span>
      )}
    </div>
  );
}
