/**
 * The component sheet (S8-T06).
 *
 * Every pill, badge, chip, bar and tile on every screen is one of these. They
 * are styled from `playbook/design/tokens-extracted.md`, which records the exact
 * recipe each one came from in the Claude Design prototype, and they use design
 * tokens only - there is no hex here and no raw Tailwind palette class.
 *
 * The vocabulary is fixed on purpose. Four visual grammars carry meaning across
 * the whole product, and they are the reason a reader can tell what a badge
 * means before reading it:
 *
 *     solid fill   the platform asserts this
 *     border only  somebody claimed it
 *     dashed       it was inferred, not measured
 *     45° stripes  two sources disagree about it
 *
 * Nothing here holds state, fetches anything, or knows what an incident is.
 */

import type { ReactNode } from "react";

/* ── the base pill ───────────────────────────────────────────────────────── */

export type PillSize = "nano" | "tiny" | "micro";

const PILL_SIZE: Record<PillSize, string> = {
  // 9px inline, sits inside a running line
  nano: "text-nano tracking-[0.06em] px-1.5 py-px",
  // 10px standalone, the default
  tiny: "text-tiny tracking-label px-1.5 py-0.5",
  // 11px header scale
  micro: "text-micro tracking-label px-2 py-1",
};

/** The shape every badge in this product shares: square, spaced, uppercase-ish. */
export function Pill({
  children,
  size = "tiny",
  className = "",
  pulse = false,
  title,
}: {
  children: ReactNode;
  size?: PillSize;
  className?: string;
  /** A pulsing pill means somebody owes an answer. Never use it for data. */
  pulse?: boolean;
  title?: string;
}) {
  return (
    <span
      title={title}
      className={`inline-flex shrink-0 items-center whitespace-nowrap font-semibold ${
        PILL_SIZE[size]
      } ${pulse ? "animate-sf-pulse" : ""} ${className}`}
    >
      {children}
    </span>
  );
}

/* ── tone, the shared colour grammar ─────────────────────────────────────── */

export type Tone =
  | "good"
  | "caution"
  | "info"
  | "conflict"
  | "closed"
  | "accent"
  | "neutral";

/** Filled: the platform asserts this. */
const FILLED: Record<Tone, string> = {
  good: "bg-ok-bg text-ok border border-ok",
  caution: "bg-warn-bg text-warn-ink border border-warn",
  info: "bg-info-bg text-info border border-info",
  conflict: "bg-conflict-bg text-conflict-ink border border-conflict",
  closed: "bg-closed-bg text-closed-ink border border-closed",
  accent: "bg-brand-100 text-brand-800 border border-accent",
  neutral: "bg-neutral-200 text-neutral-700 border border-neutral-400",
};

/** Outlined: somebody claimed it. */
const OUTLINED: Record<Tone, string> = {
  good: "border border-ok text-ok",
  caution: "border border-warn text-warn-ink",
  info: "border border-info text-info",
  conflict: "border border-conflict text-conflict-ink",
  closed: "border border-closed text-closed-ink",
  accent: "border border-accent text-accent-strong",
  neutral: "border border-neutral-600 text-neutral-700",
};

/** Dashed: inferred, not measured. */
const DASHED: Record<Tone, string> = {
  good: "border border-dashed border-ok text-ok",
  caution: "border border-dashed border-warn text-warn-ink",
  info: "border border-dashed border-info text-info",
  conflict: "border border-dashed border-conflict text-conflict-ink",
  closed: "border border-dashed border-closed text-closed-ink",
  accent: "border border-dashed border-accent text-accent-strong",
  neutral: "border border-dashed border-neutral-600 text-neutral-700",
};

export type Fill = "filled" | "outlined" | "dashed" | "striped";

export function toneClass(tone: Tone, fill: Fill): string {
  if (fill === "striped") {
    return "bg-conflict border border-conflict text-conflict-ink";
  }
  if (fill === "dashed") return DASHED[tone];
  if (fill === "outlined") return OUTLINED[tone];
  return FILLED[tone];
}

/* ── state pills ─────────────────────────────────────────────────────────── */

/**
 * The lifecycle of an application or an incident.
 *
 * The rule behind the colours, so a state this product grows later lands in the
 * right place: a state that is *waiting on the world* is caution or conflict, a
 * state that is *progressing* is info, a state that is *settled* is good or
 * closed, and the one state that is costing money right now is the accent.
 */
const STATE_TONE: Record<string, [Tone, Fill]> = {
  steady: ["good", "filled"],
  healthy: ["good", "filled"],
  watch: ["caution", "filled"],
  degraded: ["caution", "filled"],
  proposed: ["conflict", "filled"],
  breaching: ["conflict", "outlined"],
  active: ["accent", "filled"],
  anomalous: ["accent", "filled"],
  mitigating: ["info", "filled"],
  recovering: ["info", "filled"],
  validating: ["info", "filled"],
  closed: ["closed", "filled"],
  resolved: ["closed", "filled"],
  unknown: ["neutral", "outlined"],
};

export function StatePill({
  state,
  size = "micro",
  className = "",
}: {
  state: string;
  size?: PillSize;
  className?: string;
}) {
  const key = (state || "unknown").toLowerCase();
  const [tone, fill] = STATE_TONE[key] ?? ["neutral", "outlined"];
  return (
    <Pill size={size} className={`${toneClass(tone, fill)} uppercase ${className}`}>
      {state}
    </Pill>
  );
}

/* ── severity ────────────────────────────────────────────────────────────── */

/** P0 is the only filled one. A severity that is not the worst does not shout. */
export function SeverityPill({
  severity,
  size = "micro",
}: {
  severity: string;
  size?: PillSize;
}) {
  const key = (severity || "").toUpperCase();
  const style =
    key === "P0"
      ? "bg-sev-p0 text-surface"
      : key === "P1"
        ? "border border-accent text-sev-p1"
        : key === "P2"
          ? "border border-conflict text-sev-p2"
          : "border border-neutral-600 text-sev-p3";
  return (
    <Pill size={size} className={`${style} tracking-label`}>
      {key || "--"}
    </Pill>
  );
}

/* ── the fixed badge vocabulary ──────────────────────────────────────────── */

/**
 * The words this product is allowed to put on a badge, and how each one looks.
 *
 * It is a closed set because these words are the product's claims about what it
 * knows, and a screen that can invent a new one can make a claim nobody
 * designed. `Badge` falls back to a neutral outline for anything unlisted, so an
 * unknown word looks like the weakest possible statement rather than a strong one.
 */
export const BADGES: Record<string, [Tone, Fill]> = {
  SIMULATED: ["neutral", "outlined"],
  UNCERTAIN: ["caution", "filled"],
  RECONCILED: ["good", "filled"],
  "DUPLICATE PREVENTED": ["accent", "filled"],
  CONTRADICTED: ["conflict", "striped"],
  CORRECTED: ["conflict", "filled"],
  CONFIRMED: ["accent", "outlined"],
  PREDICTED: ["neutral", "dashed"],
  DEGRADED: ["caution", "filled"],
  "DEGRADED MODE": ["caution", "filled"],
  PULL: ["neutral", "outlined"],
  PUSH: ["info", "outlined"],
  READ_ONLY: ["info", "outlined"],
  REMEDIATION: ["accent", "outlined"],
  DRILL: ["neutral", "dashed"],
  VERIFIED: ["good", "filled"],
  PROPOSED: ["conflict", "filled"],
  BLOCKED: ["accent", "filled"],
  APPROVED: ["good", "filled"],
  PAGED: ["caution", "outlined"],
  ACKED: ["info", "outlined"],
  JOINED: ["good", "filled"],
  COMPLETED: ["good", "filled"],
  TIMEOUT: ["conflict", "outlined"],
  "HIGH RISK": ["accent", "outlined"],
  "NEEDS APPROVAL": ["accent", "outlined"],
  "WAITING FOR APPROVAL": ["caution", "outlined"],
  PASS: ["good", "filled"],
  PARTIAL: ["caution", "filled"],
  FAIL: ["accent", "filled"],
};

export function Badge({
  label,
  size = "tiny",
  pulse = false,
  title,
  className = "",
}: {
  label: string;
  size?: PillSize;
  pulse?: boolean;
  title?: string;
  className?: string;
}) {
  const key = label.toUpperCase();
  const [tone, fill] = BADGES[key] ?? ["neutral", "outlined"];
  const stripe = key === "FAIL" ? "bg-sev-p0 text-surface border-0" : "";
  return (
    <Pill
      size={size}
      pulse={pulse}
      title={title}
      className={`uppercase ${toneClass(tone, fill)} ${stripe} ${className}`}
    >
      {label}
    </Pill>
  );
}

/* ── evidence chips ──────────────────────────────────────────────────────── */

/**
 * How a line of the record came to be believed.
 *
 * Solid = it happened. Outline = it was said. Dashed = it was supposed. This is
 * the distinction the whole incident brain is built on, so it gets its own
 * component rather than being a colour somebody picks per screen.
 */
const EVIDENCE: Record<string, string> = {
  observed: "bg-evidence-observed text-surface",
  fact: "bg-evidence-observed text-surface",
  measurement: "bg-evidence-observed text-surface",
  authoritative: "bg-evidence-authoritative text-surface",
  decision: "bg-evidence-authoritative text-surface",
  declared: "border border-info text-info",
  commitment: "border border-info text-info",
  question: "border border-neutral-600 text-neutral-700",
  stated: "border border-dashed border-neutral-600 text-neutral-700",
  assumption: "border border-dashed border-neutral-600 text-neutral-700",
};

export function EvidenceChip({
  kind,
  label,
  size = "nano",
}: {
  kind: string;
  label?: string;
  size?: PillSize;
}) {
  const key = (kind || "").toLowerCase();
  const style = EVIDENCE[key] ?? "border border-neutral-600 text-neutral-700";
  return (
    <Pill size={size} className={`uppercase ${style}`}>
      {label ?? kind}
    </Pill>
  );
}

/* ── confidence ──────────────────────────────────────────────────────────── */

/**
 * A bar over an empty track, and the track is always full width for the leading
 * item and capped for the rest - so rank is legible from bar length before any
 * number is read.
 */
export function ConfidenceBar({
  value,
  leading = false,
  height = 8,
}: {
  /** 0..1 */
  value: number;
  leading?: boolean;
  height?: 4 | 6 | 8;
}) {
  const pct = Math.max(0, Math.min(1, Number.isFinite(value) ? value : 0)) * 100;
  const h = height === 4 ? "h-1" : height === 6 ? "h-1.5" : "h-2";
  return (
    <div
      className={`${h} w-full bg-neutral-200 ${leading ? "" : "max-w-[120px]"}`}
      role="presentation"
    >
      <div
        className={`${h} ${leading ? "bg-accent" : "bg-neutral-500"}`}
        style={{ width: `${pct.toFixed(1)}%` }}
      />
    </div>
  );
}

/** The delta beside a confidence number. No arrow at all when nothing moved. */
export function Delta({ points }: { points: number | null | undefined }) {
  if (points === null || points === undefined || Math.abs(points) < 0.5) {
    return <span className="text-micro text-neutral-500">—</span>;
  }
  const up = points > 0;
  return (
    <span className={`text-micro font-bold ${up ? "text-accent-strong" : "text-neutral-600"}`}>
      {up ? "▲" : "▼"} {Math.abs(Math.round(points))}
    </span>
  );
}

/* ── sparkline ───────────────────────────────────────────────────────────── */

/**
 * One polyline. No axes, no grid, no fill, no dots - the shape is the whole
 * message, and anything else is furniture competing with it.
 */
export function Sparkline({
  points,
  tone = "context",
  width = 120,
  height = 18,
}: {
  points: readonly number[];
  /** problem = this metric is why you are here; context = it is background. */
  tone?: "problem" | "context" | "nominal";
  width?: number;
  height?: number;
}) {
  if (points.length < 2) {
    return <div style={{ width, height }} aria-hidden />;
  }
  const min = Math.min(...points);
  const max = Math.max(...points);
  const span = max - min || 1;
  const step = width / (points.length - 1);
  const path = points
    .map((value, index) => {
      const x = (index * step).toFixed(1);
      const y = (height - 2 - ((value - min) / span) * (height - 4)).toFixed(1);
      return `${x},${y}`;
    })
    .join(" ");
  const stroke =
    tone === "problem"
      ? "rgb(var(--sf-accent))"
      : tone === "nominal"
        ? "rgb(var(--sf-neutral-400))"
        : "rgb(var(--sf-ink))";
  return (
    <svg
      width={width}
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      className="block"
      aria-hidden
    >
      <polyline points={path} fill="none" stroke={stroke} strokeWidth={2} />
    </svg>
  );
}

/* ── processing indicator ────────────────────────────────────────────────── */

/**
 * A pulsing dot means data is moving. A pulsing pill means somebody owes an
 * answer. A panel that is neither shows a still word in the same slot, so the
 * eye learns one place to look for "is this alive".
 */
export function ProcessingIndicator({
  state,
  label,
}: {
  state: "live" | "working" | "idle" | "stale";
  label?: string;
}) {
  const text = label ?? state.toUpperCase();
  if (state === "idle") {
    return (
      <span className="text-nano uppercase tracking-label text-neutral-600">{text}</span>
    );
  }
  if (state === "stale") {
    return (
      <span className="inline-flex items-center gap-1 text-nano uppercase tracking-label text-warn-ink">
        <span className="h-1.5 w-1.5 bg-warn" aria-hidden />
        {text}
      </span>
    );
  }
  const colour = state === "live" ? "bg-info" : "bg-accent";
  const ink = state === "live" ? "text-info" : "text-accent-strong";
  return (
    <span className={`inline-flex items-center gap-1 text-nano uppercase tracking-label ${ink}`}>
      <span className={`h-1.5 w-1.5 animate-sf-pulse ${colour}`} aria-hidden />
      {text}
    </span>
  );
}

/* ── the panel, which every screen is made of ────────────────────────────── */

/**
 * A card with a header strip. `accent` puts a 4px rule along the top, which in
 * this design means "this card is about that state" and nothing else - never
 * decoration.
 */
export function Card({
  children,
  accent,
  elevated = false,
  className = "",
}: {
  children: ReactNode;
  accent?: Tone;
  elevated?: boolean;
  className?: string;
}) {
  const rule = accent
    ? {
        good: "border-t-4 border-t-ok",
        caution: "border-t-4 border-t-warn",
        info: "border-t-4 border-t-info",
        conflict: "border-t-4 border-t-conflict",
        closed: "border-t-4 border-t-closed",
        accent: "border-t-4 border-t-accent",
        neutral: "border-t-4 border-t-neutral-600",
      }[accent]
    : "";
  return (
    <section
      className={`bg-surface-raised ${rule} ${elevated ? "shadow-sm" : ""} ${className}`}
    >
      {children}
    </section>
  );
}

/** The 11px/0.1em/800 uppercase strip that names a panel. */
export function PanelHeader({
  title,
  meta,
  actions,
}: {
  title: string;
  meta?: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <div className="flex flex-wrap items-baseline justify-between gap-2 border-b-2 border-line px-4 py-3">
      <h2 className="font-display text-micro font-extrabold uppercase tracking-panel">
        {title}
      </h2>
      {(meta || actions) && (
        <span className="flex items-baseline gap-2">
          {meta}
          {actions}
        </span>
      )}
    </div>
  );
}

/** The lighter eyebrow inside a card body. */
export function Kicker({ children }: { children: ReactNode }) {
  return (
    <span className="text-tiny uppercase tracking-panel text-accent">{children}</span>
  );
}

/**
 * What a panel says when the API has nothing for it.
 *
 * Designed, not apologetic: the same chrome as the filled state, and a sentence
 * naming what would fill it. "No data" tells a reader nothing they can act on.
 */
export function EmptyState({ children }: { children: ReactNode }) {
  return <p className="text-small text-ink-muted">{children}</p>;
}

/* ── copy field ──────────────────────────────────────────────────────────── */

export function CopyField({
  label,
  value,
  masked = false,
}: {
  label: string;
  value: string;
  /** Secrets render pre-masked and are never re-fetchable. */
  masked?: boolean;
}) {
  return (
    <div className="flex min-w-0 items-center gap-1.5">
      <span className="shrink-0 text-tiny uppercase tracking-label text-neutral-600">
        {label}
      </span>
      <span className="min-w-0 break-all font-mono text-micro">{value}</span>
      {!masked && (
        <button
          type="button"
          onClick={() => void navigator.clipboard?.writeText(value)}
          className="shrink-0 px-1 py-px text-tiny text-accent hover:bg-brand-100"
          aria-label={`Copy ${label}`}
        >
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden>
            <rect x="9" y="9" width="12" height="12" />
            <path d="M5 15V5a2 2 0 0 1 2-2h10" />
          </svg>
        </button>
      )}
    </div>
  );
}

/* ── avatar ──────────────────────────────────────────────────────────────── */

/** Initials in a square. Muted when the person is not here yet. */
export function Avatar({
  name,
  present = true,
  lead = false,
}: {
  name: string;
  present?: boolean;
  lead?: boolean;
}) {
  const initials = (name || "?")
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0]?.toUpperCase() ?? "")
    .join("");
  const ground = lead ? "bg-accent" : present ? "bg-neutral-800" : "bg-neutral-400";
  return (
    <span
      aria-hidden
      className={`grid h-6 w-6 shrink-0 place-items-center text-tiny font-extrabold text-surface ${ground}`}
    >
      {initials || "?"}
    </span>
  );
}
