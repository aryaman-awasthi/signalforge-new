"use client";

import { ReactNode } from "react";

/**
 * One panel of the S9 war-room shell.
 *
 * The `title` is passed through verbatim and never abbreviated or reworded by a
 * caller, because the ten titles are a fixed vocabulary: an incident commander
 * who learns where "Next Best Action" is should find it in the same place with
 * the same name in every room, every drill and every screenshot in the runbook.
 *
 * `pending` is the honest state for a panel whose feature has not been built
 * yet. It renders the panel with its real title and, inside it, one sentence
 * saying what will fill it and when - rather than an empty box, which reads as
 * "broken", or a fake chart, which reads as "working".
 *
 * S8-T05 gave it the prototype's chrome: square, no outer border, the ground one
 * step darker than the page, an 11px/0.1em/800 uppercase title over a 2px rule,
 * and an `accent` variant that puts a 4px role rule along the top. That rule
 * means "this panel is about that state" and is never decoration - it is how a
 * commander finds the one panel that needs them from across a room.
 */

export type PanelAccent = "accent" | "ok" | "warn" | "info" | "conflict";

const ACCENT: Record<PanelAccent, string> = {
  accent: "border-t-4 border-t-accent",
  ok: "border-t-4 border-t-ok",
  warn: "border-t-4 border-t-warn",
  info: "border-t-4 border-t-info",
  conflict: "border-t-4 border-t-conflict",
};

export function Panel({
  title,
  subtitle,
  actions,
  pending,
  accent,
  elevated = false,
  className = "",
  bodyClassName = "",
  children,
}: {
  title: string;
  subtitle?: string;
  actions?: ReactNode;
  pending?: string;
  accent?: PanelAccent;
  elevated?: boolean;
  className?: string;
  bodyClassName?: string;
  children?: ReactNode;
}) {
  return (
    <section
      aria-label={title}
      className={`flex min-w-0 flex-col bg-surface-raised ${
        accent ? ACCENT[accent] : ""
      } ${elevated ? "shadow-sm" : ""} ${className}`}
    >
      <header className="flex flex-wrap items-baseline gap-2 border-b-2 border-line px-4 py-3">
        <h2 className="font-display text-micro font-extrabold uppercase tracking-panel">
          {title}
        </h2>
        {subtitle && (
          <span className="text-nano uppercase tracking-label text-neutral-600">
            {subtitle}
          </span>
        )}
        <span className="flex-1" />
        {actions}
      </header>
      <div className={`flex-1 px-4 py-3 ${bodyClassName}`}>
        {pending ? (
          <p className="text-small text-ink-muted">{pending}</p>
        ) : (
          children
        )}
      </div>
    </section>
  );
}
