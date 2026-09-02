"use client";

import { useState } from "react";

import { ApiError } from "@/lib/api/client";
import { DeadLetter, requeueDeadLetter } from "@/features/platform/api";

const STATE_STYLE: Record<DeadLetter["state"], string> = {
  dead: "border-danger/40 bg-danger/10 text-danger",
  requeued: "border-warn/40 bg-warn/10 text-warn",
  resolved: "border-ok/40 bg-ok/10 text-ok",
};

/**
 * The operator pane for events a handler could not process. Requeue is the only
 * action: it asks the dispatcher to deliver the event again, and because the
 * idempotency ledger is checked first, pressing it on something that actually
 * succeeded is harmless.
 */
export function DeadLettersPanel({
  deadLetters,
  onChanged,
}: {
  deadLetters: DeadLetter[];
  onChanged: () => void;
}) {
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const requeue = async (id: string) => {
    setBusy(id);
    setError(null);
    setNotice(null);
    try {
      const result = await requeueDeadLetter(id);
      if (!result.drained_here) {
        setNotice(
          `Queued for redelivery, but no consumer for "${result.group}" runs on this node. ` +
            "It moves when a worker owning that group runs.",
        );
      }
      onChanged();
    } catch (failure) {
      setError(
        failure instanceof ApiError ? failure.message : "Could not requeue that event",
      );
    } finally {
      setBusy(null);
    }
  };

  if (deadLetters.length === 0) {
    return (
      <p className="rounded-card border border-line bg-surface-raised px-3 py-6 text-sm text-ink-muted">
        No dead letters. Every event delivered.
      </p>
    );
  }

  return (
    <div className="space-y-3">
      {error && (
        <p className="rounded-card border border-danger/40 bg-danger/10 px-3 py-2 text-sm text-danger">
          {error}
        </p>
      )}
      {notice && (
        <p className="rounded-card border border-warn/40 bg-warn/10 px-3 py-2 text-sm text-warn">
          {notice}
        </p>
      )}
      <ul className="divide-y divide-line rounded-card border border-line bg-surface-raised">
        {deadLetters.map((row) => (
          <li key={row.id} className="space-y-1 px-3 py-3 text-sm">
            <div className="flex flex-wrap items-center gap-2">
              <span
                className={`border px-2 py-0.5 text-[11px] uppercase ${
                  STATE_STYLE[row.state]
                }`}
              >
                {row.state}
              </span>
              <span className="font-medium">{row.topic}</span>
              <span className="font-mono text-xs text-ink-muted">
                #{row.event_seq} · group {row.group} · lane {row.lane} · {row.attempts}{" "}
                attempts
              </span>
              <span className="flex-1" />
              {row.state === "dead" && (
                <button
                  disabled={busy === row.id}
                  onClick={() => void requeue(row.id)}
                  className="rounded-card border border-line px-2.5 py-1 text-xs hover:border-accent hover:text-accent-strong disabled:opacity-50"
                >
                  {busy === row.id ? "Requeueing…" : "Requeue"}
                </button>
              )}
            </div>
            <p className="font-mono text-xs text-danger">{row.error}</p>
            <p className="text-xs text-ink-muted">
              partition {row.partition_key} · first failed {row.first_failed_at} · last{" "}
              {row.last_failed_at}
              {row.requeued_by ? ` · requeued by ${row.requeued_by}` : ""}
            </p>
          </li>
        ))}
      </ul>
    </div>
  );
}
