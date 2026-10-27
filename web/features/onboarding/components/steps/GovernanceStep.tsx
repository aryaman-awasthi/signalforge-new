"use client";

import { useState } from "react";

import { ApiError } from "@/lib/api/client";
import { Profile, Slo, putGovernance } from "@/features/apps/api";

/**
 * Step 5 - SLOs, the severity matrix and the policy defaults.
 *
 * The policy fields are the ones an operator is allowed to tune. The floor is
 * not: a high-risk class can never auto-execute, the six high-risk classes
 * cannot be removed from the approval list, and an approval may not outlive the
 * situation it was granted for. The floor is enforced on the *merged* policy in
 * the domain, so a partial edit cannot widen it, and a refusal is written to the
 * audit chain before the 422 comes back - the response is seen only by whoever
 * tried.
 *
 * This screen states the floor rather than hiding the controls, because an
 * operator who cannot see the rule cannot design around it.
 */
export function GovernanceStep({
  appId,
  profile,
  onChanged,
}: {
  appId: string;
  profile: Profile;
  onChanged: () => void;
}) {
  const [slos, setSlos] = useState<Slo[]>(profile.slos);
  const [policy, setPolicy] = useState({
    approval_ttl_s: profile.policy.approval_ttl_s,
    stability_window_s: profile.policy.stability_window_s,
    max_action_retries: profile.policy.max_action_retries,
    auto_execute_low_risk: profile.policy.auto_execute_low_risk,
    confidence_threshold: profile.policy.confidence_threshold,
    revalidation_cycles_before_escalation:
      profile.policy.revalidation_cycles_before_escalation,
  });
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const [busy, setBusy] = useState(false);

  async function save() {
    setBusy(true);
    setError(null);
    setSaved(false);
    try {
      await putGovernance(appId, { slos, policy });
      setSaved(true);
      onChanged();
    } catch (failure) {
      setError(
        failure instanceof ApiError ? failure.message : "Could not save governance",
      );
    } finally {
      setBusy(false);
    }
  }

  const numberField = (
    label: string,
    key: keyof typeof policy,
    hint: string,
  ) => (
    <label key={key} className="space-y-1">
      <span className="text-[11px] text-ink-muted">{label}</span>
      <input
        type="number"
        step="any"
        value={String(policy[key])}
        onChange={(event) =>
          setPolicy((current) => ({
            ...current,
            [key]: Number(event.target.value),
          }))
        }
        className="block w-32 rounded-card border border-line bg-surface px-2 py-1 text-sm"
      />
      <span className="block max-w-52 text-[11px] text-ink-muted">{hint}</span>
    </label>
  );

  return (
    <div className="space-y-6">
      <section className="space-y-3">
        <div className="flex items-center gap-3">
          <h2 className="text-lg font-medium">SLO targets</h2>
          <button
            onClick={() =>
              setSlos((current) => [
                ...current,
                {
                  slo_id: "",
                  name: "",
                  objective_pct: 99.9,
                  window_days: 30,
                  journey_id: profile.journeys[0]?.journey_id ?? null,
                  indicator: "",
                  fast_burn_multiplier: 14.4,
                  slow_burn_multiplier: 6,
                  error_budget_pct: 0,
                },
              ])
            }
            className="rounded-card border border-line px-2 py-0.5 text-xs hover:border-accent hover:text-accent-strong"
          >
            Add SLO
          </button>
        </div>
        <p className="text-xs text-ink-muted">
          Fast burn proposes an incident; slow burn opens a Watch. An SLO must name
          a journey the mapping declares.
        </p>
        <div className="space-y-2">
          {slos.map((slo, index) => (
            <div
              key={index}
              className="flex flex-wrap items-end gap-2 rounded-card border border-line bg-surface-raised p-2"
            >
              <input
                value={slo.slo_id}
                placeholder="slo id"
                onChange={(event) =>
                  setSlos((current) =>
                    current.map((item, i) =>
                      i === index ? { ...item, slo_id: event.target.value } : item,
                    ),
                  )
                }
                className="w-40 rounded-card border border-line bg-surface px-2 py-1 text-sm"
              />
              <input
                value={slo.name}
                placeholder="Name"
                onChange={(event) =>
                  setSlos((current) =>
                    current.map((item, i) =>
                      i === index ? { ...item, name: event.target.value } : item,
                    ),
                  )
                }
                className="min-w-40 flex-1 rounded-card border border-line bg-surface px-2 py-1 text-sm"
              />
              <label className="space-y-1">
                <span className="text-[11px] text-ink-muted">objective %</span>
                <input
                  type="number"
                  step="0.01"
                  value={slo.objective_pct}
                  onChange={(event) =>
                    setSlos((current) =>
                      current.map((item, i) =>
                        i === index
                          ? { ...item, objective_pct: Number(event.target.value) }
                          : item,
                      ),
                    )
                  }
                  className="block w-24 rounded-card border border-line bg-surface px-2 py-1 text-sm"
                />
              </label>
              <label className="space-y-1">
                <span className="text-[11px] text-ink-muted">journey</span>
                <select
                  value={slo.journey_id ?? ""}
                  onChange={(event) =>
                    setSlos((current) =>
                      current.map((item, i) =>
                        i === index
                          ? { ...item, journey_id: event.target.value || null }
                          : item,
                      ),
                    )
                  }
                  className="block rounded-card border border-line bg-surface px-2 py-1 text-sm"
                >
                  <option value="">-</option>
                  {profile.journeys.map((journey) => (
                    <option key={journey.journey_id} value={journey.journey_id}>
                      {journey.journey_id}
                    </option>
                  ))}
                </select>
              </label>
              <input
                value={slo.indicator}
                placeholder="indicator (a mapped signal key, e.g. payment_success_ratio)"
                onChange={(event) =>
                  setSlos((current) =>
                    current.map((item, i) =>
                      i === index ? { ...item, indicator: event.target.value } : item,
                    ),
                  )
                }
                className="min-w-40 flex-1 rounded-card border border-line bg-surface px-2 py-1 font-mono text-xs"
              />
              <button
                onClick={() => setSlos((current) => current.filter((_, i) => i !== index))}
                className="text-xs text-danger hover:underline"
              >
                Remove
              </button>
            </div>
          ))}
        </div>
      </section>

      <section className="space-y-3">
        <h2 className="text-lg font-medium">Severity matrix</h2>
        <p className="text-xs text-ink-muted">
          Rules of journey tier × regions affected × success rate × customer impact.
          The most severe match wins; anything matching nothing is the default. It
          ships configured, so the matrix exists from the first second rather than
          from whenever somebody visits this step.
        </p>
        <ul className="divide-y divide-line rounded-card border border-line bg-surface-raised text-xs">
          {profile.severity_matrix.rules.map((rule, index) => (
            <li key={index} className="flex flex-wrap gap-3 px-3 py-2">
              <span className="w-10 font-medium uppercase">{rule.severity}</span>
              <span className="text-ink-muted">
                {rule.max_journey_tier !== null &&
                  `journey tier ≤ ${rule.max_journey_tier} · `}
                {rule.min_regions_affected !== null &&
                  `≥ ${rule.min_regions_affected} region(s) · `}
                {rule.success_rate_below_pct !== null &&
                  `success rate < ${rule.success_rate_below_pct}% · `}
                {rule.customer_impacting !== null &&
                  `customer impacting: ${rule.customer_impacting} · `}
                {rule.description}
              </span>
            </li>
          ))}
          <li className="px-3 py-2 text-ink-muted">
            default: {profile.severity_matrix.default}
          </li>
        </ul>
      </section>

      <section className="space-y-3">
        <h2 className="text-lg font-medium">Policy defaults</h2>
        <div className="rounded-card border border-warn/40 bg-warn/10 p-3 text-xs text-warn">
          <p className="font-medium">The floor, which nothing on this screen can move:</p>
          <ul className="mt-1 list-inside list-disc space-y-0.5">
            <li>a high-risk action class can never be set to auto-execute</li>
            <li>
              the six high-risk classes always require approval:{" "}
              {profile.policy.require_approval_classes.join(", ")}
            </li>
            <li>an approval may not live longer than an hour</li>
            <li>a risk override may raise risk, never lower it</li>
            <li>audit cannot be disabled</li>
          </ul>
          <p className="mt-1">
            Every refusal is written to the audit chain before the error is returned.
          </p>
        </div>
        <div className="flex flex-wrap gap-4">
          {numberField(
            "approval TTL (s)",
            "approval_ttl_s",
            "60-3600. An approval that outlives its situation is a standing permission.",
          )}
          {numberField(
            "stability window (s)",
            "stability_window_s",
            "How long things must stay good before closure is offered.",
          )}
          {numberField(
            "max action retries",
            "max_action_retries",
            "A retry after an uncertain outcome reconciles first.",
          )}
          {numberField(
            "confidence threshold",
            "confidence_threshold",
            "Below this, a proposal is advisory only.",
          )}
          {numberField(
            "revalidation cycles",
            "revalidation_cycles_before_escalation",
            "Failed validations before the incident escalates.",
          )}
          <label className="flex items-center gap-2 text-xs">
            <input
              type="checkbox"
              checked={policy.auto_execute_low_risk}
              onChange={(event) =>
                setPolicy((current) => ({
                  ...current,
                  auto_execute_low_risk: event.target.checked,
                }))
              }
            />
            auto-execute low-risk actions
          </label>
        </div>
      </section>

      {error && (
        <p className="rounded-card border border-danger/40 bg-danger/10 px-3 py-2 text-sm text-danger">
          {error}
        </p>
      )}
      {saved && !error && (
        <p className="rounded-card border border-ok/40 bg-ok/10 px-3 py-2 text-sm text-ok">
          Governance saved.
        </p>
      )}
      <button
        onClick={() => void save()}
        disabled={busy}
        className="rounded-card bg-accent px-3 py-2 text-sm font-medium text-surface-raised hover:bg-accent-strong disabled:opacity-60"
      >
        {busy ? "Saving…" : "Save SLOs and policy"}
      </button>
    </div>
  );
}
