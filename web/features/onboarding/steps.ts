/**
 * The seven wizard steps, in the order the platform walks them.
 *
 * The keys are the domain's own `WizardStep` values, which is what lets the
 * stepper show per-step progress straight off `readiness.steps` instead of the
 * cockpit inventing a second notion of "done" that could disagree with the
 * score.
 */

export const WIZARD_STEPS = [
  {
    key: "basics",
    title: "Basics",
    blurb:
      "Who this application is and where it lives. The base URL does real work: " +
      "auto-discovery walks it and every adapter config that takes one is pre-filled from it.",
  },
  {
    key: "connect",
    title: "Connect",
    blurb:
      "Bind integrations. Pull adapters take config and a credential; push adapters " +
      "get an ingest URL and a credential the sender holds; simulated adapters are built in.",
  },
  {
    key: "map",
    title: "Map",
    blurb:
      "Golden signals including business counters, the region label, journeys, probes, " +
      "and the actions this application publishes in its own manifest.",
  },
  {
    key: "topology",
    title: "Topology",
    blurb:
      "Upload the dependency map. Versioned by content, so re-uploading the same map " +
      "is one version and not a second.",
  },
  {
    key: "slos",
    title: "SLOs & policy",
    blurb:
      "Objectives, the severity matrix and the policy defaults. The floor is enforced " +
      "on whatever you save - a high-risk class can never be set to auto-execute.",
  },
  {
    key: "baseline",
    title: "Baseline",
    blurb:
      "Learn what normal looks like for each mapped signal. A run that reaches some " +
      "signals and not others reports the misses by name and keeps what it got.",
  },
  {
    key: "test",
    title: "Test",
    blurb:
      "Every connector tested in its own direction, every journey probe run in every " +
      "region, and the readiness score recomputed from what came back.",
  },
] as const;

export type WizardStepKey = (typeof WIZARD_STEPS)[number]["key"];

/** Steps are addressed as 1..7 in the URL; humans count from one. */
export function stepFromParam(raw: string | undefined): number {
  const parsed = Number.parseInt(raw ?? "1", 10);
  if (!Number.isFinite(parsed)) return 1;
  return Math.min(Math.max(parsed, 1), WIZARD_STEPS.length);
}
