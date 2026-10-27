"use client";

import { useState } from "react";

import { ApiError } from "@/lib/api/client";
import {
  PageRow,
  acknowledgePage,
  escalatePage,
} from "@/features/warroom/api";
import {
  formatCountdown,
  useCountdown,
} from "@/features/warroom/hooks/useCountdown";

/**
 * S8 - the acknowledgement board.
 *
 * One row per person asked, each with its own timer and, next to it, **the
 * reason they were asked**. The reason is not decoration: somebody woken at
 * 03:00 deserves to know why it was them, and a board that showed only names and
 * timers would make every page look interchangeable.
 *
 * Three things this board deliberately does not do:
 *
 * - It does not compute its own countdown. The number comes from the server (see
 *   `useCountdown`), so a laptop with a skewed clock cannot show a responder two
 *   minutes remaining while the escalation sweep is already waking somebody else.
 * - It does not show an Acknowledge button on somebody else's row. The API would
 *   refuse it - a page is answered by the person it names - and rendering a
 *   button that is guaranteed to 403 teaches people the product is unreliable.
 * - It does not draw a `failed` page as though it were counting down. A page no
 *   channel could carry reached nobody, and it says so with the reason.
 */

const STATE_TONE: Record<string, string> = {
  pending: "border-line bg-surface text-ink-muted",
  sent: "border-warn/40 bg-warn/10 text-warn",
  acked: "border-ok/40 bg-ok/10 text-ok",
  escalated: "border-line bg-surface text-ink-muted",
  failed: "border-danger/40 bg-danger/10 text-danger",
  cancelled: "border-line bg-surface text-ink-muted",
};

function Timer({ page }: { page: PageRow }) {
  const remaining = useCountdown(page.countdown_s);
  if (page.state === "acked") {
    return (
      <span className="font-mono text-xs text-ok">
        answered{page.acked_at ? "" : ""}
      </span>
    );
  }
  if (remaining === null) {
    return <span className="font-mono text-xs text-ink-muted">—</span>;
  }
  return (
    <span
      className={`font-mono text-xs ${remaining <= 0 ? "text-danger" : remaining < 60 ? "text-warn" : ""}`}
    >
      {formatCountdown(remaining)}
    </span>
  );
}

export function AckBoard({
  incidentId,
  pages,
  currentUserId,
  canEscalate,
  onChanged,
  names = {},
}: {
  incidentId: string;
  pages: PageRow[];
  /** Who is looking. Used only to decide whose row gets an Acknowledge button;
   * the API enforces the same rule regardless of what is rendered. */
  currentUserId: string | null;
  canEscalate: boolean;
  onChanged: () => void;
  /** user id -> display name, from the room's members. An escalation hop
   * carries the recipient's id and no display name; the room already knows
   * the person, so the board does not print an id. */
  names?: Record<string, string>;
}) {
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function run(pageId: string, work: () => Promise<unknown>) {
    setBusy(pageId);
    setError(null);
    try {
      await work();
      onChanged();
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "That did not work");
    } finally {
      setBusy(null);
    }
  }

  if (pages.length === 0) {
    return (
      <p className="text-xs text-ink-muted">
        Nobody has been paged: the blast radius called for no paged role, or
        nobody holds the roles it called for.
      </p>
    );
  }

  // A page with no channel to carry it lands FAILED and stays there - nothing
  // escalates a page that reached nobody. When that is every page in the room,
  // the board has to say so above the rows rather than let a column of "failed"
  // read like a column of people who did not answer.
  const allFailed = pages.every((page) => page.state === "failed");

  return (
    <div className="space-y-2">
      {allFailed && (
        <p className="rounded-card border border-danger/40 bg-danger/10 px-2.5 py-1.5 text-xs text-danger">
          Every page failed: no notification connector bound to this application
          could carry one, so nobody was reached. Bind{" "}
          <span className="font-mono">notify-in-app</span> or{" "}
          <span className="font-mono">page-sim</span> on the application&apos;s
          Connect step, then page again.
        </p>
      )}
      {error && (
        <p className="rounded-card border border-danger/40 bg-danger/10 px-2.5 py-1.5 text-sm text-danger">
          {error}
        </p>
      )}
      <ul className="divide-y divide-line rounded-card border border-line bg-surface-raised">
        {pages.map((page) => {
          const mine = currentUserId !== null && page.recipient === currentUserId;
          return (
            <li key={page.id} className="flex flex-wrap items-start gap-3 px-3 py-2">
              <div className="min-w-48 flex-1">
                <div className="flex flex-wrap items-center gap-2 text-sm">
                  <span className="font-medium">
                    {page.recipient_display || names[page.recipient] || page.recipient}
                  </span>
                  {page.role && (
                    <span className="border border-line px-2 py-0.5 text-[11px] text-ink-muted">
                      {page.role}
                    </span>
                  )}
                  {page.hop > 0 && (
                    <span className="border border-line px-2 py-0.5 text-[11px] text-ink-muted">
                      escalation hop {page.hop}
                    </span>
                  )}
                </div>
                {/* Why this person. Rendered on every row, always. */}
                <p className="mt-0.5 text-xs text-ink-muted">{page.reason}</p>
                {page.state === "failed" && (
                  <p className="mt-0.5 text-xs text-danger">{page.detail}</p>
                )}
                {page.ack_note && (
                  <p className="mt-0.5 text-xs text-ok">“{page.ack_note}”</p>
                )}
                {page.deliveries.length > 0 && (
                  <ul className="mt-1 flex flex-wrap gap-1.5">
                    {page.deliveries.map((delivery) => (
                      <li
                        key={delivery.notification_id}
                        title={delivery.detail}
                        className="border border-line px-2 py-0.5 text-[10px] text-ink-muted"
                      >
                        {delivery.channel} · {delivery.status}
                        {delivery.simulated ? " · SIMULATED" : ""}
                      </li>
                    ))}
                  </ul>
                )}
              </div>

              <span
                className={`border px-2 py-0.5 text-xs ${
                  STATE_TONE[page.state] ?? STATE_TONE.pending
                }`}
              >
                {page.state}
              </span>
              <Timer page={page} />

              <div className="flex gap-2">
                {mine && page.state !== "acked" && page.state !== "failed" && (
                  <button
                    type="button"
                    disabled={busy === page.id}
                    onClick={() =>
                      void run(page.id, () => acknowledgePage(incidentId, page.id))
                    }
                    className="rounded-card bg-accent px-2.5 py-1 text-xs font-medium text-surface-raised hover:bg-accent-strong disabled:opacity-60"
                  >
                    Acknowledge
                  </button>
                )}
                {canEscalate &&
                  (page.state === "sent" || page.state === "pending") && (
                    <button
                      type="button"
                      disabled={busy === page.id}
                      onClick={() =>
                        void run(page.id, () => escalatePage(incidentId, page.id))
                      }
                      className="rounded-card border border-line px-2.5 py-1 text-xs hover:border-accent hover:text-accent-strong disabled:opacity-60"
                      title="Page the next person now instead of waiting for the countdown"
                    >
                      Escalate now
                    </button>
                  )}
              </div>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
