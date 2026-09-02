/**
 * The composite pieces of the component sheet (S8-T06).
 *
 * These are the shapes that appear on more than one screen and would otherwise
 * be re-typed as inline markup each time: the plan-diff row, the ledger step,
 * the layer verdict tile, the ack row, the label/value grid an approval sheet is
 * made of. They take plain props - a string, a number, a verdict - and know
 * nothing about incidents, so a feature passes its own types in and no domain
 * shape leaks into `lib/`.
 *
 * Everything here is presentational. Not one of them decides anything.
 */

import type { ReactNode } from "react";

import { Badge, Pill, toneClass, type Tone } from "@/lib/ui/primitives";

/* ── plan diff ───────────────────────────────────────────────────────────── */

export type PlanVerb = "kept" | "paused" | "added" | "removed" | "changed";

const PLAN_VERB: Record<PlanVerb, { ink: string; row: string; strike: boolean }> = {
  kept: { ink: "text-ok", row: "", strike: false },
  paused: { ink: "text-warn-ink", row: "bg-warn-bg", strike: false },
  added: { ink: "text-info", row: "bg-info-bg", strike: false },
  removed: { ink: "text-neutral-500", row: "", strike: true },
  changed: { ink: "text-accent-strong", row: "bg-brand-100", strike: false },
};

/**
 * One line of "what changed between plan v3 and plan v4".
 *
 * The verb is the whole point: a plan revision that reads as a new plan hides
 * the fact that a step was *paused* rather than dropped, and paused is the state
 * an operator has to go back to.
 */
export function PlanDiffRow({
  verb,
  children,
}: {
  verb: PlanVerb;
  children: ReactNode;
}) {
  const style = PLAN_VERB[verb];
  return (
    <div
      className={`grid grid-cols-[70px_1fr] gap-2.5 border-t border-line px-1 py-2 font-mono text-dense ${style.row}`}
    >
      <span className={`text-tiny font-semibold uppercase tracking-[0.06em] ${style.ink}`}>
        {verb}
      </span>
      <span className={style.strike ? "line-through" : ""}>{children}</span>
    </div>
  );
}

/* ── ledger step ─────────────────────────────────────────────────────────── */

export type LedgerStepState =
  | "completed"
  | "running"
  | "uncertain"
  | "read_only"
  | "duplicate"
  | "blocked"
  | "queued";

/**
 * The 24px marker at the head of an execution-ledger row.
 *
 * The states that matter most are the ones that are not success: `uncertain` is
 * an outcome nobody knows, `duplicate` is an attempt the platform refused, and
 * both get a marker a reader can pick out of a column without reading a word.
 */
export function LedgerStepIcon({
  state,
  ordinal,
}: {
  state: LedgerStepState;
  ordinal: number;
}) {
  const base = "grid h-6 w-6 shrink-0 place-items-center text-small font-extrabold";
  if (state === "completed") {
    return <span className={`${base} bg-ok text-surface`}>✓</span>;
  }
  if (state === "duplicate") {
    return <span className={`${base} border border-conflict bg-conflict`} aria-hidden />;
  }
  if (state === "read_only") {
    return <span className={`${base} border-2 border-info text-info`}>{ordinal}</span>;
  }
  if (state === "blocked") {
    return <span className={`${base} border-2 border-accent text-accent-strong`}>!</span>;
  }
  if (state === "queued") {
    return <span className={`${base} border-2 border-neutral-400 text-neutral-500`}>{ordinal}</span>;
  }
  return <span className={`${base} border-2 border-warn text-warn-ink`}>{ordinal}</span>;
}

/** The row that marker heads: title, badges, timestamp, then one checkpoint line. */
export function LedgerStep({
  state,
  ordinal,
  title,
  badges,
  at,
  detail,
  actions,
  first = false,
  last = false,
}: {
  state: LedgerStepState;
  ordinal: number;
  title: string;
  badges?: ReactNode;
  at?: string;
  detail?: ReactNode;
  actions?: ReactNode;
  first?: boolean;
  last?: boolean;
}) {
  return (
    <div
      className={`grid grid-cols-[28px_1fr] gap-4 py-4 ${
        first ? "border-t-2 border-line" : "border-t border-line"
      } ${last ? "border-b-2 border-line" : ""}`}
    >
      <LedgerStepIcon state={state} ordinal={ordinal} />
      <div className="flex min-w-0 flex-col gap-1.5">
        <div className="flex flex-wrap items-baseline gap-2.5">
          <strong className="text-dense">{title}</strong>
          {badges}
          {at && <span className="font-mono text-small text-ink-muted">{at}</span>}
          {actions && <span className="ml-auto">{actions}</span>}
        </div>
        {detail && <span className="text-small text-ink-muted">{detail}</span>}
      </div>
    </div>
  );
}

/* ── layer verdict ───────────────────────────────────────────────────────── */

export type LayerVerdict = "pass" | "partial" | "fail" | "unknown";

const VERDICT_TONE: Record<LayerVerdict, Tone> = {
  pass: "good",
  partial: "caution",
  fail: "accent",
  unknown: "neutral",
};

/**
 * One of the four layers recovery is judged on.
 *
 * The failing tile is the only one that lifts off the page, because the whole
 * reason this screen is four tiles and not one number is that a healthy estate
 * with a failing customer journey has not recovered.
 */
export function LayerVerdictTile({
  layer,
  verdict,
  readings,
}: {
  layer: string;
  verdict: LayerVerdict;
  readings: readonly string[];
}) {
  const tone = VERDICT_TONE[verdict];
  const rule = {
    good: "border-t-4 border-t-ok",
    caution: "border-t-4 border-t-warn",
    accent: "border-t-4 border-t-accent",
    neutral: "border-t-4 border-t-neutral-400",
    info: "border-t-4 border-t-info",
    conflict: "border-t-4 border-t-conflict",
    closed: "border-t-4 border-t-closed",
  }[tone];
  return (
    <div
      className={`flex flex-col gap-2 bg-surface-raised p-4 ${rule} ${
        verdict === "fail" ? "shadow-md" : ""
      }`}
    >
      <div className="flex items-baseline justify-between gap-2">
        <span className="text-tiny uppercase tracking-panel text-accent">{layer}</span>
        <Badge label={verdict.toUpperCase()} size="micro" />
      </div>
      <div className="flex flex-col gap-0.5 font-mono text-small">
        {readings.length === 0 ? (
          <span className="text-ink-muted">not measured</span>
        ) : (
          readings.map((reading) => (
            <span key={reading} className={verdict === "fail" ? "text-accent-strong" : ""}>
              {reading}
            </span>
          ))
        )}
      </div>
    </div>
  );
}

/* ── ack row ─────────────────────────────────────────────────────────────── */

export type AckState = "paged" | "acked" | "joined" | "escalated" | "pending";

const ACK_BADGE: Record<AckState, { label: string; tone: Tone; fill: "filled" | "outlined"; pulse: boolean }> = {
  pending: { label: "PENDING", tone: "neutral", fill: "outlined", pulse: false },
  paged: { label: "PAGED", tone: "caution", fill: "outlined", pulse: true },
  acked: { label: "ACKED", tone: "info", fill: "outlined", pulse: false },
  joined: { label: "JOINED", tone: "good", fill: "filled", pulse: false },
  escalated: { label: "ESCALATED", tone: "accent", fill: "filled", pulse: false },
};

/**
 * A page, and whether anyone answered it.
 *
 * The pill pulses only while somebody owes an answer, and the countdown beside
 * it is the escalation timer - both stop the moment a person acknowledges, which
 * is what makes the board readable at a glance from across a room.
 */
export function AckStatePill({ state }: { state: AckState }) {
  const spec = ACK_BADGE[state] ?? ACK_BADGE.pending;
  return (
    <Pill
      size="tiny"
      pulse={spec.pulse}
      className={`uppercase ${toneClass(spec.tone, spec.fill)}`}
    >
      {spec.label}
    </Pill>
  );
}

/** The escalation countdown. Amber while it runs, silent once it cannot fire. */
export function EscalationCountdown({
  seconds,
  running,
}: {
  seconds: number | null;
  running: boolean;
}) {
  if (!running || seconds === null) {
    return <span className="font-mono text-small text-neutral-500">—</span>;
  }
  const clamped = Math.max(0, Math.floor(seconds));
  const minutes = Math.floor(clamped / 60);
  const rest = clamped % 60;
  return (
    <span className="font-mono text-small tabular-nums text-warn-ink">
      {minutes}:{String(rest).padStart(2, "0")}
    </span>
  );
}

/* ── label/value grid ────────────────────────────────────────────────────── */

/**
 * The shape an approval sheet, an NBA detail block and a policy summary all
 * share: a narrow uppercase label against a value that can wrap.
 */
export function FactGrid({
  rows,
  labelWidth = "150px",
}: {
  rows: readonly { label: string; value: ReactNode }[];
  labelWidth?: string;
}) {
  return (
    <dl className="flex flex-col gap-1.5 text-dense">
      {rows.map((row) => (
        <div
          key={row.label}
          className="grid gap-2"
          style={{ gridTemplateColumns: `${labelWidth} 1fr` }}
        >
          <dt className="text-tiny uppercase tracking-panel text-neutral-600">
            {row.label}
          </dt>
          <dd className="min-w-0">{row.value}</dd>
        </div>
      ))}
    </dl>
  );
}

/* ── buttons ─────────────────────────────────────────────────────────────── */

const BUTTON: Record<string, string> = {
  primary: "bg-accent text-surface hover:bg-brand-600 active:bg-brand-700",
  secondary: "border border-line hover:bg-ink/[0.07] active:bg-ink/[0.14]",
  ghost: "text-accent px-1 hover:bg-accent/10 active:bg-accent/[0.18]",
  danger: "bg-accent text-surface hover:bg-brand-700",
};

/**
 * Square, Archivo, 14px. The design has no corners and no icon-only
 * primary action; a destructive-looking button that is not destructive is worse
 * than a plain one.
 */
export function Button({
  variant = "secondary",
  size = "base",
  className = "",
  children,
  ...rest
}: {
  variant?: "primary" | "secondary" | "ghost" | "danger";
  size?: "base" | "small";
  className?: string;
  children: ReactNode;
} & React.ButtonHTMLAttributes<HTMLButtonElement>) {
  const pad = size === "small" ? "px-2 py-1 text-small" : "px-3.5 py-2 text-dense";
  return (
    <button
      {...rest}
      className={`inline-flex items-center justify-center gap-1.5 font-display font-extrabold leading-tight disabled:cursor-not-allowed disabled:opacity-45 ${pad} ${BUTTON[variant]} ${className}`}
    >
      {children}
    </button>
  );
}
