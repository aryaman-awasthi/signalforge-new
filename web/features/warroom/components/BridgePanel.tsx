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

  if (!bridge) {
    // Not yet read. Distinct from "no bridge": the room renders before the
    // transcript arrives, and "nobody has a bridge" and "the bridge is still
    // loading" must not look the same.
    return <p className="text-xs text-ink-muted">Reading the bridge…</p>;
  }
  if (!bridge.has_bridge) {
    return (
      <p className="text-xs text-ink-muted">
        {bridge.detail ||
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
      {/* The prototype's transcript row: who and when on one line, what they
          said on the next. A line that is all on one row reads as a log; a
          transcript reads as people talking. */}
      <ul className="flex max-h-72 flex-col gap-2 overflow-y-auto text-small">
        {bridge.messages.length === 0 ? (
          <li className="text-ink-muted">Nothing has been said yet.</li>
        ) : (
          bridge.messages.map((message) => (
            <li key={message.message_id}>
              <div className="flex flex-wrap items-baseline gap-1.5">
                <span className="font-semibold">
                  {message.author_display || message.author}
                </span>
                <span className="font-mono text-tiny text-ink-muted">
                  {message.at.slice(11, 16)}
                </span>
                {message.simulated && (
                  <span className="border border-neutral-600 px-1 text-nano uppercase tracking-label text-neutral-700">
                    simulated
                  </span>
                )}
              </div>
              <p className="text-dense">{message.text}</p>
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
