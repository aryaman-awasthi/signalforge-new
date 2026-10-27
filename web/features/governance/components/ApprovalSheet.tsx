"use client";

import { useState } from "react";

import { ApiError } from "@/lib/api/client";
import {
  Approval,
  Execution,
  approveExecution,
  denyExecution,
  runExecution,
} from "@/features/governance/api";
import { RuleChip } from "@/features/governance/components/Chips";
import {
  formatCountdown,
  useSecondsLeft,
} from "@/features/governance/hooks/useGovernance";
import { Badge } from "@/lib/ui/primitives";
import { Button, FactGrid } from "@/lib/ui/patterns";

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
 *
 * Two things changed for the cockpit (S10): the sheet is one strip, not a
 * document - the decision form opens on request - and once an approval is
 * granted the sheet offers **Send it** to incident command right here. An
 * approval is not an execution; the person who requested the action still has
 * to send it, and a granted approval that nobody noticed is one that expires.
 */
export function ApprovalSheet({
  incidentId,
  execution,
  approval,
  canApprove,
  canCommand = false,
  names = {},
  onChanged,
}: {
  incidentId: string;
  execution: Execution;
  approval: Approval | null;
  canApprove: boolean;
  /** May send a granted approval (declare_incident). */
  canCommand?: boolean;
  /** user id -> display name, from the room's members. */
  names?: Record<string, string>;
  onChanged: () => void;
}) {
  const [typed, setTyped] = useState("");
  const [note, setNote] = useState("");
  const [deciding, setDeciding] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const left = useSecondsLeft(approval?.expires_at ?? null);

  const granted = approval?.state === "granted" || execution.state === "approved";
  const confirmed = typed.trim() === execution.action_id;
  const requester = names[execution.requested_by] ?? execution.requested_by;
  const approver =
    approval?.approver_label ||
    (approval?.approver_user_id ? names[approval.approver_user_id] : "") ||
    approval?.approver_user_id ||
    "";

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
      setDeciding(false);
      onChanged();
    } catch (caught) {
      setError(
        caught instanceof ApiError ? caught.message : "The decision was refused",
      );
    } finally {
      setBusy(false);
    }
  }

  async function send() {
    setBusy(true);
    setError(null);
    try {
      await runExecution(incidentId, execution.execution_id);
      onChanged();
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "Could not send it");
    } finally {
      setBusy(false);
    }
  }

  return (
    <article
      aria-label={`Approval: ${execution.action_id}`}
      className={`flex flex-col gap-2 border-t-4 bg-surface-raised px-4 py-3 shadow-sm ${
        granted ? "border-t-ok" : "border-t-warn"
      }`}
    >
      <header className="flex flex-wrap items-center gap-2">
        <h3 className="font-display text-h5 font-extrabold leading-tight">
          {execution.action_id.replace(/[-_]/g, " ")}
        </h3>
        {execution.risk_class === "high_risk" && <Badge label="HIGH RISK" size="nano" />}
        {granted ? (
          <Badge label="APPROVED" size="nano" />
        ) : (
          <Badge label="WAITING FOR APPROVAL" size="nano" pulse />
        )}
        {execution.rules_hit.slice(0, 2).map((hit) => (
          <RuleChip key={hit.rule_id} ruleId={hit.rule_id} source={hit.source} detail={hit.detail} />
        ))}
        <span className="ml-auto flex items-baseline gap-2">
          {granted && approval?.expires_at && (
            <>
              <span
                className={`font-mono text-h5 font-semibold tabular-nums ${
                  left < 60 ? "text-accent-strong" : "text-warn-ink"
                }`}
                title="After this the approval is refused at execution, whether or not this tab is open."
              >
                {formatCountdown(left)}
              </span>
              <span className="text-tiny text-ink-muted">until it expires</span>
            </>
          )}
        </span>
      </header>

      <div className="grid gap-x-6 gap-y-1 text-dense sm:grid-cols-[1fr_1fr]">
        <span>
          <span className="text-nano uppercase tracking-label text-neutral-600">Requested by </span>
          {requester}
          <span className="text-ink-muted"> · {execution.requested_at.slice(11, 16)}</span>
        </span>
        <span>
          <span className="text-nano uppercase tracking-label text-neutral-600">May be approved by </span>
          {(approval?.required_roles ?? []).map((role) => role.replace(/_/g, " ")).join(" or ") ||
            "incident command"}
          <span className="text-ink-muted"> - not the requester</span>
        </span>
        {Object.keys(execution.args).length > 0 && (
          <span className="sm:col-span-2">
            <span className="text-nano uppercase tracking-label text-neutral-600">With </span>
            <span className="font-mono text-small">{JSON.stringify(execution.args)}</span>
          </span>
        )}
      </div>

      {error && (
        <p className="border border-accent bg-brand-100 px-2 py-1.5 text-small text-accent-strong">
          {error}
        </p>
      )}

      {granted ? (
        <div className="flex flex-wrap items-center gap-3 border-t border-line pt-2">
          <p className="text-small text-ok">
            Approved by {approver || "a person holding approve_high_risk"}
            {approval?.approver_roles?.length ? ` (${approval.approver_roles.join(", ")})` : ""}.
            It authorises this request only, and it has <strong>not been sent yet</strong>.
          </p>
          <span className="ml-auto">
            {canCommand ? (
              <Button variant="primary" size="small" disabled={busy} onClick={() => void send()}>
                {busy ? "Sending…" : "Send it now"}
              </Button>
            ) : (
              <span className="text-tiny text-ink-muted">incident command sends it</span>
            )}
          </span>
        </div>
      ) : !deciding ? (
        <div className="flex flex-wrap items-center gap-3 border-t border-line pt-2">
          <p className="text-small text-ink-muted">{execution.decision_reason}</p>
          <span className="ml-auto">
            {canApprove ? (
              <Button variant="primary" size="small" onClick={() => setDeciding(true)}>
                Review and decide
              </Button>
            ) : (
              <span className="text-tiny text-ink-muted">
                needs approve_high_risk, held by somebody other than the requester
              </span>
            )}
          </span>
        </div>
      ) : (
        <div className="flex flex-col gap-2 border-t border-line pt-2">
          <FactGrid
            labelWidth="120px"
            rows={[
              { label: "Why", value: execution.decision_reason },
              {
                label: "Under policy",
                value: `${execution.policy_ref} · floor ${execution.floor_version}`,
              },
            ]}
          />
          <label className="block text-small">
            <span className="text-ink-muted">
              Type <code className="font-mono">{execution.action_id}</code> to confirm you have
              read what you are approving
            </span>
            <input
              value={typed}
              onChange={(event) => setTyped(event.target.value)}
              className="mt-1 w-full border border-line bg-surface px-2 py-1 font-mono text-small"
              placeholder={execution.action_id}
            />
          </label>
          <label className="block text-small">
            <span className="text-ink-muted">Note (goes on the record)</span>
            <input
              value={note}
              onChange={(event) => setNote(event.target.value)}
              className="mt-1 w-full border border-line bg-surface px-2 py-1 text-small"
            />
          </label>
          <div className="flex flex-wrap items-center gap-2">
            <Button
              variant="primary"
              size="small"
              disabled={!confirmed || busy}
              onClick={() => void decide("approve")}
            >
              Approve
            </Button>
            <Button size="small" disabled={busy} onClick={() => void decide("deny")}>
              Deny
            </Button>
            <Button variant="ghost" size="small" onClick={() => setDeciding(false)}>
              Not now
            </Button>
          </div>
        </div>
      )}
    </article>
  );
}
