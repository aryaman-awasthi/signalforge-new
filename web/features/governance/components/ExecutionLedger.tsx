"use client";

import { useState } from "react";

import { ApiError } from "@/lib/api/client";
import {
  Approval,
  Execution,
  Ledger,
  cancelExecution,
  compensateExecution,
  reconcileExecution,
  retryExecution,
  runExecution,
  takeOverExecution,
} from "@/features/governance/api";
import { Chip, RiskChip, Tone } from "@/features/governance/components/Chips";

/**
 * S12 - the execution ledger.
 *
 * One row per attempt to change somebody's production estate, and the row is
 * the product. Two rules govern how it renders:
 *
 * **UNCERTAIN is visually obvious and its Retry control is not there.** Not
 * greyed - absent, replaced by Reconcile. A timeout means the target may have
 * applied the change after SignalForge stopped waiting, so a Retry button beside
 * it would be an offer to roll production back twice. The server refuses it
 * anyway (409), and this is the half of that control the operator can see.
 *
 * **Every terminal state says what it means.** `completed_late` is not a failure
 * and `not_applied` is not a success, and an operator reading the ledger at
 * minute forty should not have to remember which is which - so each state
 * carries its sentence.
 */

const STATE_TONE: Record<string, Tone> = {
  pending: "neutral",
  approval_required: "warn",
  approved: "warn",
  blocked: "danger",
  running: "accent",
  succeeded: "ok",
  failed: "danger",
  uncertain: "danger",
  reconciling: "warn",
  completed_late: "ok",
  not_applied: "neutral",
  partial: "danger",
  compensating: "warn",
  compensated: "ok",
  cancelled: "neutral",
  taken_over: "warn",
};

const STATE_MEANING: Record<string, string> = {
  pending: "decided, not yet sent",
  approval_required: "waiting for a person",
  approved: "authorised, not yet sent",
  blocked: "refused before anything was sent",
  running: "sent, waiting for the target",
  succeeded: "the target confirmed it",
  failed: "the target rejected it; nothing was applied",
  uncertain:
    "we stopped waiting and the target may or may not have applied it. Reconcile before doing anything else.",
  reconciling: "asking the target what actually happened",
  completed_late: "it did land, after we stopped waiting. Do not repeat it.",
  not_applied: "it never landed. A fresh attempt is safe.",
  partial: "some of it landed. Compensate, continue, or take over.",
  compensating: "undoing it through the declared reverse action",
  compensated: "undone",
  cancelled: "abandoned before it was sent",
  taken_over: "a person is finishing this by hand",
};

export function ExecutionLedger({
  incidentId,
  ledger,
  canCommand,
  onChanged,
}: {
  incidentId: string;
  ledger: Ledger;
  canCommand: boolean;
  onChanged: () => void;
}) {
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  if (ledger.executions.length === 0) {
    return (
      <p className="text-xs text-ink-muted">
        Nothing has been executed against this application during this incident.
      </p>
    );
  }

  async function run(
    executionId: string,
    what: string,
    call: () => Promise<unknown>,
  ) {
    setBusy(`${executionId}:${what}`);
    setError(null);
    try {
      await call();
      onChanged();
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "Refused");
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="space-y-2">
      {error && (
        <p className="rounded-card border border-danger/40 bg-danger/10 px-2 py-1.5 text-xs text-danger">
          {error}
        </p>
      )}
      <ul className="space-y-2">
        {ledger.executions.map((execution) => (
          <Row
            key={execution.execution_id}
            execution={execution}
            approval={
              ledger.approvals.find(
                (a) => a.approval_id === execution.approval_id,
              ) ?? null
            }
            canCommand={canCommand}
            busy={busy}
            onRun={(what, call) => void run(execution.execution_id, what, call)}
            incidentId={incidentId}
          />
        ))}
      </ul>
    </div>
  );
}

function Row({
  incidentId,
  execution,
  approval,
  canCommand,
  busy,
  onRun,
}: {
  incidentId: string;
  execution: Execution;
  approval: Approval | null;
  canCommand: boolean;
  busy: string | null;
  onRun: (what: string, call: () => Promise<unknown>) => void;
}) {
  const uncertain = execution.state === "uncertain";
  const id = execution.execution_id;
  const working = (what: string) => busy === `${id}:${what}`;

  return (
    <li
      className={`rounded-card border px-2.5 py-2 ${
        uncertain
          ? "border-danger bg-danger/10"
          : execution.state === "partial"
            ? "border-danger/50 bg-danger/5"
            : "border-line"
      }`}
    >
      <div className="flex flex-wrap items-center gap-2 text-xs">
        <Chip tone={STATE_TONE[execution.state] ?? "neutral"}>
          {execution.state.replace(/_/g, " ")}
        </Chip>
        <span className="font-medium">{execution.action_id}</span>
        <RiskChip riskClass={execution.risk_class} />
        {execution.attempt > 1 && <Chip>attempt {execution.attempt}</Chip>}
        {execution.reconcile_outcome && (
          <Chip tone="accent">
            reconciled: {execution.reconcile_outcome.replace(/_/g, " ")}
          </Chip>
        )}
        {execution.compensates_execution_id && <Chip>compensation</Chip>}
        <span className="flex-1" />
        <span className="font-mono text-[10px] text-ink-muted">
          {execution.requested_at.slice(11, 16)}
        </span>
      </div>

      <p className="mt-1 text-[11px] text-ink-muted">
        {STATE_MEANING[execution.state] ?? execution.decision_reason}
      </p>
      {execution.error && (
        <p className="mt-0.5 font-mono text-[10px] text-ink-muted">
          {execution.error}
        </p>
      )}
      {execution.takeover_note && (
        <p className="mt-0.5 text-[11px]">
          <span className="font-medium">Taken over: </span>
          {execution.takeover_note}
        </p>
      )}
      {approval && (
        <p className="mt-0.5 text-[11px] text-ink-muted">
          Approved by {approval.approver_label || approval.approver_user_id} ·{" "}
          {approval.state}
        </p>
      )}

      <div className="mt-1.5 flex flex-wrap gap-1.5">
        {execution.state === "approved" && (
          <Control
            label="Send it"
            busy={working("run")}
            disabled={!canCommand}
            onClick={() => onRun("run", () => runExecution(incidentId, id))}
          />
        )}

        {/* The control the whole sprint is about. While the outcome is unknown
            there is no Retry here at all - only Reconcile, which asks the target
            what actually happened. */}
        {(uncertain || execution.state === "partial") && (
          <>
            <Control
              label="Reconcile"
              tone="danger"
              busy={working("reconcile")}
              disabled={!canCommand}
              onClick={() =>
                onRun("reconcile", () => reconcileExecution(incidentId, id))
              }
            />
            <Control
              label="I did it by hand"
              busy={working("takeover")}
              disabled={!canCommand}
              onClick={() => {
                // Cancel means cancel. `null` is the person closing the dialog,
                // and recording "handled manually" for them would put a
                // take-over nobody performed on the ledger.
                const note = window.prompt("What did you do?");
                if (note === null) return;
                onRun("takeover", () =>
                  takeOverExecution(incidentId, id, note.trim() || "handled manually"),
                );
              }}
            />
          </>
        )}

        {execution.state === "not_applied" && !execution.retry_blocked && (
          <Control
            label="Retry"
            busy={working("retry")}
            disabled={!canCommand}
            onClick={() => onRun("retry", () => retryExecution(incidentId, id))}
          />
        )}

        {["succeeded", "completed_late", "partial"].includes(execution.state) && (
          <Control
            label="Compensate"
            busy={working("compensate")}
            disabled={!canCommand}
            onClick={() =>
              onRun("compensate", () =>
                compensateExecution(incidentId, id, "undoing"),
              )
            }
          />
        )}

        {["pending", "approval_required", "approved"].includes(
          execution.state,
        ) && (
          <Control
            label="Cancel"
            busy={working("cancel")}
            disabled={!canCommand}
            onClick={() =>
              onRun("cancel", () => cancelExecution(incidentId, id, "cancelled"))
            }
          />
        )}
      </div>

      {uncertain && (
        <p className="mt-1.5 rounded-card border border-danger/40 px-2 py-1 text-[11px] text-danger">
          Retry is unavailable until this is reconciled. The target may have
          applied it after we stopped waiting, and repeating it would apply it
          twice.
        </p>
      )}
    </li>
  );
}

function Control({
  label,
  tone = "neutral",
  busy,
  disabled,
  onClick,
}: {
  label: string;
  tone?: Tone;
  busy: boolean;
  disabled: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      disabled={disabled || busy}
      onClick={onClick}
      className={`rounded-card border px-2 py-0.5 text-[11px] disabled:opacity-50 ${
        tone === "danger"
          ? "border-danger text-danger hover:bg-danger/10"
          : "border-line hover:border-accent"
      }`}
    >
      {busy ? "…" : label}
    </button>
  );
}
