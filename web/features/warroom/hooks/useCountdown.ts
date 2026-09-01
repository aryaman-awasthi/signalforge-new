"use client";

import { useEffect, useState } from "react";

/**
 * The ack board's ticking clock (REQ-045).
 *
 * It ticks from the **server's** `countdown_s`, not from `Date.now()` against a
 * parsed deadline. That is the whole design of this hook and it is worth being
 * explicit about, because the obvious implementation is wrong in a way nobody
 * notices until it matters:
 *
 * A responder's laptop clock can be minutes off. If the browser computed
 * `deadline - Date.now()`, that screen would show a different number from the
 * escalation sweep - and the sweep is the thing that actually escalates. Somebody
 * would watch two minutes remaining while a colleague was already being woken,
 * and would reasonably conclude the pager is broken.
 *
 * So the server sends how many seconds are left at the moment of the read, the
 * browser counts down locally from that number for smoothness, and every poll
 * re-anchors it. Local drift between polls is bounded by the poll interval and
 * is never cumulative.
 */
export function useCountdown(seconds: number | null, tickMs = 1000): number | null {
  const [remaining, setRemaining] = useState<number | null>(seconds);

  // Re-anchor whenever the server speaks. The dependency is the value itself,
  // so a poll that returns the same number does not restart the interval.
  useEffect(() => {
    setRemaining(seconds);
  }, [seconds]);

  useEffect(() => {
    if (seconds === null) return;
    const handle = setInterval(() => {
      setRemaining((current) => (current === null ? null : current - tickMs / 1000));
    }, tickMs);
    return () => clearInterval(handle);
  }, [seconds, tickMs]);

  return remaining;
}

/**
 * A countdown as a person reads it: `4:37`, or `overdue by 1:12`.
 *
 * Overdue is rendered as elapsed time rather than as a negative number, because
 * "-72" is arithmetic and "overdue by 1:12" is information. A settled page has
 * no countdown at all rather than a frozen one, and this returns an empty string
 * so a caller renders nothing rather than `0:00`, which would read as "about to
 * escalate" for a page that never will.
 */
export function formatCountdown(seconds: number | null): string {
  if (seconds === null) return "";
  const overdue = seconds < 0;
  const total = Math.max(0, Math.floor(Math.abs(seconds)));
  const minutes = Math.floor(total / 60);
  const rest = String(total % 60).padStart(2, "0");
  return overdue ? `overdue by ${minutes}:${rest}` : `${minutes}:${rest}`;
}
