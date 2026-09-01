"use client";

import { useState } from "react";

import { ApiError } from "@/lib/api/client";
import { Nba, revisePlan } from "@/features/governance/api";
import { Chip, Tone } from "@/features/governance/components/Chips";

/**
 * The plan-diff triad: KEPT, PAUSED, ADDED.
 *
 * A response plan is never replaced. When evidence moves, the platform writes
 * version n+1 and marks every action against version n - and a paused action
 * stays on the screen with the reason it was paused and the assumption that
 * changed. "We stopped doing that at 03:14 because the pool metric came back
 * clean" is the sentence a post-incident review is made of, and a plan that
 * silently rewrote itself would have deleted it.
 */

const DISPOSITION: Record<string, { tone: Tone; label: string }> = {
  kept: { tone: "neutral", label: "kept" },
  paused: { tone: "warn", label: "paused" },
  added: { tone: "accent", label: "added" },
};

export function PlanDiff({
  incidentId,
  nba,
  canCommand,
  onChanged,
}: {
  incidentId: string;
  nba: Nba | null;
  canCommand: boolean;
  onChanged: () => void;
}) {
  const [assumption, setAssumption] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const plan = nba?.plan ?? null;

  async function revise() {
    setBusy(true);
    setError(null);
    try {
      await revisePlan(
        incidentId,
        assumption.trim() ? [assumption.trim()] : [],
        "",
      );
      setAssumption("");
      onChanged();
    } catch (caught) {
      setError(
        caught instanceof ApiError ? caught.message : "Could not revise the plan",
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

      {plan ? (
        <>
          <p className="text-[11px] text-ink-muted">
            Version {plan.version}
            {plan.previous_version !== null &&
              ` · against version ${plan.previous_version}`}{" "}
            · {plan.kept.length} kept, {plan.paused.length} paused,{" "}
            {plan.added.length} added
          </p>
          {plan.changed_assumptions.length > 0 && (
            <ul className="space-y-0.5 text-[11px] text-warn">
              {plan.changed_assumptions.map((line) => (
                <li key={line}>What changed: {line}</li>
              ))}
            </ul>
          )}
          <ul className="space-y-1">
            {plan.items.map((item) => {
              const style = DISPOSITION[item.disposition] ?? DISPOSITION.kept;
              return (
                <li
                  key={`${item.action_id}-${item.disposition}`}
                  className="rounded-card border border-line px-2 py-1.5 text-xs"
                >
                  <div className="flex flex-wrap items-center gap-2">
                    <Chip tone={style.tone}>{style.label}</Chip>
                    <span
                      className={
                        item.disposition === "paused"
                          ? "text-ink-muted line-through"
                          : "font-medium"
                      }
                    >
                      {item.name}
                    </span>
                    <span className="flex-1" />
                    <span className="font-mono text-[10px] text-ink-muted">
                      {item.score >= 0 ? "+" : ""}
                      {item.score.toFixed(2)}
                    </span>
                  </div>
                  <p className="mt-0.5 text-[11px] text-ink-muted">
                    {item.reason}
                  </p>
                  {item.changed_assumption && (
                    <p className="mt-0.5 text-[11px] text-warn">
                      because: {item.changed_assumption}
                    </p>
                  )}
                </li>
              );
            })}
          </ul>
        </>
      ) : (
        <p className="text-xs text-ink-muted">
          No plan has been written yet. Revising records what the room decided to
          do, and every later revision is a diff against this one rather than a
          replacement.
        </p>
      )}

      {canCommand && (
        <div className="flex flex-wrap items-end gap-2">
          <label className="flex-1 text-[11px]">
            <span className="text-ink-muted">
              What changed since the last plan?
            </span>
            <input
              value={assumption}
              onChange={(event) => setAssumption(event.target.value)}
              placeholder="the pool metric came back clean"
              className="mt-1 w-full rounded-card border border-line bg-surface px-2 py-1 text-xs"
            />
          </label>
          <button
            type="button"
            disabled={busy}
            onClick={() => void revise()}
            className="rounded-card border border-line px-3 py-1.5 text-xs hover:border-accent disabled:opacity-50"
          >
            {plan ? "Revise the plan" : "Write the plan"}
          </button>
        </div>
      )}
    </div>
  );
}
