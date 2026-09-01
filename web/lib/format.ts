/**
 * Display formatting shared by the integration screens.
 *
 * Freshness is deliberately rendered from the *server's* number rather than
 * recomputed from a timestamp in the browser: the platform can run on a drill
 * clock, and a cockpit that quietly substituted wall time would report a
 * 100000x drill as hours stale.
 */

export function relativeTime(iso: string | null | undefined): string {
  if (!iso) return "never";
  const seconds = Math.round((Date.now() - new Date(iso).getTime()) / 1000);
  if (!Number.isFinite(seconds)) return iso;
  return `${duration(Math.max(seconds, 0))} ago`;
}

export function duration(seconds: number): string {
  if (seconds < 60) return `${Math.round(seconds)}s`;
  if (seconds < 3600) return `${Math.round(seconds / 60)}m`;
  if (seconds < 86400) return `${Math.round(seconds / 3600)}h`;
  return `${Math.round(seconds / 86400)}d`;
}

/** Seconds-since as the health screens say it: "never" is a fact, not a gap. */
export function freshness(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined) return "never";
  return `${duration(seconds)} ago`;
}
