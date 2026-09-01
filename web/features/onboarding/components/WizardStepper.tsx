"use client";

import Link from "next/link";

import { WIZARD_STEPS } from "@/features/onboarding/steps";
import type { Readiness } from "@/features/apps/api";

/**
 * The seven steps with per-step progress read straight off the readiness score.
 *
 * The numbers are the domain's: `readiness.steps` is `step -> [earned,
 * available]`, so a step reads complete because the platform's own weighted
 * checks say so, not because a form was submitted once. A step whose points are
 * partly earned shows partly earned rather than a tick.
 */
export function WizardStepper({
  appId,
  current,
  readiness,
}: {
  appId: string;
  current: number;
  readiness: Readiness;
}) {
  const blocking = new Set(
    readiness.checks.filter((check) => check.blocking && !check.passed).map((c) => c.step),
  );

  return (
    <ol className="flex flex-wrap gap-1">
      {WIZARD_STEPS.map((step, index) => {
        const number = index + 1;
        const [earned, available] = readiness.steps[step.key] ?? [0, 0];
        const complete = available > 0 && earned >= available;
        const active = number === current;
        return (
          <li key={step.key}>
            <Link
              href={`/apps/${encodeURIComponent(appId)}/onboard/${number}`}
              className={`flex items-center gap-2 rounded-card border px-3 py-1.5 text-xs ${
                active
                  ? "border-accent text-accent"
                  : "border-line text-ink-muted hover:text-ink"
              }`}
            >
              <span
                className={`flex h-5 w-5 items-center justify-center rounded-full text-[11px] ${
                  complete
                    ? "bg-ok/20 text-ok"
                    : blocking.has(step.key)
                      ? "bg-danger/20 text-danger"
                      : "bg-line text-ink-muted"
                }`}
              >
                {complete ? "✓" : number}
              </span>
              <span>{step.title}</span>
              {available > 0 && (
                <span className="font-mono text-[10px]">
                  {earned}/{available}
                </span>
              )}
            </Link>
          </li>
        );
      })}
    </ol>
  );
}
