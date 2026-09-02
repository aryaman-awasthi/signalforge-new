import type { Metadata } from "next";
import localFont from "next/font/local";

import { AppShell } from "@/features/shell/components/AppShell";

import "./globals.css";

/**
 * Archivo, self-hosted.
 *
 * The design system loads it from Google Fonts; this build must not, because the
 * machine this is demonstrated on may have no outbound network at build time and
 * a failed font fetch fails `next build` outright. One variable file carries the
 * whole 100-900 axis, which is what Google serves for all three weights anyway.
 */
const archivo = localFont({
  src: "../public/fonts/archivo-variable-latin.woff2",
  weight: "100 900",
  style: "normal",
  display: "swap",
  variable: "--font-archivo",
  fallback: ["system-ui", "-apple-system", "Segoe UI", "sans-serif"],
});

export const metadata: Metadata = {
  title: "SignalForge",
  description: "Enterprise incident command centre",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={archivo.variable}>
      <body>
        <AppShell>{children}</AppShell>
      </body>
    </html>
  );
}
