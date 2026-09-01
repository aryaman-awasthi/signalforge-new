"use client";

import { useState } from "react";

import { ApiError } from "@/lib/api/client";
import {
  Approval,
  Execution,
  approveExecution,
  denyExecution,
} from "@/features/governance/api";
import { Chip, RiskChip, RuleChip } from "@/features/governance/components/Chips";
import {
  formatCountdown,
  useSecondsLeft,
} from "@/features/governance/hooks/useGovernance";

/**
 * S11's approval sheet.
 *
 * Four things are on the screen before anybody can press anything, and they are
 * the four an approver is actually being asked about:
 *
 * - **what** - the action, its risk class and the arguments as the ledger
 *   stored them (already redacted server-side);
 * - **which rule** - the floor rule or policy rule that made approval necessary,
 *   named rather than summarised;
 * - **who** - the roles entitled to approve, and that the requester is not one
 *   of them for their own request;
 * - **for how long** - a live countdown from the server-issued expiry, after
 *   which the approval is refused at execution whether or not this tab is open.
 *
 * The typed confirmation is the last control, and it asks for the action id
 * rather than "YES". Typing `rollback-checkout` is a moment in which a person
 * reads what they are approving; typing YES is muscle memory.
 */
export function ApprovalSheet({
  incidentId,
  execution,
  approval,
  canApprove,
  onChanged,
}: {
  incidentId: string;
  execution: Execution;
  approval: Approval | null;
  canApprove: boolean;
  onChanged: () => void;
}) {
  const [typed, setTyped] = useState("");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const left = useSecondsLeft(approval?.expires_at ?? null);

  const granted = approval?.state === "granted";
  const confirmed = typed.trim() === execution.action_id;

  async function decide(kind: "approve" | "deny") {
    setBusy(true);
    setError(null);
    try {
      if (kind === "approve") {
        await approveExecution(incidentId, execution.execution_id, note);
      } else {
        await denyExecution(incidentId, execution.execution_id, note);
      }
      setTyped("");
      setNote("");
      onChanged();
    } catch (caught) {
      setError(
        caught instanceof ApiError ? caught.message : "The decision was refused",
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <article className="rounded-card border border-warn/40 bg-warn/5 px-3 py-2.5">
      <header className="flex flex-wrap items-center gap-2">
        <h3 className="text-sm font-semibold">{execution.action_id}</h3>
        <RiskChip riskClass={execution.risk_class} />
        <Chip tone="warn">approval required</Chip>
        <span className="flex-1" />
        {granted && (
          <span
            className={`font-mono text-sm font-semibold ${left < 60 ? "text-danger" : "text-warn"}`}
            title="After this the approval is refused at execution, whether or not this tab is open."
          >
            {formatCountdown(left)}
          </span>
        )}
      </header>

      <p className="mt-1.5 text-xs text-ink-muted">
        {execution.decision_reason}
      </p>

      <div className="mt-1.5 flex flex-wrap gap-1">
        {execution.rules_hit.map((hit) => (
          <RuleChip
            key={hit.rule_id}
            ruleId={hit.rule_id}
            source={hit.source}
            detail={hit.detail}
          />
        ))}
      </div>

      <dl className="mt-2 grid gap-1 text-[11px] sm:grid-cols-2">
        <div>
          <dt className="inline font-medium">Requested by: </dt>
          <dd className="inline text-ink-muted">{execution.requested_by}</dd>
        </div>
        <div>
          <dt className="inline font-medium">May be approved by: </dt>
          <dd className="inline text-ink-muted">
            {approval?.required_roles.join(", ") || "incident command"}
          </dd>
        </div>
        <div>
          <dt className="inline font-medium">Arguments: </dt>
          <dd className="inline font-mono text-ink-muted">
            {JSON.stringify(execution.args)}
          </dd>
        </div>
        <div>
          <dt className="inline font-medium">Under policy: </dt>
          <dd className="inline text-ink-muted">
            {execution.policy_ref} · floor {execution.floor_version}
          </dd>
        </div>
      </dl>

      {granted ? (
        <p className="mt-2 rounded-card border border-ok/40 bg-ok/10 px-2 py-1.5 text-[11px] text-ok">
          Approved by {approval?.approver_label || approval?.approver_user_id} (
          {approval?.approver_roles.join(", ")}). It expires in{" "}
          {formatCountdown(left)} and authorises this request only - the action,
          these arguments, this policy version.
        </p>
      ) : (
        <div className="mt-2 space-y-2">
          {error && (
            <p className="rounded-card border border-danger/40 bg-danger/10 px-2 py-1.5 text-[11px] text-danger">
              {error}
            </p>
          )}
          <label className="block text-[11px]">
            <span className="text-ink-muted">
              Type <code className="font-mono">{execution.action_id}</code> to
              confirm you have read what you are approving
            </span>
            <input
              value={typed}
              onChange={(event) => setTyped(event.target.value)}
              disabled={!canApprove}
              className="mt-1 w-full rounded-card border border-line bg-surface px-2 py-1 font-mono text-xs"
              placeholder={execution.action_id}
            />
          </label>
          <label className="block text-[11px]">
            <span className="text-ink-muted">Note (goes on the record)</span>
            <input
              value={note}
              onChange={(event) => setNote(event.target.value)}
              disabled={!canApprove}
              className="mt-1 w-full rounded-card border border-line bg-surface px-2 py-1 text-xs"
            />
          </label>
          <div className="flex flex-wrap items-center gap-2">
            <button
              type="button"
              disabled={!canApprove || !confirmed || busy}
              onClick={() => void decide("approve")}
              className="rounded-card bg-accent px-3 py-1.5 text-xs font-medium text-surface-raised hover:bg-accent-strong disabled:opacity-50"
            >
              Approve
            </button>
            <button
              type="button"
              disabled={!canApprove || busy}
              onClick={() => void decide("deny")}
              className="rounded-card border border-line px-3 py-1.5 text-xs hover:border-danger disabled:opacity-50"
            >
              Deny
            </button>
            {!canApprove && (
              <span className="text-[11px] text-ink-muted">
                Approving needs approve_high_risk, and the person who requested
                it cannot approve it.
              </span>
            )}
          </div>
        </div>
      )}
    </article>
  );
}
