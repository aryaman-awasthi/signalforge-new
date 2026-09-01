/**
 * Route guards per role group (REQ-012, UX layer only - the API enforces RBAC
 * for real and audits denials). Groups:
 *   org group (org_owner): /setup organisation administration, /settings
 *     platform operations, /apps onboarding and /integration-health - all need
 *     manage_org or manage_apps, which only the org owner holds in the frozen
 *     matrix
 *   any authenticated session: everything except the public pages - which
 *     includes /incidents, /incidents/[id] (the war room) and /my/pages
 *     (the mobile pager), deliberately and permanently
 *
 * The war room and the pager must never join a role group. The frozen roles
 * matrix gives the SME no permissions at all, and the SME is the person most
 * likely to be paged; a group guard on those paths would redirect exactly the
 * people an incident exists to reach away from the screen that pages them. The
 * API enforces the same split - those routes carry no permission and are
 * self-scoped instead - so adding a guard here would not tighten anything, it
 * would only lock somebody out of a page they are allowed to open.
 */

import { NextRequest, NextResponse } from "next/server";

const PUBLIC_PATHS = ["/", "/health", "/login", "/signup"];
const ORG_GROUP = ["org_owner"];
const ORG_GROUP_PATHS = ["/setup", "/settings", "/apps", "/integration-health"];

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

  const orgGroupPath = ORG_GROUP_PATHS.find((path) => pathname.startsWith(path));
  if (orgGroupPath) {
    const roles = (request.cookies.get("sf_roles")?.value ?? "").split(",");
    if (!ORG_GROUP.some((role) => roles.includes(role))) {
      return NextResponse.redirect(
        new URL(`/?denied=${orgGroupPath.slice(1)}`, request.url),
      );
    }
  }

  return NextResponse.next();
}

export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon.ico).*)"],
};
