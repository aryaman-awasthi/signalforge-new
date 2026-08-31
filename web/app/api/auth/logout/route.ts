import { NextRequest, NextResponse } from "next/server";

import { API_BASE, COOKIE_ACCESS, clearSessionCookies } from "@/app/api/_lib/session";

export async function POST(request: NextRequest) {
  const access = request.cookies.get(COOKIE_ACCESS)?.value;
  if (access) {
    // Best effort: revoke the session server-side; cookies are cleared regardless.
    await fetch(`${API_BASE}/auth/logout`, {
      method: "POST",
      headers: { Authorization: `Bearer ${access}` },
      cache: "no-store",
    }).catch(() => undefined);
  }
  const response = NextResponse.json({ ok: true });
  clearSessionCookies(response);
  return response;
}
