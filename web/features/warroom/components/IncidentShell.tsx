"use client";

import type { ReactNode } from "react";

/**
 * The one layout framework every incident screen is expressed through (S8-T05).
 *
 * S9 to S18 are not ten screens. They are one cockpit and its expanded states,
 * and this file is what makes that structural rather than a convention. The grid
 * is the prototype's, column for column:
 *
 *     minmax(300px,350px)   1fr             minmax(280px,320px)
 *     what people said      what to do      what is true
 *
 * That is the reading order of a war room. The left column is testimony - the
 * bridge, the chat, who is here. The centre is decision - the brain, the next
 * best action, what has happened so far. The right is measurement - health,
 * blast radius, prior warnings. A commander scanning left to right goes from
 * "what are people telling me" through "what should I do" to "what is actually
 * true", and the layout does not let those three get mixed up.
 *
 * `expanded` is how S10 to S18 arrive: one deep view at a time, in place of the
 * grid, with the header still above it. It is not a route and not a modal - a
 * panel becoming a document is the prototype's own behaviour, and it means the
 * incident context is never lost while somebody reads the ledger.
 */

export function IncidentShell({
  header,
  banner,
  approvals,
  left,
  centre,
  right,
  below,
  expanded,
}: {
  header: ReactNode;
  /** Gaps, errors - anything that is true of the whole room. */
  banner?: ReactNode;
  /** Anything waiting on a person. Above the grid, never a scroll away. */
  approvals?: ReactNode;
  left: ReactNode;
  centre: ReactNode;
  right: ReactNode;
  /** Panels that are wider than a column: the ledger, the verdict, comms. */
  below?: ReactNode;
  /** When set, this replaces the grid entirely - one deep view at a time. */
  expanded?: ReactNode;
}) {
  return (
    <div className="flex flex-col gap-4">
      {header}
      {banner}
      {approvals}
      {expanded ? (
        expanded
      ) : (
        <>
          <div className="grid items-start gap-4 lg:grid-cols-[minmax(300px,350px)_1fr_minmax(280px,320px)]">
            <div className="flex min-w-0 flex-col gap-3.5">{left}</div>
            <div className="flex min-w-0 flex-col gap-3.5">{centre}</div>
            <div className="flex min-w-0 flex-col gap-3.5">{right}</div>
          </div>
          {below}
        </>
      )}
    </div>
  );
}

/**
 * The affordance that opens a deep view.
 *
 * The prototype writes it as a ghost link with a trailing arrow at the foot of
 * the panel it expands, and that placement is the point: the way to the ledger
 * is through the panel that mentions the ledger, not through a menu somewhere
 * else that a commander has to remember exists.
 */
export function ExpandLink({
  onClick,
  children,
}: {
  onClick: () => void;
  children: ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="self-start text-small font-semibold text-accent hover:underline"
    >
      {children} →
    </button>
  );
}

/** The header of a deep view: what you are reading, and the way back. */
export function ExpandedHeader({
  eyebrow,
  title,
  meta,
  onClose,
}: {
  eyebrow: string;
  title: string;
  meta?: ReactNode;
  onClose: () => void;
}) {
  return (
    <div className="flex flex-col gap-2">
      <div className="flex flex-wrap items-baseline gap-4">
        <h6 className="m-0 text-ink-muted">{eyebrow}</h6>
        {meta}
        <button
          type="button"
          onClick={onClose}
          className="ml-auto text-small font-semibold text-accent hover:underline"
        >
          ← War room
        </button>
      </div>
      <h2 className="text-h2">{title}</h2>
    </div>
  );
}
