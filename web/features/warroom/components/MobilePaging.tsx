"use client";

import Link from "next/link";
import { useState } from "react";

import { ApiError } from "@/lib/api/client";
import { Brief, PageRow, acknowledgePage, getBrief } from "@/features/warroom/api";
import {
  formatCountdown,
  useCountdown,
} from "@/features/warroom/hooks/useCountdown";
import { LiveBadge } from "@/features/warroom/components/LiveBadge";
import { PAGER_POLL_MS, useMyPages } from "@/features/warroom/hooks/useWarRoom";

/**
 * Screen 22 - the mobile paging page: acknowledge, and read the join brief.
 *
 * This is the screen somebody opens one-handed, on a phone, at 03:00, while
 * still deciding whether to get out of bed. Everything about it follows from
 * that.
 *
 * - **Two things only.** Acknowledge, and read the brief. Anything else is a
 *   decision that can wait for a laptop.
 * - **Big targets, no confirmation dialog.** Acknowledging is idempotent and
 *   terminal on the server, so a mis-tap costs nothing and a confirmation step
 *   would cost seconds when seconds are the point.
 * - **The countdown is the server's number.** A phone whose clock is off must
 *   not show four minutes remaining while the escalation sweep is already waking
 *   a colleague.
 * - **It reads `/my/pages`**, which has no recipient parameter at all. There is
 *   no way for this screen to show anybody else's pages, which is what lets the
 *   route carry no permission - and the person holding the phone is usually an
 *   SME, who by the frozen roles matrix holds none.
 */
export function MobilePaging() {
  const { state, reload, connected } = useMyPages(true);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [brief, setBrief] = useState<{ pageId: string; brief: Brief } | null>(null);

  async function ack(page: PageRow) {
    setBusy(page.id);
    setError(null);
    try {
      await acknowledgePage(page.incident_id, page.id);
      reload();
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "Could not acknowledge");
    } finally {
      setBusy(null);
    }
  }

  async function showBrief(page: PageRow) {
    setBusy(page.id);
    setError(null);
    try {
      setBrief({ pageId: page.id, brief: await getBrief(page.incident_id) });
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "Could not load the brief");
    } finally {
      setBusy(null);
    }
  }

  if (state.kind === "loading") {
    return <p className="p-4 text-ink-muted">Loading your pages…</p>;
  }
  if (state.kind === "error") {
    return (
      <p className="m-4 rounded-card border border-danger/40 bg-danger/10 px-3 py-2 text-danger">
        {state.message}
      </p>
    );
  }

  const pages = state.pages.pages;

  return (
    <div className="mx-auto max-w-md space-y-4 p-4">
      <header className="flex items-center gap-2">
        <h1 className="text-xl font-semibold">Your pages</h1>
        <span className="flex-1" />
        <LiveBadge connected={connected} pollSeconds={PAGER_POLL_MS / 1000} />
        <span className="text-xs text-ink-muted">{pages.length} open</span>
      </header>

      {error && (
        <p className="rounded-card border border-danger/40 bg-danger/10 px-3 py-2 text-sm text-danger">
          {error}
        </p>
      )}

      {pages.length === 0 ? (
        <p className="rounded-card border border-line bg-surface-raised px-3 py-6 text-center text-sm text-ink-muted">
          Nothing is waiting for you.
        </p>
      ) : (
        <ul className="space-y-3">
          {pages.map((page) => (
            <MobilePage
              key={page.id}
              page={page}
              busy={busy === page.id}
              brief={brief?.pageId === page.id ? brief.brief : null}
              onAck={() => void ack(page)}
              onBrief={() => void showBrief(page)}
            />
          ))}
        </ul>
      )}
    </div>
  );
}

function MobilePage({
  page,
  busy,
  brief,
  onAck,
  onBrief,
}: {
  page: PageRow;
  busy: boolean;
  brief: Brief | null;
  onAck: () => void;
  onBrief: () => void;
}) {
  const remaining = useCountdown(page.countdown_s);

  return (
    <li className="space-y-3 rounded-card border border-line bg-surface-raised p-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className="rounded-full border border-danger/40 bg-danger/10 px-2 py-0.5 font-mono text-sm font-semibold text-danger">
          {page.severity}
        </span>
        {page.role && (
          <span className="rounded-full border border-line px-2 py-0.5 text-[11px] text-ink-muted">
            {page.role}
          </span>
        )}
        <span className="flex-1" />
        {remaining !== null && (
          <span
            className={`font-mono text-sm ${remaining <= 0 ? "text-danger" : remaining < 60 ? "text-warn" : "text-ink-muted"}`}
          >
            {formatCountdown(remaining)}
          </span>
        )}
      </div>

      {/* Why you. The first thing a person wants to know at 03:00. */}
      <p className="text-sm">{page.reason}</p>
      {page.hop > 0 && (
        <p className="text-xs text-warn">
          Escalated to you — the previous page went unanswered.
        </p>
      )}

      <div className="flex gap-2">
        <button
          type="button"
          disabled={busy}
          onClick={onAck}
          className="flex-1 rounded-card bg-accent px-3 py-3 text-base font-semibold text-surface-raised hover:bg-accent-strong disabled:opacity-60"
        >
          Acknowledge
        </button>
        <button
          type="button"
          disabled={busy}
          onClick={onBrief}
          className="rounded-card border border-line px-3 py-3 text-sm hover:border-accent hover:text-accent-strong disabled:opacity-60"
        >
          Brief
        </button>
      </div>

      {brief && (
        <div className="space-y-2 rounded-card border border-line bg-surface p-2.5 text-xs">
          <p className="font-medium">{brief.headline}</p>
          {brief.what_is_happening.map((line) => (
            <p key={line} className="text-ink-muted">
              {line}
            </p>
          ))}
          {brief.what_we_need.length > 0 && (
            <div>
              <p className="font-medium">What we need</p>
              <ul className="text-ink-muted">
                {brief.what_we_need.map((line) => (
                  <li key={line}>{line}</li>
                ))}
              </ul>
            </div>
          )}
          {/* Carried onto the phone as well as the desk. A responder deciding
              what to do next needs to know how much is actually known. */}
          <ul className="border-t border-line pt-1.5 text-warn">
            {brief.caveats.map((caveat) => (
              <li key={caveat}>{caveat}</li>
            ))}
          </ul>
          <Link
            href={`/incidents/${page.incident_id}`}
            className="inline-block underline decoration-line"
          >
            Open the war room
          </Link>
        </div>
      )}
    </li>
  );
}
