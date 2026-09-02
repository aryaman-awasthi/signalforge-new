import type { ComponentHealth } from "@/features/platform/api";

const STYLES: Record<ComponentHealth["status"], string> = {
  up: "border-ok/40 bg-ok/10 text-ok",
  degraded: "border-warn/40 bg-warn/10 text-warn",
  unknown: "border-warn/40 bg-warn/10 text-warn",
  down: "border-danger/40 bg-danger/10 text-danger",
};

/** The fixed status vocabulary, in one place so every pane says it the same way. */
export function StatusPill({ status }: { status: ComponentHealth["status"] }) {
  return (
    <span
      className={`border px-2 py-0.5 text-[11px] uppercase tracking-wide ${STYLES[status]}`}
    >
      {status}
    </span>
  );
}
