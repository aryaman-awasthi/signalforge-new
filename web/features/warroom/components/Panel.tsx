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
 */
export function Panel({
  title,
  subtitle,
  actions,
  pending,
  children,
}: {
  title: string;
  subtitle?: string;
  actions?: ReactNode;
  pending?: string;
  children?: ReactNode;
}) {
  return (
    <section
      aria-label={title}
      className="flex flex-col rounded-card border border-line bg-surface-raised"
    >
      <header className="flex flex-wrap items-center gap-2 border-b border-line px-3 py-2">
        <h2 className="text-sm font-semibold">{title}</h2>
        {subtitle && <span className="text-xs text-ink-muted">{subtitle}</span>}
        <span className="flex-1" />
        {actions}
      </header>
      <div className="flex-1 px-3 py-2.5">
        {pending ? (
          <p className="text-xs text-ink-muted">{pending}</p>
        ) : (
          children
        )}
      </div>
    </section>
  );
}
