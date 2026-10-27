/**
 * The middleware's way back in (REQ-010).
 *
 * A page request that arrives with no access cookie but a refresh cookie is
 * sent here by the middleware. The session is rotated against the API, the
 * cookies are re-set, and the browser is redirected to where it was going. If
 * the refresh is refused - revoked, reused, expired - the cookies are cleared
 * and the person is sent to sign in with the same destination, which is the
 * outcome the middleware produced before, only now it happens for a dead
 * session rather than for every session that had merely gone quiet.
 *
 * GET, because a redirect is the only way the middleware can hand the browser
 * off, and a GET that rotates a refresh token is safe here: the token is read
 * from an httpOnly cookie, never from the URL, and the only thing the URL
 * carries is a same-origin path.
 */

import { NextRequest, NextResponse } from "next/server";

import {
  API_BASE,
  COOKIE_REFRESH,
  clearSessionCookies,
  safeNext,
  setTokenCookies,
  type Tokens,
} from "@/app/api/_lib/session";

function toLogin(request: NextRequest, next: string): NextResponse {
  const login = new URL("/login", request.url);
  login.searchParams.set("next", next);
  const response = NextResponse.redirect(login);
  clearSessionCookies(response);
  return response;
}

export async function GET(request: NextRequest) {
  const next = safeNext(request.nextUrl.searchParams.get("next"));
  const refresh = request.cookies.get(COOKIE_REFRESH)?.value;
  if (!refresh) return toLogin(request, next);

  let upstream: Response;
  try {
    upstream = await fetch(`${API_BASE}/auth/refresh`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ refresh_token: refresh }),
      cache: "no-store",
    });
  } catch {
    return NextResponse.json({ detail: "API unreachable" }, { status: 502 });
  }
  if (!upstream.ok) return toLogin(request, next);

  const tokens = (await upstream.json()) as Tokens;
  const response = NextResponse.redirect(new URL(next, request.url));
  setTokenCookies(response, tokens);
  return response;
}
