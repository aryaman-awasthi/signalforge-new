"use client";

import { useState } from "react";

import { ApiError } from "@/lib/api/client";
import {
  Comms,
  Communication,
  approveCommunication,
  draftCommunication,
  draftCorrection,
  issueCommunication,
} from "@/features/governance/api";
import {
  Chip,
  ProvenanceChip,
  Tone,
} from "@/features/governance/components/Chips";

/**
 * S15 - governed communications.
 *
 * There is no Send on this panel and there is no schedule. A communication
 * reaches an audience because a named person with that audience's approval
 * permission pressed Issue, and the card shows who and when.
 *
 * The two states worth their colour:
 *
 * - **contradicted** is danger, because a statement that has already gone out
 *   and is no longer supported by the evidence is the most expensive thing on
 *   this screen. The text is *not* edited - it is what was published - and the
 *   card offers a correction instead.
 * - **corrected** is neutral: the record is now complete, with the redline on
 *   the correction saying what changed and why.
 */

const STATE: Record<string, { tone: Tone; label: string }> = {
  draft: { tone: "neutral", label: "draft" },
  approved: { tone: "warn", label: "approved, not issued" },
  issued: { tone: "ok", label: "issued" },
  contradicted: { tone: "danger", label: "contradicted" },
  corrected: { tone: "neutral", label: "corrected" },
  withdrawn: { tone: "neutral", label: "withdrawn" },
};

const AUDIENCES = [
  { value: "responders", label: "Responders" },
  { value: "leadership", label: "Leadership" },
  { value: "customer_support", label: "Support" },
  { value: "customer", label: "Customers" },
];

export function CommsPanel({
  incidentId,
  comms,
  permissions,
  onChanged,
}: {
  incidentId: string;
  comms: Comms | null;
  permissions: string[];
  onChanged: () => void;
}) {
  const [audience, setAudience] = useState("responders");
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function run(key: string, call: () => Promise<unknown>) {
    setBusy(key);
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

  const rows = comms?.communications ?? [];

  return (
    <div className="space-y-2">
      {error && (
        <p className="rounded-card border border-danger/40 bg-danger/10 px-2 py-1.5 text-xs text-danger">
          {error}
        </p>
      )}

      <div className="flex flex-wrap items-center gap-2">
        <select
          value={audience}
          onChange={(event) => setAudience(event.target.value)}
          className="rounded-card border border-line bg-surface px-2 py-1 text-xs"
        >
          {AUDIENCES.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </select>
        <button
          type="button"
          disabled={busy === "draft"}
          onClick={() =>
            void run("draft", () =>
              draftCommunication(incidentId, { audience }),
            )
          }
          className="rounded-card border border-line px-2.5 py-1 text-xs hover:border-accent disabled:opacity-50"
        >
          Draft from the record
        </button>
        <span className="text-[11px] text-ink-muted">
          Drafts cite incident-brain nodes. Nothing sends itself.
        </span>
      </div>

      {rows.length === 0 ? (
        <p className="text-xs text-ink-muted">
          Nothing has been drafted for this incident yet.
        </p>
      ) : (
        <ul className="space-y-2">
          {rows.map((communication) => (
            <Card
              key={communication.communication_id}
              incidentId={incidentId}
              communication={communication}
              permissions={permissions}
              busy={busy}
              onRun={run}
            />
          ))}
        </ul>
      )}
    </div>
  );
}

function Card({
  incidentId,
  communication,
  permissions,
  busy,
  onRun,
}: {
  incidentId: string;
  communication: Communication;
  permissions: string[];
  busy: string | null;
  onRun: (key: string, call: () => Promise<unknown>) => void;
}) {
  const id = communication.communication_id;
  const style = STATE[communication.state] ?? STATE.draft;
  const mayApprove =
    communication.requires_permission === null ||
    permissions.includes(communication.requires_permission);

  return (
    <li
      className={`rounded-card border px-2.5 py-2 ${
        communication.state === "contradicted"
          ? "border-danger bg-danger/10"
          : "border-line"
      }`}
    >
      <div className="flex flex-wrap items-center gap-2 text-xs">
        <Chip tone={style.tone}>{style.label}</Chip>
        <Chip>{communication.audience.replace("_", " ")}</Chip>
        <span className="font-medium">{communication.subject}</span>
        <span className="flex-1" />
        <ProvenanceChip degraded={communication.degraded} />
      </div>

      {communication.state === "contradicted" && (
        <p className="mt-1.5 rounded-card border border-danger/40 px-2 py-1 text-[11px] text-danger">
          This was issued and the evidence under it has moved:{" "}
          {communication.contradiction_reason}. The text above is what was
          published and has not been changed - correct it with a new statement.
        </p>
      )}

      <pre className="mt-1.5 whitespace-pre-wrap font-sans text-[11px] text-ink-muted">
        {communication.body}
      </pre>

      {communication.redline.length > 0 && (
        <div className="mt-1.5 space-y-1">
          <h4 className="text-[10px] font-semibold uppercase tracking-wide text-ink-muted">
            What changed
          </h4>
          {communication.redline.map((change, index) => (
            <div
              key={`${id}-redline-${index}`}
              className="rounded-card border border-line px-2 py-1 text-[11px]"
            >
              <p className="text-danger line-through">{change.old}</p>
              <p className="text-ok">{change.new}</p>
              {change.reason && (
                <p className="text-ink-muted">— {change.reason}</p>
              )}
            </div>
          ))}
        </div>
      )}

      <div className="mt-1.5 flex flex-wrap items-center gap-1">
        {communication.grounded_node_ids.slice(0, 6).map((node) => (
          <Chip key={node} title={node}>
            {node.slice(0, 8)}
          </Chip>
        ))}
        {communication.grounded_node_ids.length > 6 && (
          <Chip>+{communication.grounded_node_ids.length - 6} more</Chip>
        )}
      </div>

      {communication.issued_at && (
        <p className="mt-1 text-[11px] text-ink-muted">
          Issued by {communication.issued_by} at{" "}
          {communication.issued_at.slice(11, 16)}
          {communication.approved_by_label &&
            ` · approved by ${communication.approved_by_label}`}
        </p>
      )}

      <div className="mt-1.5 flex flex-wrap gap-1.5">
        {communication.state === "draft" && (
          <button
            type="button"
            disabled={!mayApprove || busy === `${id}:approve`}
            onClick={() =>
              onRun(`${id}:approve`, () => approveCommunication(incidentId, id))
            }
            className="rounded-card border border-line px-2 py-0.5 text-[11px] hover:border-accent disabled:opacity-50"
          >
            Approve
          </button>
        )}
        {communication.state === "approved" && (
          <button
            type="button"
            disabled={!mayApprove || busy === `${id}:issue`}
            onClick={() =>
              onRun(`${id}:issue`, () => issueCommunication(incidentId, id))
            }
            className="rounded-card bg-accent px-2.5 py-0.5 text-[11px] font-medium text-surface-raised hover:bg-accent-strong disabled:opacity-50"
          >
            Issue
          </button>
        )}
        {(communication.state === "contradicted" ||
          communication.state === "issued") &&
          !communication.corrected_by_id && (
            <button
              type="button"
              disabled={busy === `${id}:correction`}
              onClick={() =>
                onRun(`${id}:correction`, () =>
                  draftCorrection(incidentId, id, {
                    reason: communication.contradiction_reason,
                  }),
                )
              }
              className="rounded-card border border-line px-2 py-0.5 text-[11px] hover:border-accent disabled:opacity-50"
            >
              Draft a correction
            </button>
          )}
        {!mayApprove && communication.requires_permission && (
          <span className="text-[11px] text-ink-muted">
            {communication.requires_permission} approves this audience.
          </span>
        )}
      </div>
    </li>
  );
}
