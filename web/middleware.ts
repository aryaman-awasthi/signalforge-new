/**
 * Route guards per role group (REQ-012, UX layer only - the API enforces RBAC
 * for real and audits denials). Groups:
 *   org group (org_owner): /setup organisation administration, /settings
 *     platform operations, the /apps overview and registration, the onboarding
 *     wizard under /apps/[appId]/onboard, and /integration-health - all need
 *     manage_org or manage_apps, which only the org owner holds in the frozen
 *     matrix
 *   any authenticated session: everything except the public pages - which
 *     includes /apps/[appId] (the app detail screen the proposal sheet lives
 *     on), /incidents, /incidents/[id] (the war room) and /my/pages (the mobile
 *     pager), deliberately and permanently
 *
 * The war room, the pager and the app detail screen must never join a role group. The frozen roles matrix gives the SME no permissions at all and the
 * incident commander no `manage_apps`; those are the people most likely to be
 * paged and the only people allowed to declare. A group guard on those paths
 * would redirect exactly the people an incident exists to reach away from the
 * screens that page them and that they declare from - which is precisely what
 * the S4 browser walkthrough found (DR-089). The API enforces the same split -
 * those routes carry no permission and are tenant- or self-scoped instead - so
 * adding a guard here would not tighten anything, it would only lock somebody
 * out of a page they are allowed to open.
 *
 * `/apps` is matched exactly rather than by prefix for that reason: the
 * overview and the register form are administration, one screen deeper is
 * incident response.
 */

import { NextRequest, NextResponse } from "next/server";

const PUBLIC_PATHS = ["/", "/health", "/login", "/signup"];
const ORG_GROUP = ["org_owner"];
/** Prefix-matched: everything under these paths is organisation administration. */
const ORG_GROUP_PATHS = ["/setup", "/settings", "/integration-health"];

/** Exactly these, and nothing under them. */
const ORG_GROUP_EXACT = ["/apps"];

/** Administration that lives *under* an incident-response path. */
const ORG_GROUP_SUFFIXES = ["/onboard"];

function orgGroupPath(pathname: string): string | undefined {
  if (ORG_GROUP_EXACT.includes(pathname)) return pathname;
  const prefixed = ORG_GROUP_PATHS.find((path) => pathname.startsWith(path));
  if (prefixed) return prefixed;
  if (
    pathname.startsWith("/apps/") &&
    ORG_GROUP_SUFFIXES.some((suffix) => pathname.includes(suffix))
  ) {
    return "/apps";
  }
  return undefined;
}

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

  const guarded = orgGroupPath(pathname);
  if (guarded) {
    const roles = (request.cookies.get("sf_roles")?.value ?? "").split(",");
    if (!ORG_GROUP.some((role) => roles.includes(role))) {
      return NextResponse.redirect(
        new URL(`/?denied=${guarded.slice(1)}`, request.url),
      );
    }
  }

  return NextResponse.next();
}

export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon.ico).*)"],
};
