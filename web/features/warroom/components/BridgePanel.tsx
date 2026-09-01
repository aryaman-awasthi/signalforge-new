"use client";

import { FormEvent, useState } from "react";

import { ApiError } from "@/lib/api/client";
import { Bridge, postToBridge } from "@/features/warroom/api";

/**
 * The Live Bridge Transcript panel's contents.
 *
 * Scripted and live lines share one stream, distinguished by a badge rather than
 * split into two panes. A room that separated rehearsed conversation from real
 * conversation would rehearse nothing useful - the point of a drill is that it
 * reads like the real thing.
 *
 * With no collaboration connector bound, the panel says so in a sentence. An
 * empty transcript and an absent bridge look identical, and during an incident
 * "nobody is saying anything" and "we cannot hear them" need opposite responses.
 */
export function BridgePanel({
  incidentId,
  bridge,
  onPosted,
}: {
  incidentId: string;
  bridge: Bridge | null;
  onPosted: () => void;
}) {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (!bridge || !bridge.has_bridge) {
    return (
      <p className="text-xs text-ink-muted">
        {bridge?.detail ??
          "No collaboration connector is bound to this application, so this war room has no bridge."}{" "}
        Everything else in the room - paging, the task board, the timeline - works
        without one.
      </p>
    );
  }

  async function send(event: FormEvent) {
    event.preventDefault();
    const body = text.trim();
    if (!body) return;
    setBusy(true);
    setError(null);
    try {
      await postToBridge(incidentId, body);
      setText("");
      onPosted();
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "Could not post");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-2">
      <ul className="max-h-64 space-y-1.5 overflow-y-auto text-xs">
        {bridge.messages.length === 0 ? (
          <li className="text-ink-muted">Nothing has been said yet.</li>
        ) : (
          bridge.messages.map((message) => (
            <li key={message.message_id} className="flex flex-wrap gap-2">
              <span className="font-mono text-ink-muted">
                {message.at.slice(11, 16)}
              </span>
              <span className="font-medium">
                {message.author_display || message.author}
              </span>
              {message.simulated && (
                <span className="rounded-full border border-line px-1.5 py-0.5 text-[10px] uppercase text-ink-muted">
                  simulated
                </span>
              )}
              <span className="min-w-32 flex-1">{message.text}</span>
            </li>
          ))
        )}
      </ul>

      {error && <p className="text-xs text-danger">{error}</p>}

      <form onSubmit={send} className="flex gap-2">
        <input
          value={text}
          onChange={(event) => setText(event.target.value)}
          placeholder="Say something — a commitment becomes a task"
          className="flex-1 rounded-card border border-line bg-surface px-2 py-1 text-xs text-ink"
        />
        <button
          type="submit"
          disabled={busy || !text.trim()}
          className="rounded-card bg-accent px-2.5 py-1 text-xs font-medium text-surface-raised hover:bg-accent-strong disabled:opacity-60"
        >
          Post
        </button>
      </form>
      <p className="text-[11px] text-ink-muted">
        &ldquo;I&rsquo;ll roll back region-b in 10 minutes&rdquo; becomes a task with
        an owner and a due time. A question does not.
      </p>
    </div>
  );
}
