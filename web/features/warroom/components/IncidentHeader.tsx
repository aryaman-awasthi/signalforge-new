"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { LiveBadge } from "@/features/warroom/components/LiveBadge";
import { SeverityPill, StatePill } from "@/lib/ui/primitives";

/**
 * The incident status strip (S8-T04, S8-T05).
 *
 * The prototype puts six things in one line above everything else, and the order
 * is the order a person reads them in when they walk in: which incident, what it
 * is, how bad, what state, whether the screen is live, and who is running it.
 * The elapsed clock sits hard right, because it is the one number that is always
 * changing and the eye should not have to hunt for it.
 *
 * The elapsed clock is the one number here derived from the browser's own clock,
 * and it is deliberately the only one. Everything that *decides* something - the
 * escalation countdown on the ack board, the approval TTL - reads a remaining
 * duration the server computed, because a responder's laptop can be minutes off
 * and the sweep that actually escalates does not use this screen's clock. This
 * number decides nothing: it says roughly how long the room has been open, and
 * a reader who needs the exact moment has `opened_at` on the overview panel and
 * the audit replay behind it.
 */

function useElapsed(openedAt: string): string {
  const [seconds, setSeconds] = useState(() => sinceIso(openedAt));
  useEffect(() => {
    setSeconds(sinceIso(openedAt));
    const timer = setInterval(() => setSeconds((value) => value + 1), 1000);
    return () => clearInterval(timer);
  }, [openedAt]);
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  const rest = seconds % 60;
  return hours > 0
    ? `${hours}:${String(minutes).padStart(2, "0")}:${String(rest).padStart(2, "0")}`
    : `${String(minutes).padStart(2, "0")}:${String(rest).padStart(2, "0")}`;
}

function sinceIso(iso: string): number {
  const opened = Date.parse(iso);
  if (Number.isNaN(opened)) return 0;
  return Math.max(0, Math.floor((Date.now() - opened) / 1000));
}

export function IncidentHeader({
  reference,
  title,
  severity,
  state,
  appId,
  openedAt,
  connected,
  pollSeconds,
  presentCount,
  radiusCount,
  commander,
  degraded = false,
  actions,
}: {
  reference: string;
  title: string;
  severity: string;
  state: string;
  appId: string;
  openedAt: string;
  connected: boolean;
  pollSeconds: number;
  presentCount: number;
  radiusCount: number;
  commander?: string | null;
  degraded?: boolean;
  actions?: React.ReactNode;
}) {
  const elapsed = useElapsed(openedAt);
  return (
    <header className="flex flex-wrap items-center gap-3.5 border-b-2 border-line pb-3.5">
      <span className="font-mono text-dense">{reference}</span>
      <h1 className="text-h3">{title}</h1>
      <SeverityPill severity={severity} />
      <StatePill state={state} />
      {degraded && <StatePill state="degraded" />}
      <Link
        href={`/apps/${encodeURIComponent(appId)}`}
        className="text-small text-ink-muted underline decoration-line underline-offset-2 hover:text-accent"
      >
        {appId}
      </Link>
      {commander && (
        <span className="text-dense text-ink-muted">IC — {commander}</span>
      )}
      <span className="ml-auto flex items-center gap-3">
        <LiveBadge connected={connected} pollSeconds={pollSeconds} />
        <span className="text-small text-ink-muted">
          {presentCount} in the room · {radiusCount} in the radius
        </span>
        <span className="font-mono text-h5 font-semibold tabular-nums">{elapsed}</span>
        <span className="text-micro text-ink-muted">elapsed</span>
        {actions}
      </span>
    </header>
  );
}
