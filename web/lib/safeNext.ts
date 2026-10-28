/**
 * Where a sign-in or a refresh may land afterwards (REQ-010).
 *
 * Only a same-origin path is honoured. A `next` that names another host is how
 * a sign-in form becomes an open redirector, and browsers read more than one
 * spelling as "another host": `//evil.example` is scheme-relative, and
 * `/\\evil.example` is normalised to the same thing by every mainstream browser.
 * So the rule is a single leading slash followed by neither a slash nor a
 * backslash - written once and used by the server-side refresh handler and the
 * client-side sign-in form alike, so the two can never disagree.
 */
export function safeNext(candidate: string | null | undefined, fallback = "/apps"): string {
  if (!candidate) return fallback;
  if (!/^\/(?![\/\\])/.test(candidate)) return fallback;
  return candidate;
}
