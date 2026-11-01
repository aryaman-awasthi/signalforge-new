/**
 * Server-only session plumbing for the auth cookie proxy (REQ-010).
 * Tokens live in httpOnly cookies; browser JavaScript never sees them.
 * sf_roles is a display/guard hint for middleware - the API is the enforcer.
 *
 * The refresh cookie is sent on every same-origin request (`path: "/"`), not
 * only to the route handlers. It is still httpOnly, so nothing in the page can
 * read it; what the wider path buys is that the middleware can *see* that a
 * session exists after the fifteen-minute access token has lapsed, and send
 * the browser through `/api/auth/refresh` instead of back to the sign-in form.
 * Before this, every page opened after a quarter of an hour of reading was a
 * fresh sign-in, which is what "I keep getting logged out" meant.
 */

import { NextResponse } from "next/server";

export const API_BASE = process.env.SIGNALFORGE_API_URL ?? "http://127.0.0.1:8000";

export const COOKIE_ACCESS = "sf_access";
export const COOKIE_REFRESH = "sf_refresh";
export const COOKIE_ROLES = "sf_roles";

const secure = process.env.NODE_ENV === "production";

export type Tokens = { access_token: string; refresh_token: string; expires_in: number };
type Binding = { role: string; app_id: string | null };

/** The two credential cookies, from a token pair. Used on login, on accept-invite,
 * on the proxy's one-shot refresh and on the middleware-driven refresh, so the
 * four can never disagree about a flag. */
export function setTokenCookies(response: NextResponse, tokens: Tokens): void {
  response.cookies.set(COOKIE_ACCESS, tokens.access_token, {
    httpOnly: true,
    sameSite: "lax",
    secure,
    path: "/",
    maxAge: tokens.expires_in,
  });
  response.cookies.set(COOKIE_REFRESH, tokens.refresh_token, {
    httpOnly: true,
    sameSite: "lax",
    secure,
    path: "/",
    maxAge: 14 * 86400,
  });
}

export function setSessionCookies(
  response: NextResponse,
  tokens: Tokens,
  bindings: Binding[],
): void {
  setTokenCookies(response, tokens);
  const roles = Array.from(
    new Set(bindings.filter((b) => b.app_id === null).map((b) => b.role)),
  ).join(",");
  response.cookies.set(COOKIE_ROLES, roles, {
    httpOnly: true,
    sameSite: "lax",
    secure,
    path: "/",
    maxAge: 14 * 86400,
  });
}

export function clearSessionCookies(response: NextResponse): void {
  for (const [name, path] of [
    [COOKIE_ACCESS, "/"],
    [COOKIE_REFRESH, "/"],
    // The refresh cookie used to be scoped to /api; a browser that still holds
    // one from before is cleared on that path too, or it lingers for a fortnight.
    [COOKIE_REFRESH, "/api"],
    [COOKIE_ROLES, "/"],
  ] as const) {
    response.cookies.set(name, "", { httpOnly: true, path, maxAge: 0 });
  }
}

/** Forwards an auth bootstrap call (login/signup/accept-invite) to the API and,
 * on success, converts the token payload into cookies. Tokens are stripped
 * from the body the browser receives. */
export async function bootstrapSession(path: string, body: unknown): Promise<NextResponse> {
  let upstream: Response;
  try {
    upstream = await fetch(`${API_BASE}${path}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      cache: "no-store",
    });
  } catch {
    return NextResponse.json({ detail: "API unreachable" }, { status: 502 });
  }
  const payload = await upstream.json().catch(() => ({}));
  if (!upstream.ok) {
    const response = NextResponse.json(payload, { status: upstream.status });
    const retryAfter = upstream.headers.get("Retry-After");
    if (retryAfter) response.headers.set("Retry-After", retryAfter);
    return response;
  }
  const { tokens, user, org } = payload as {
    tokens: Tokens;
    user: { bindings: Binding[] };
    org: unknown;
  };
  const response = NextResponse.json({ user, org }, { status: upstream.status });
  setSessionCookies(response, tokens, user.bindings);
  return response;
}

/** Where a sign-in or a refresh should land. One rule, shared with the
 * sign-in form: see lib/safeNext.ts. */
export { safeNext } from "@/lib/safeNext";
