"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { fetchMe, logout, type Me } from "@/features/auth/api";
import { fetchOrg, type Org } from "@/features/shell/api";
import { NotificationToasts } from "@/features/shell/components/NotificationToasts";
import { Pill } from "@/lib/ui/primitives";

/**
 * The application shell (S8-T04): the one piece of chrome every screen sits in.
 *
 * Matched to the prototype's `.nav`: a sticky bar on the page ground with a 2px
 * rule under it, the mark as a small accent square beside the wordmark, the
 * organisation as a monospace tag, and the signed-in person with their role.
 *
 * Two things it deliberately does:
 *
 * 1. **It names the organisation.** A command centre that does not say whose
 *    estate you are looking at is one wrong browser tab away from an expensive
 *    mistake.
 * 2. **It names your role.** Every refusal in this product is a role refusing,
 *    and a reader who cannot see their own role cannot understand the refusal.
 *
 * The frame width is the other half of the shell. The prototype has two: a
 * document, centred and padded, for everything that is read; and the cockpit,
 * full width with a tighter gutter, for the one screen that is watched. The
 * route decides which, because that is what the route already means.
 */

const NAV = [
  { href: "/health", label: "Health" },
  { href: "/apps", label: "Applications" },
  { href: "/integration-health", label: "Integrations" },
  { href: "/setup", label: "Setup" },
  { href: "/settings", label: "Settings" },
];

/** Routes that are watched rather than read, and get the cockpit frame. */
function isCockpit(pathname: string): boolean {
  return pathname.startsWith("/incidents/");
}

function primaryRole(me: Me | null): string | null {
  if (!me || me.bindings.length === 0) return null;
  // Org-wide bindings first: a person who is both an SRE on one application and
  // the Incident Commander for the organisation is introduced as the commander.
  const orgWide = me.bindings.find((binding) => binding.app_id === null);
  return (orgWide ?? me.bindings[0]).role;
}

export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname() || "/";
  const router = useRouter();
  const [me, setMe] = useState<Me | null>(null);
  const [org, setOrg] = useState<Org | null>(null);

  useEffect(() => {
    let cancelled = false;
    void fetchMe()
      .then((who) => {
        if (!cancelled) setMe(who);
        return fetchOrg();
      })
      .then((current) => {
        if (!cancelled && current) setOrg(current);
      })
      // Signed out is not an error here. The shell renders either way; the page
      // underneath is what decides whether it needs a session.
      .catch(() => {
        if (!cancelled) {
          setMe(null);
          setOrg(null);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [pathname]);

  const role = primaryRole(me);
  const cockpit = isCockpit(pathname);

  async function signOut() {
    try {
      await logout();
    } finally {
      setMe(null);
      setOrg(null);
      router.push("/login");
      router.refresh();
    }
  }

  return (
    <>
      <header className="sticky top-0 z-50 border-b-2 border-line bg-surface">
        <div className="flex flex-wrap items-center gap-4 px-4 py-3">
          <Link
            href="/"
            className="flex items-center gap-2.5 font-display text-h5 font-extrabold tracking-display"
          >
            <span className="h-3 w-3 bg-accent" aria-hidden />
            SignalForge
          </Link>

          {org && (
            <Pill
              size="micro"
              className="bg-neutral-100 font-mono font-normal normal-case text-neutral-800"
            >
              {org.name}
            </Pill>
          )}

          <nav className="flex flex-wrap items-center gap-4 text-dense">
            {NAV.map((item) => {
              const current = pathname === item.href || pathname.startsWith(`${item.href}/`);
              return (
                <Link
                  key={item.href}
                  href={item.href}
                  aria-current={current ? "page" : undefined}
                  className={current ? "text-accent" : "hover:text-accent"}
                >
                  {item.label}
                </Link>
              );
            })}
          </nav>

          <div className="ml-auto flex items-center gap-3 text-dense">
            {me ? (
              <>
                <span className="text-ink-muted">{me.display_name || me.email}</span>
                {role && (
                  <Pill
                    size="tiny"
                    className="border border-neutral-600 uppercase text-neutral-700"
                    title="Your role decides what this product will let you do"
                  >
                    {role.replace(/_/g, " ")}
                  </Pill>
                )}
                <button
                  type="button"
                  onClick={() => void signOut()}
                  className="text-accent hover:underline"
                >
                  Sign out
                </button>
              </>
            ) : (
              <Link href="/login" className="text-accent hover:underline">
                Sign in
              </Link>
            )}
          </div>
        </div>
      </header>

      <main
        className={
          cockpit
            ? "px-6 py-5"
            : "mx-auto w-full max-w-[1240px] px-12 py-8"
        }
      >
        {children}
      </main>
      {/* The socket is held open only while somebody is signed in: a signed-out
          shell has no ticket to fetch and nothing to be told. */}
      <NotificationToasts active={me !== null} />
    </>
  );
}
