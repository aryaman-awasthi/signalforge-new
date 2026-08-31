"use client";

import { useState } from "react";

import { ApiError } from "@/lib/api/client";
import {
  EscalationStep,
  OrgUser,
  PagePlan,
  Policy,
  Rotation,
  deletePolicy,
  savePolicy,
  testFirePolicy,
} from "@/features/org-setup/api";
import { FormError } from "@/features/auth/components/Field";

/** Escalation policies: ordered steps with ack timers (REQ-014). Test fire is
 * simulated, requires run_drills, and shows the resolved page plan. */
export function EscalationPanel({
  users,
  rotations,
  policies,
  onChanged,
}: {
  users: OrgUser[];
  rotations: Rotation[];
  policies: Policy[];
  onChanged: () => void;
}) {
  const [name, setName] = useState("");
  const [steps, setSteps] = useState<EscalationStep[]>([]);
  const [error, setError] = useState<string>();
  const [plans, setPlans] = useState<Record<string, PagePlan | string>>({});

  const userName = (id: string) =>
    users.find((u) => u.id === id)?.display_name ?? id;
  const targetName = (step: { target_type: string; target_id: string }) =>
    step.target_type === "rotation"
      ? `rotation ${rotations.find((r) => r.id === step.target_id)?.name ?? step.target_id}`
      : userName(step.target_id);

  function addStep() {
    const target = rotations[0]
      ? { target_type: "rotation" as const, target_id: rotations[0].id }
      : { target_type: "user" as const, target_id: users[0]?.id ?? "" };
    setSteps((current) => [...current, { ...target, timeout_s: 300 }]);
  }

  function patchStep(index: number, patch: Partial<EscalationStep>) {
    setSteps((current) =>
      current.map((step, i) => (i === index ? { ...step, ...patch } : step)),
    );
  }

  async function submit() {
    setError(undefined);
    try {
      await savePolicy({ name, steps, app_id: null });
      setName("");
      setSteps([]);
      onChanged();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Something went wrong");
    }
  }

  async function testFire(policyId: string) {
    try {
      const plan = await testFirePolicy(policyId);
      setPlans((current) => ({ ...current, [policyId]: plan }));
    } catch (e) {
      setPlans((current) => ({
        ...current,
        [policyId]:
          e instanceof ApiError && e.status === 403
            ? `Denied: ${e.detail ?? "requires run_drills (incident command)"}`
            : "Test fire failed",
      }));
    }
  }

  return (
    <section className="space-y-4">
      <form
        className="space-y-3 rounded-card border border-line bg-surface-raised p-4"
        onSubmit={(event) => {
          event.preventDefault();
          void submit();
        }}
      >
        <h3 className="font-semibold">New escalation policy</h3>
        <input
          required
          value={name}
          onChange={(event) => setName(event.target.value)}
          placeholder="Policy name, e.g. sev1"
          className="w-full rounded-card border border-line bg-surface px-3 py-2 text-sm sm:max-w-xs"
        />
        <ol className="space-y-2">
          {steps.map((step, index) => (
            <li key={index} className="flex flex-wrap items-center gap-2 text-sm">
              <span className="w-5 text-ink-muted">{index + 1}.</span>
              <select
                value={`${step.target_type}:${step.target_id}`}
                onChange={(event) => {
                  const separator = event.target.value.indexOf(":");
                  patchStep(index, {
                    target_type: event.target.value.slice(0, separator) as
                      | "user"
                      | "rotation",
                    target_id: event.target.value.slice(separator + 1),
                  });
                }}
                className="rounded-card border border-line bg-surface px-2 py-1"
              >
                {rotations.map((rotation) => (
                  <option key={rotation.id} value={`rotation:${rotation.id}`}>
                    rotation: {rotation.name}
                  </option>
                ))}
                {users.map((user) => (
                  <option key={user.id} value={`user:${user.id}`}>
                    user: {user.display_name}
                  </option>
                ))}
              </select>
              <label className="flex items-center gap-1 text-ink-muted">
                escalate after
                <input
                  type="number"
                  min={1}
                  max={86400}
                  value={step.timeout_s}
                  onChange={(event) =>
                    patchStep(index, { timeout_s: Number(event.target.value) })
                  }
                  className="w-24 rounded-card border border-line bg-surface px-2 py-1"
                />
                s
              </label>
              <button
                type="button"
                onClick={() => setSteps((c) => c.filter((_, i) => i !== index))}
                className="text-danger hover:underline"
              >
                remove
              </button>
            </li>
          ))}
        </ol>
        <div className="flex gap-3">
          <button type="button" onClick={addStep}
            className="rounded-card border border-line px-3 py-2 text-sm text-ink-muted hover:border-accent hover:text-ink">
            + Add step
          </button>
          <button
            type="submit"
            disabled={steps.length === 0}
            className="rounded-card bg-accent px-3 py-2 text-sm font-medium text-surface-raised hover:bg-accent-strong disabled:opacity-60"
          >
            Save policy
          </button>
        </div>
        <FormError message={error} />
      </form>

      <ul className="space-y-2">
        {policies.map((policy) => {
          const plan = plans[policy.id];
          return (
            <li key={policy.id} className="rounded-card border border-line bg-surface-raised px-4 py-3 text-sm">
              <div className="flex items-center justify-between">
                <p className="font-medium">{policy.name}</p>
                <div className="flex gap-3">
                  <button onClick={() => void testFire(policy.id)}
                    className="text-xs text-accent hover:underline">
                    Test fire (simulated)
                  </button>
                  <button onClick={() => void deletePolicy(policy.id).then(onChanged)}
                    className="text-xs text-danger hover:underline">
                    Delete
                  </button>
                </div>
              </div>
              <ol className="mt-1 space-y-0.5 text-ink-muted">
                {policy.steps.map((step, index) => (
                  <li key={index}>
                    t+{policy.page_offsets_s[index]}s: page {targetName(step)}, ack within{" "}
                    {step.timeout_s}s
                  </li>
                ))}
              </ol>
              {typeof plan === "string" && (
                <p className="mt-2 rounded-card border border-danger/40 bg-danger/10 px-2 py-1 text-xs text-danger">
                  {plan}
                </p>
              )}
              {plan !== undefined && typeof plan !== "string" && (
                <div className="mt-2 rounded-card border border-ok/40 bg-ok/10 px-2 py-1 text-xs">
                  <p className="font-medium">Simulated fire - nobody was paged:</p>
                  {plan.plan.map((entry) => (
                    <p key={entry.order}>
                      t+{entry.page_at_offset_s}s → {userName(entry.resolved_user_id)}
                    </p>
                  ))}
                </div>
              )}
            </li>
          );
        })}
        {policies.length === 0 && (
          <li className="text-sm text-ink-muted">No escalation policies yet.</li>
        )}
      </ul>
    </section>
  );
}
