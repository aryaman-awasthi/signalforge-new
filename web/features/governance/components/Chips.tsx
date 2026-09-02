"use client";

import { ReactNode } from "react";

/**
 * The small shared vocabulary of the Sprint 6 panels.
 *
 * Colour carries meaning here and it is deliberately narrow: danger is reserved
 * for a state that requires a person to act before anything else may happen -
 * an uncertain execution, a contradicted statement, a blocked closure - and
 * nothing else uses it. A screen where five things are red at 03:00 is a screen
 * where nothing is.
 */

export type Tone = "neutral" | "ok" | "warn" | "danger" | "accent";

const TONE: Record<Tone, string> = {
  neutral: "border-line text-ink-muted",
  ok: "border-ok/40 bg-ok/10 text-ok",
  warn: "border-warn/40 bg-warn/10 text-warn",
  danger: "border-danger/40 bg-danger/10 text-danger",
  accent: "border-accent/40 bg-accent/10 text-accent-strong",
};

export function Chip({
  tone = "neutral",
  title,
  children,
}: {
  tone?: Tone;
  title?: string;
  children: ReactNode;
}) {
  return (
    <span
      title={title}
      className={`border px-1.5 py-0.5 text-[10px] font-medium ${TONE[tone]}`}
    >
      {children}
    </span>
  );
}

/** The platform's risk class, never the application's declaration. */
export function RiskChip({ riskClass }: { riskClass: string }) {
  const tone: Tone =
    riskClass === "high_risk"
      ? "danger"
      : riskClass === "medium_risk"
        ? "warn"
        : riskClass === "read_only"
          ? "ok"
          : "neutral";
  return (
    <Chip
      tone={tone}
      title={
        riskClass === "high_risk"
          ? "A high-risk class. It can never auto-execute and always needs approval."
          : undefined
      }
    >
      {riskClass.replace("_", " ")}
    </Chip>
  );
}

/** allow / approval required / blocked, in the engine's own words. */
export function OutcomeChip({ outcome }: { outcome: string }) {
  const tone: Tone =
    outcome === "allow" ? "ok" : outcome === "block" ? "danger" : "warn";
  return <Chip tone={tone}>{outcome.replace("_", " ")}</Chip>;
}

/**
 * One rule that fired, with where it came from.
 *
 * A floor rule is rendered differently from an application's own rule because
 * they are different kinds of fact: one is configuration somebody chose and the
 * other is a property of the platform that nobody can change.
 */
export function RuleChip({
  ruleId,
  source,
  detail,
}: {
  ruleId: string;
  source: string;
  detail?: string;
}) {
  return (
    <Chip tone={source === "floor" ? "danger" : "neutral"} title={detail}>
      {source === "floor" ? "🔒 " : ""}
      {ruleId}
    </Chip>
  );
}

/** Deterministic or model-written. Never both, never neither. */
export function ProvenanceChip({ degraded }: { degraded: boolean }) {
  return (
    <Chip
      tone={degraded ? "neutral" : "accent"}
      title={
        degraded
          ? "Written by the platform from stored numbers. No model was involved."
          : "Written by the model from the platform's numbers. The numbers are not the model's."
      }
    >
      {degraded ? "deterministic" : "model-written"}
    </Chip>
  );
}

export function ScoreBar({
  label,
  value,
  negative = false,
}: {
  label: string;
  value: number;
  negative?: boolean;
}) {
  const width = `${Math.round(Math.min(1, Math.max(0, value)) * 100)}%`;
  return (
    <div className="flex items-center gap-2">
      <span className="w-28 shrink-0 text-[10px] uppercase tracking-wide text-ink-muted">
        {label}
      </span>
      <span className="h-1.5 flex-1 bg-line">
        <span
          style={{ width }}
          className={`block h-1.5 ${negative ? "bg-danger" : "bg-accent"}`}
        />
      </span>
      <span className="w-10 shrink-0 text-right font-mono text-[10px]">
        {negative ? "−" : ""}
        {value.toFixed(2)}
      </span>
    </div>
  );
}
