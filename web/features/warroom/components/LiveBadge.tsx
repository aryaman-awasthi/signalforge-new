"use client";

/**
 * Which refresh mode this screen is actually in (REQ-033).
 *
 * Small, and load-bearing. A war room five seconds behind reality is fine; a
 * responder who *believes* a screen is live when it has fallen back to polling
 * is how somebody concludes nobody is answering their page. So the screen says
 * which it is rather than looking identical either way - and "Polling" is
 * written as a working state, not as an error, because it is one.
 */
export function LiveBadge({
  connected,
  pollSeconds,
}: {
  connected: boolean;
  pollSeconds: number;
}) {
  return (
    <span
      data-testid="live-badge"
      data-live={connected ? "true" : "false"}
      title={
        connected
          ? "Updates arrive as they happen; the screen also re-reads periodically."
          : `The live connection is not available, so this screen re-reads every ${pollSeconds} seconds. Nothing shown is wrong - it can be up to ${pollSeconds} seconds old.`
      }
      className={
        connected
          ? "inline-flex items-center gap-1.5 rounded-full border border-ok/40 bg-ok/10 px-2.5 py-1 text-xs text-ok"
          : "inline-flex items-center gap-1.5 rounded-full border border-line px-2.5 py-1 text-xs text-ink-muted"
      }
    >
      <span
        aria-hidden
        className={
          connected
            ? "h-1.5 w-1.5 rounded-full bg-ok"
            : "h-1.5 w-1.5 rounded-full bg-ink-muted"
        }
      />
      {connected ? "Live" : `Polling every ${pollSeconds}s`}
    </span>
  );
}
