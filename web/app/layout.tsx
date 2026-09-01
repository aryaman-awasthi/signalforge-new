import type { Metadata } from "next";
import Link from "next/link";

import "./globals.css";

export const metadata: Metadata = {
  title: "SignalForge",
  description: "Enterprise incident command centre",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <header className="border-b border-line bg-surface-raised">
          <div className="mx-auto flex h-14 max-w-5xl items-center gap-8 px-6">
            <Link href="/" className="font-semibold tracking-tight">
              Signal<span className="text-accent">Forge</span>
            </Link>
            <nav className="flex gap-4 text-sm text-ink-muted">
              <Link href="/health" className="hover:text-ink">
                Health
              </Link>
              <Link href="/apps" className="hover:text-ink">
                Applications
              </Link>
              <Link href="/integration-health" className="hover:text-ink">
                Integrations
              </Link>
              <Link href="/setup" className="hover:text-ink">
                Setup
              </Link>
              <Link href="/settings" className="hover:text-ink">
                Settings
              </Link>
              <Link href="/login" className="hover:text-ink">
                Sign in
              </Link>
            </nav>
          </div>
        </header>
        <main className="mx-auto max-w-6xl px-6 py-10">{children}</main>
      </body>
    </html>
  );
}
