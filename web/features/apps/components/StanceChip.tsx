"use client";

import { useEffect, useState } from "react";

import { Lifecycle, getLifecycle } from "@/features/incidents/api";

/**
 * The application's stance, on the estate screen.
 *
 * `/apps` used to show readiness and nothing else, so an application that the
 * detector had just proposed a P0 for looked exactly like one that was fine.
 * This chip reads the same lifecycle route the application page reads, on the
 * same five-second cadence, so the estate and the application can never
 * disagree about what the platform believes.
 *
 * Steady is deliberately quiet - a green chip on every healthy card is the
 * state of the world, not news. Watch is muted. Proposed and an open incident
 * are the two that should be legible from across a room.
 */

const POLL_MS = 5000;

const TONE: Record<string, string> = {
  steady: "border-ok/40 bg-ok/10 text-ok",
  watch: "border-line bg-surface text-ink-muted",
  proposed: "border-accent bg-accent/10 text-accent-strong",
  active: "border-danger bg-danger/10 text-danger",
  recovering: "border-warn bg-warn/10 text-warn-ink",
  closed: "border-line bg-surface text-ink-muted",
};

export function StanceChip({ appId }: { appId: string }) {
  const [lifecycle, setLifecycle] = useState<Lifecycle | null>(null);

  useEffect(() => {
    let live = true;
    const read = async () => {
      try {
        const next = await getLifecycle(appId);
        if (live) setLifecycle(next);
      } catch {
        // A lifecycle that cannot be read is not a stance; the chip says
        // nothing rather than something stale.
        if (live) setLifecycle(null);
      }
    };
    void read();
    const handle = setInterval(() => void read(), POLL_MS);
    return () => {
      live = false;
      clearInterval(handle);
    };
  }, [appId]);

  if (!lifecycle) return null;

  const severity =
    lifecycle.source === "incident"
      ? lifecycle.incident?.severity
      : lifecycle.state === "proposed"
        ? lifecycle.severity ?? null
        : null;

  return (
    <span
      className={`shrink-0 border px-2 py-1 text-tiny font-semibold uppercase tracking-label ${
        TONE[lifecycle.state] ?? TONE.steady
      }`}
      title={
        lifecycle.source === "incident"
          ? "An incident somebody declared"
          : "The detection stance, recomputed every 5s"
      }
    >
      {lifecycle.state}
      {severity ? ` · ${severity}` : ""}
    </span>
  );
}
