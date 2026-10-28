/**
 * Route guards per role group (REQ-012, UX layer only - the API enforces RBAC
 * for real and audits denials). Groups:
 *   org group (org_owner): /setup organisation administration, /settings
 *     platform operations, the onboarding wizard under /apps/[appId]/onboard,
 *     and /integration-health - all need manage_org or manage_apps, which only
 *     the org owner holds in the frozen matrix
 *   any authenticated session: everything except the public pages - which
 *     includes the /apps overview, /apps/[appId] (the app detail screen the
 *     proposal sheet lives on), /incidents, /incidents/[id] (the war room) and
 *     /my/pages (the mobile pager), deliberately and permanently
 *
 * The war room, the pager, the application list and the app detail screen must
 * never join a role group. The frozen roles matrix gives the SME no permissions
 * at all and the incident commander no `manage_apps`; those are the people most
 * likely to be paged and the only people allowed to declare. A group guard on
 * those paths would redirect exactly the people an incident exists to reach
 * away from the screens that page them and that they declare from - which is
 * precisely what the S4 browser walkthrough found (DR-089), and what the S10
 * invite walkthrough found again on `/apps`: an Incident Commander who signed
 * in from an invite was bounced off the only list of applications there is.
 * The API enforces the same split - those routes carry no permission and are
 * tenant- or binding-scoped instead - so adding a guard here would not tighten
 * anything, it would only lock somebody out of a page they are allowed to open.
 *
 * A session whose access cookie has lapsed but whose refresh cookie is present
 * is sent through `/api/auth/refresh` and back, rather than to the sign-in
 * form. The access token lives fifteen minutes (REQ-010); a person reading a
 * runbook for twenty is not signed out.
 */

import { NextRequest, NextResponse } from "next/server";

const PUBLIC_PATHS = ["/", "/health", "/login", "/signup"];
const ORG_GROUP = ["org_owner"];
/** Prefix-matched: everything under these paths is organisation administration. */
const ORG_GROUP_PATHS = ["/setup", "/settings", "/integration-health"];

/** Administration that lives *under* an incident-response path: the wizard is
 * `/apps/[appId]/onboard`. Matched as a whole path segment, never as a
 * substring: an application whose id happens to start with "onboard" is still
 * an application page, and locking its detail screen behind the org group was
 * exactly the redirect this file's comment warns about. */
const ORG_GROUP_SEGMENTS = ["onboard"];

function orgGroupPath(pathname: string): string | undefined {
  const prefixed = ORG_GROUP_PATHS.find((path) => pathname.startsWith(path));
  if (prefixed) return prefixed;
  const segments = pathname.split("/").filter(Boolean);
  if (
    segments[0] === "apps" &&
    segments.length >= 3 &&
    ORG_GROUP_SEGMENTS.includes(segments[2])
  ) {
    return "/onboard";
  }
  return undefined;
}

function isPublic(pathname: string): boolean {
  return (
    PUBLIC_PATHS.includes(pathname) ||
    pathname.startsWith("/invite/") ||
    // Every route handler under /api authenticates for itself: the proxy
    // forwards the cookie's bearer token and performs its own one-shot
    // refresh on a 401. Redirecting a lapsed POST or PATCH from here turned it
    // into a GET of /api/auth/refresh - a 405 from the browser's point of view
    // - so a form submitted in the sixteenth minute failed instead of
    // refreshing. Pages are guarded below; API calls are not pages.
    pathname.startsWith("/api/") ||
    pathname.startsWith("/_next/") ||
    pathname === "/favicon.ico"
  );
}

export function middleware(request: NextRequest) {
  const { pathname, search } = request.nextUrl;
  if (isPublic(pathname)) {
    return NextResponse.next();
  }

  const authenticated = Boolean(request.cookies.get("sf_access")?.value);
  if (!authenticated) {
    if (request.cookies.get("sf_refresh")?.value) {
      const refresh = new URL("/api/auth/refresh", request.url);
      refresh.searchParams.set("next", `${pathname}${search}`);
      return NextResponse.redirect(refresh);
    }
    const login = new URL("/login", request.url);
    login.searchParams.set("next", pathname);
    return NextResponse.redirect(login);
  }

  const guarded = orgGroupPath(pathname);
  if (guarded) {
    const roles = (request.cookies.get("sf_roles")?.value ?? "").split(",");
    if (!ORG_GROUP.some((role) => roles.includes(role))) {
      // Sent to the page everybody can open, with a note saying what was
      // refused - not to the landing page, which offers a signed-in person a
      // sign-up form and reads as "you are locked out".
      return NextResponse.redirect(
        new URL(`/apps?denied=${guarded.slice(1)}`, request.url),
      );
    }
  }

  return NextResponse.next();
}

export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon.ico).*)"],
};
