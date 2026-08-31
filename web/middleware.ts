/**
 * Route guards per role group (REQ-012, UX layer only - the API enforces RBAC
 * for real and audits denials). Groups:
 *   org group (org_owner): /setup organisation administration
 *   any authenticated session: everything except the public pages
 */

import { NextRequest, NextResponse } from "next/server";

const PUBLIC_PATHS = ["/", "/health", "/login", "/signup"];
const ORG_GROUP = ["org_owner"];

function isPublic(pathname: string): boolean {
  return (
    PUBLIC_PATHS.includes(pathname) ||
    pathname.startsWith("/invite/") ||
    pathname.startsWith("/api/auth/") ||
    pathname.startsWith("/_next/") ||
    pathname === "/favicon.ico"
  );
}

export function middleware(request: NextRequest) {
  const { pathname } = request.nextUrl;
  if (isPublic(pathname)) {
    return NextResponse.next();
  }

  const authenticated = Boolean(request.cookies.get("sf_access")?.value);
  if (!authenticated) {
    const login = new URL("/login", request.url);
    login.searchParams.set("next", pathname);
    return NextResponse.redirect(login);
  }

  if (pathname.startsWith("/setup")) {
    const roles = (request.cookies.get("sf_roles")?.value ?? "").split(",");
    if (!ORG_GROUP.some((role) => roles.includes(role))) {
      return NextResponse.redirect(new URL("/?denied=setup", request.url));
    }
  }

  return NextResponse.next();
}

export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon.ico).*)"],
};
