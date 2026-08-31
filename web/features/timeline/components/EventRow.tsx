"use client";

import type { EventEnvelope } from "@/features/timeline/api";
import { ROW_HEIGHT } from "@/features/timeline/hooks/useVirtualRows";

function shortTime(iso: string): string {
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? iso : date.toISOString().slice(11, 23);
}

export function EventRow({
  event,
  anchored,
  onAnchor,
}: {
  event: EventEnvelope;
  anchored: boolean;
  onAnchor: (seq: number) => void;
}) {
  const seq = event.seq ?? 0;
  return (
    <div
      style={{ height: ROW_HEIGHT }}
      className={`flex items-center gap-3 border-b border-line px-3 text-sm ${
        anchored ? "bg-accent/10" : ""
      }`}
      data-testid="event-row"
    >
      <button
        onClick={() => onAnchor(seq)}
        title="Anchor the timeline here"
        className="w-16 shrink-0 text-left font-mono text-xs text-ink-muted hover:text-accent"
      >
        #{seq}
      </button>
      <span className="w-28 shrink-0 font-mono text-xs text-ink-muted">
        {shortTime(event.occurred_at)}
      </span>
      <span className="min-w-0 flex-1 truncate font-medium">{event.topic}</span>
      {event.plane === "incident" && (
        <span className="shrink-0 rounded border border-line px-1.5 py-0.5 text-[10px] uppercase text-ink-muted">
          kept
        </span>
      )}
      {event.source.simulated && (
        <span className="shrink-0 rounded border border-warn/40 bg-warn/10 px-1.5 py-0.5 text-[10px] uppercase text-warn">
          simulated
        </span>
      )}
      {event.causation_id && (
        <span
          title={`caused by ${event.causation_id}`}
          className="shrink-0 text-xs text-ink-muted"
        >
          ↳
        </span>
      )}
      <span className="w-32 shrink-0 truncate text-right text-xs text-ink-muted">
        {event.source.system}
      </span>
    </div>
  );
}
