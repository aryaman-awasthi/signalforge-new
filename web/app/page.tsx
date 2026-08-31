import Link from "next/link";

export default function HomePage() {
  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-semibold tracking-tight">SignalForge</h1>
      <p className="max-w-prose text-ink-muted">
        Enterprise incident command centre. Sprint 0 scaffold - the authless shell,
        design tokens, typed API client and WebSocket store are in place.
      </p>
      <Link
        href="/health"
        className="inline-block rounded-card bg-accent px-4 py-2 text-sm font-medium text-surface-raised hover:bg-accent-strong"
      >
        Check platform health
      </Link>
    </div>
  );
}
