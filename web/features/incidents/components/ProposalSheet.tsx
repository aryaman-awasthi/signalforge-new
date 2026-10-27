"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";

import { ApiError } from "@/lib/api/client";
import { Me, fetchMe } from "@/features/auth/api";
import {
  Detection,
  Lifecycle,
  Severity,
  declareIncident,
} from "@/features/incidents/api";

/**
 * S7 - the proposal sheet: the corroboration checklist, and Declare.
 *
 * This is the screen where the platform's central rule becomes visible. The
 * detector has reached a corroborated proposal and stopped; every button here is
 * a person's, and the sheet says so in the text rather than only in the code.
 *
 * The checklist exists because "the AI thinks this is a P0" is not a thing an
 * incident commander can act on at 03:00. What they can act on is: two
 * independent signal families are saying it, one of them is customer-facing,
 * these regions are affected, this many journeys are failing, and here are the
 * evidence refs. The checklist renders the platform's own corroboration verdict
 * item by item, including the items that did **not** pass - because a proposal
 * that is not corroborated is exactly the one somebody needs to look at
 * carefully before declaring, and hiding the failed rows would make it look like
 * the strong one.
 *
 * Declaring at the proposed severity and declaring at a different one are two
 * separate buttons, not a dropdown with a submit. Both are human acts; the
 * record says which, and the API stores `proposed` or `chosen` accordingly.
 */

const SEVERITIES: Severity[] = ["P0", "P1", "P2", "P3"];

type CheckRow = { label: string; passed: boolean; detail: string };

function checklist(detection: Detection): CheckRow[] {
  const { corroboration, severity, storm } = detection;
  const impact = severity?.impact;
  return [
    {
      label: "Two or more independent signal families",
      passed: corroboration.families.length >= 2,
      detail:
        corroboration.families.length > 0
          ? corroboration.families.join(", ")
          : "no family is breaching",
    },
    {
      label: "At least one is customer-facing",
      passed: corroboration.customer_facing_families.length > 0,
      detail:
        corroboration.customer_facing_families.join(", ") ||
        "nothing customer-facing is implicated",
    },
    {
      label: "A customer is feeling it",
      passed: Boolean(impact?.customer_impacting),
      detail: impact?.detail || "no journey impact measured",
    },
    {
      label: "More than one region affected",
      passed: (impact?.regions_affected ?? 0) > 1,
      detail: impact
        ? `${impact.regions_affected} of ${impact.regions_total}${
            impact.affected_regions.length
              ? `: ${impact.affected_regions.join(", ")}`
              : ""
          }`
        : "no region evidence",
    },
    {
      label: "Not an alert storm being read as many faults",
      passed: !storm.storming,
      detail: storm.storming
        ? `${storm.alerts} alerts in ${storm.clusters.length} cluster(s), ${storm.suppressed} suppressed`
        : "no storm",
    },
  ];
}

export function ProposalSheet({
  appId,
  lifecycle,
  detection,
  onDeclared,
}: {
  appId: string;
  lifecycle: Lifecycle;
  detection: Detection | null;
  onDeclared?: () => void;
}) {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [reason, setReason] = useState("");
  const [chosen, setChosen] = useState<Severity | "">("");
  const [manual, setManual] = useState(false);
  const [me, setMe] = useState<Me | null>(null);

  useEffect(() => {
    let live = true;
    fetchMe()
      .then((who) => {
        if (live) setMe(who);
      })
      .catch(() => {
        if (live) setMe(null);
      });
    return () => {
      live = false;
    };
  }, []);

  // The sheet is for a stance, never for an incident. Once somebody has
  // declared, the war room is the screen - a second Declare button beside a
  // live incident is how a duplicate gets pressed.
  if (lifecycle.source === "incident") return null;

  // And it is for a *proposal*. Steady and Watch are stances in which the
  // platform has decided nothing, and a row of P0-P3 buttons on a healthy
  // application is an invitation to declare one. A person who holds the
  // permission can still open the sheet by hand - the human override is the
  // product's central rule - but that is a deliberate act behind a link, not
  // the default face of every application page.
  const canDeclare = Boolean(me?.permissions.includes("declare_incident"));
  const proposed = lifecycle.state === "proposed";
  if (!proposed) {
    if (!canDeclare) return null;
    if (!manual) {
      return (
        <p className="text-right">
          <button
            type="button"
            onClick={() => setManual(true)}
            className="text-xs text-ink-muted hover:text-ink hover:underline"
          >
            Declare an incident manually…
          </button>
        </p>
      );
    }
  }

  const proposal = proposed ? detection?.severity ?? null : null;

  async function declare(severity?: Severity) {
    setBusy(true);
    setError(null);
    try {
      const incident = await declareIncident(appId, {
        severity,
        reason: reason.trim() || undefined,
      });
      onDeclared?.();
      router.push(`/incidents/${incident.id}`);
    } catch (caught) {
      setError(
        caught instanceof ApiError ? caught.message : "Could not declare an incident",
      );
      setBusy(false);
    }
  }

  return (
    <section
      aria-label="Severity proposal"
      className="space-y-3 rounded-card border border-accent/40 bg-accent/5 p-3"
    >
      <div className="flex flex-wrap items-baseline gap-2">
        <h2 className="text-sm font-semibold uppercase tracking-wide">
          Severity proposal
        </h2>
        {proposal ? (
          <>
            <span className="border border-accent/50 bg-surface-raised px-2 py-0.5 font-mono text-sm font-semibold">
              {proposal.severity}
            </span>
            <span className="text-xs text-ink-muted">by rule {proposal.rule}</span>
          </>
        ) : (
          <span className="text-xs text-ink-muted">
            The matrix is not proposing a severity. A person can still declare -
            they just have to name one.
          </span>
        )}
        <span className="flex-1" />
        {/* Published as a constant by the API and rendered from it, so this
            sentence cannot drift from what the platform actually enforces. */}
        <span className="text-xs text-ink-muted">
          Proposed, not declared. Only a person declares an incident.
        </span>
      </div>

      {detection && (
        <ul className="space-y-1">
          {checklist(detection).map((row) => (
            <li key={row.label} className="flex items-start gap-2 text-sm">
              <span
                aria-hidden
                className={row.passed ? "text-ok" : "text-ink-muted"}
              >
                {row.passed ? "✓" : "○"}
              </span>
              <span className={row.passed ? "" : "text-ink-muted"}>
                {row.label}
                <span className="ml-2 text-xs opacity-80">{row.detail}</span>
              </span>
            </li>
          ))}
        </ul>
      )}

      {detection && !detection.corroboration.corroborated && (
        <p className="rounded-card border border-warn/40 bg-warn/10 px-2.5 py-1.5 text-xs text-warn">
          Not corroborated: {detection.corroboration.reason || "one signal family, not two"}.
          Whatever this looks like, the cause is a hypothesis - which is a reason
          to look, not a reason not to declare.
        </p>
      )}

      {proposal && proposal.evidence.length > 0 && (
        <details className="rounded-card border border-line bg-surface-raised px-2.5 py-1.5">
          <summary className="cursor-pointer text-xs text-ink-muted">
            {proposal.evidence.length} evidence ref
            {proposal.evidence.length === 1 ? "" : "s"}
          </summary>
          <ul className="mt-1 space-y-0.5 text-xs text-ink-muted">
            {proposal.evidence.map((ref) => (
              <li key={`${ref.kind}-${ref.ref}`} className="font-mono">
                {ref.kind} {ref.ref}
                {ref.region ? ` [${ref.region}]` : ""}
                {ref.detail ? ` — ${ref.detail}` : ""}
              </li>
            ))}
          </ul>
        </details>
      )}

      <label className="block text-xs text-ink-muted">
        Why (optional, kept on the incident record)
        <input
          value={reason}
          onChange={(event) => setReason(event.target.value)}
          className="mt-1 w-full rounded-card border border-line bg-surface-raised px-2 py-1 text-sm text-ink"
          placeholder="what made you declare"
        />
      </label>

      {error && (
        <p className="rounded-card border border-danger/40 bg-danger/10 px-2.5 py-1.5 text-sm text-danger">
          {error}
        </p>
      )}

      <div className="flex flex-wrap items-center gap-2">
        {me === null ? null : !canDeclare ? (
          <span className="text-xs text-ink-muted">
            Only an Incident Commander or Tech Lead can declare. The proposal
            stays here until one of them does.
          </span>
        ) : (
          <>
            {proposal && (
              <button
                type="button"
                disabled={busy}
                onClick={() => void declare()}
                className="rounded-card bg-accent px-3 py-1.5 text-sm font-medium text-surface-raised hover:bg-accent-strong disabled:opacity-60"
              >
                Declare {proposal.severity} as proposed
              </button>
            )}
            <span className="text-xs text-ink-muted">
              {proposal ? "or declare at a different severity:" : "declare at:"}
            </span>
            {/* Picking a severity arms it; a second, differently worded button
                declares. Declaring at the proposed severity is one press because
                that is the act the sheet exists for; declaring at a severity the
                platform did not propose - or with no proposal at all - is a
                heavier act and gets a confirm. */}
            {SEVERITIES.map((severity) => (
              <button
                key={severity}
                type="button"
                disabled={busy}
                onClick={() => setChosen(chosen === severity ? "" : severity)}
                className={`rounded-card border px-2.5 py-1.5 font-mono text-sm disabled:opacity-60 ${
                  chosen === severity
                    ? "border-accent text-accent-strong"
                    : "border-line hover:border-accent hover:text-accent-strong"
                }`}
              >
                {severity}
              </button>
            ))}
            {chosen && (
              <button
                type="button"
                disabled={busy}
                onClick={() => void declare(chosen)}
                className="rounded-card border border-danger px-3 py-1.5 text-sm font-medium text-danger hover:bg-danger/10 disabled:opacity-60"
              >
                Confirm: declare {chosen}
                {proposal ? " (override)" : ""}
              </button>
            )}
            {manual && (
              <button
                type="button"
                onClick={() => {
                  setManual(false);
                  setChosen("");
                }}
                className="text-xs text-ink-muted hover:underline"
              >
                Cancel
              </button>
            )}
          </>
        )}
      </div>
      {proposal && (
        <p className="text-xs text-ink-muted">
          Choosing a different severity is recorded as an override, with what the
          matrix proposed kept beside it.
        </p>
      )}
    </section>
  );
}
