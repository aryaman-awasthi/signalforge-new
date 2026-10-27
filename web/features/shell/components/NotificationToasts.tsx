"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { wsStore, type WsFrame } from "@/lib/ws/store";

/**
 * Live notifications, anywhere in the product.
 *
 * Until now a `notification` frame was rendered by exactly one screen,
 * `/my/pages`, so an Incident Commander reading the estate screen learned that
 * the platform had proposed a P0 only when they happened to open the right
 * application. The shell now keeps one subscription open while a person is
 * signed in and shows what arrives as a small stack in the corner: the title,
 * the body, and the way to the thing it is about.
 *
 * Two deliberate limits. Replays are shown once, quietly, because a reconnect
 * that re-raised a day of pages would train people to dismiss the stack
 * unread. And nothing here acknowledges anything: an acknowledgement is a
 * decision with a countdown behind it, and it belongs on the pager screen
 * where the countdown is.
 */

type Toast = {
  key: string;
  title: string;
  body: string;
  href: string | null;
  replay: boolean;
  simulated: boolean;
  at: number;
};

const MAX_VISIBLE = 4;
const LINGER_MS = 20_000;

function hrefOf(frame: WsFrame): string | null {
  const incident = typeof frame.incident_id === "string" ? frame.incident_id : "";
  if (incident) return `/incidents/${encodeURIComponent(incident)}`;
  const app = typeof frame.app_id === "string" ? frame.app_id : "";
  if (app) return `/apps/${encodeURIComponent(app)}`;
  return null;
}

export function NotificationToasts({ active }: { active: boolean }) {
  const [toasts, setToasts] = useState<Toast[]>([]);

  useEffect(() => {
    if (!active) return;
    return wsStore.subscribe((frame: WsFrame) => {
      if (frame.type !== "notification") return;
      const id =
        typeof frame.notification_id === "string"
          ? frame.notification_id
          : `${Date.now()}-${Math.random()}`;
      const toast: Toast = {
        key: id,
        title: typeof frame.title === "string" ? frame.title : "Notification",
        body: typeof frame.body === "string" ? frame.body : "",
        href: hrefOf(frame),
        replay: frame.replay === true,
        simulated: frame.simulated === true,
        at: Date.now(),
      };
      setToasts((current) =>
        [toast, ...current.filter((item) => item.key !== toast.key)].slice(0, MAX_VISIBLE),
      );
    });
  }, [active]);

  useEffect(() => {
    if (toasts.length === 0) return;
    const handle = setInterval(() => {
      const cutoff = Date.now() - LINGER_MS;
      setToasts((current) => current.filter((item) => item.at > cutoff));
    }, 1000);
    return () => clearInterval(handle);
  }, [toasts.length]);

  if (toasts.length === 0) return null;

  return (
    <div
      aria-live="polite"
      aria-label="Notifications"
      className="pointer-events-none fixed bottom-4 right-4 z-50 flex w-[360px] max-w-[calc(100vw-2rem)] flex-col gap-2"
    >
      {toasts.map((toast) => (
        <div
          key={toast.key}
          className={`pointer-events-auto border bg-surface-raised px-3 py-2.5 shadow-md ${
            toast.replay ? "border-line" : "border-accent"
          }`}
        >
          <div className="flex items-start gap-2">
            <div className="min-w-0 flex-1">
              <p className="text-sm font-semibold leading-tight">{toast.title}</p>
              {toast.body && (
                <p className="mt-0.5 text-xs text-ink-muted">{toast.body}</p>
              )}
              <p className="mt-1 flex flex-wrap gap-2 text-[10px] uppercase tracking-label text-ink-muted">
                {toast.replay && <span>while you were away</span>}
                {toast.simulated && <span>simulated</span>}
                {toast.href && (
                  <Link href={toast.href} className="text-accent hover:underline">
                    open →
                  </Link>
                )}
              </p>
            </div>
            <button
              type="button"
              aria-label="Dismiss"
              onClick={() =>
                setToasts((current) => current.filter((item) => item.key !== toast.key))
              }
              className="text-ink-muted hover:text-ink"
            >
              ×
            </button>
          </div>
        </div>
      ))}
    </div>
  );
}
