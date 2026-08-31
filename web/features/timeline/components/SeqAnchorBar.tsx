"use client";

import { useState } from "react";

/**
 * The seq anchor. During an incident the timeline keeps growing underneath the
 * reader; anchoring pins the view to one recorded position so it stays put, and
 * gives the operator an exact number to quote in the bridge - the same number
 * the replay scrubber and GET /events?until= take.
 */
export function SeqAnchorBar({
  anchorSeq,
  lastSeq,
  shown,
  total,
  onAnchor,
}: {
  anchorSeq: number | null;
  lastSeq: number;
  shown: number;
  total: number;
  onAnchor: (seq: number | null) => void;
}) {
  const [draft, setDraft] = useState("");

  return (
    <div className="flex flex-wrap items-center gap-2 text-xs text-ink-muted">
      <span>
        {shown} of {total} loaded · head #{lastSeq}
      </span>
      <form
        className="flex items-center gap-1"
        onSubmit={(submit) => {
          submit.preventDefault();
          const seq = Number.parseInt(draft, 10);
          if (Number.isFinite(seq) && seq > 0) onAnchor(seq);
        }}
      >
        <label className="sr-only" htmlFor="seq-anchor">
          Anchor at seq
        </label>
        <input
          id="seq-anchor"
          value={draft}
          onChange={(change) => setDraft(change.target.value)}
          inputMode="numeric"
          placeholder="seq"
          className="w-20 rounded border border-line bg-surface px-2 py-1 font-mono"
        />
        <button
          type="submit"
          className="rounded border border-line px-2 py-1 hover:border-accent hover:text-accent-strong"
        >
          Anchor
        </button>
      </form>
      {anchorSeq !== null && (
        <button
          onClick={() => onAnchor(null)}
          className="rounded border border-accent/50 bg-accent/10 px-2 py-1 text-accent-strong"
        >
          Anchored at #{anchorSeq} · clear
        </button>
      )}
    </div>
  );
}
