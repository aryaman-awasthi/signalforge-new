/**
 * Auth cookie proxy (REQ-010): forwards /api/proxy/* to the API with the
 * bearer token from the httpOnly cookie. On a 401 it attempts one refresh
 * with the refresh cookie, rotates both cookies and retries the request.
 */

import { NextRequest, NextResponse } from "next/server";

import {
  API_BASE,
  COOKIE_ACCESS,
  COOKIE_REFRESH,
  clearSessionCookies,
  setTokenCookies,
  type Tokens,
} from "@/app/api/_lib/session";

async function forward(
  request: NextRequest,
  path: string,
  access: string | undefined,
  body: string | undefined,
): Promise<Response> {
  const url = new URL(`${API_BASE}/${path}`);
  // `append`, not `set`: the API declares repeated query parameters (the
  // timeline's `topics`), and `set` would keep only the last value of each.
  request.nextUrl.searchParams.forEach((value, key) => url.searchParams.append(key, value));
  return fetch(url, {
    method: request.method,
    headers: {
      "Content-Type": "application/json",
      ...(access ? { Authorization: `Bearer ${access}` } : {}),
    },
    body,
    cache: "no-store",
  });
}

async function handle(request: NextRequest, params: { path: string[] }) {
  const path = params.path.join("/");
  const body = ["GET", "HEAD"].includes(request.method)
    ? undefined
    : await request.text();

  let access = request.cookies.get(COOKIE_ACCESS)?.value;
  let rotated: Tokens | null = null;

  let upstream: Response;
  try {
    upstream = await forward(request, path, access, body);

    if (upstream.status === 401) {
      const refresh = request.cookies.get(COOKIE_REFRESH)?.value;
      if (!refresh) return unauthenticated();
      const refreshed = await fetch(`${API_BASE}/auth/refresh`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ refresh_token: refresh }),
        cache: "no-store",
      });
      if (!refreshed.ok) return unauthenticated();
      rotated = (await refreshed.json()) as Tokens;
      access = rotated.access_token;
      upstream = await forward(request, path, access, body);
    }
  } catch {
    return NextResponse.json({ detail: "API unreachable" }, { status: 502 });
  }

  const text = await upstream.text();
  const response = new NextResponse(text.length ? text : null, {
    status: upstream.status,
    headers: {
      "Content-Type": upstream.headers.get("Content-Type") ?? "application/json",
      ...(upstream.headers.get("Retry-After")
        ? { "Retry-After": upstream.headers.get("Retry-After")! }
        : {}),
    },
  });
  if (rotated) setTokenCookies(response, rotated);
  return response;
}

function unauthenticated(): NextResponse {
  const response = NextResponse.json({ detail: "authentication failed" }, { status: 401 });
  clearSessionCookies(response);
  return response;
}

export async function GET(request: NextRequest, ctx: { params: { path: string[] } }) {
  return handle(request, ctx.params);
}
export async function POST(request: NextRequest, ctx: { params: { path: string[] } }) {
  return handle(request, ctx.params);
}
export async function PUT(request: NextRequest, ctx: { params: { path: string[] } }) {
  return handle(request, ctx.params);
}
export async function PATCH(request: NextRequest, ctx: { params: { path: string[] } }) {
  return handle(request, ctx.params);
}
export async function DELETE(request: NextRequest, ctx: { params: { path: string[] } }) {
  return handle(request, ctx.params);
}
