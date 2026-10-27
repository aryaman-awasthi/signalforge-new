"use client";

import { useState } from "react";

import { ApiError } from "@/lib/api/client";
import {
  Candidate,
  Execution,
  Ledger,
  Nba,
  requestExecution,
  runExecution,
} from "@/features/governance/api";
import {
  Chip,
  OutcomeChip,
  ProvenanceChip,
  RiskChip,
  RuleChip,
  ScoreBar,
} from "@/features/governance/components/Chips";
import { Badge } from "@/lib/ui/primitives";
import { Button } from "@/lib/ui/patterns";

/**
 * S11 - Next Best Action.
 *
 * The screen shows the arithmetic. `score` is
 * `urgency x impact x information gain - risk`, computed on the server from
 * stored facts, and all four terms are rendered beside it - because an operator
 * who can see why a rollback outranks a restart can disagree with it, and one
 * who is shown only a rank cannot.
 *
 * **There is no auto-execute control on this card, for any action.** Not a
 * disabled one, not one behind a confirmation: a high-risk action's only path is
 * Request approval, and the backend refuses `intent: auto` on the floor whether
 * or not any client offers it. The absence is the design.
 *
 * An ineligible action is listed with its reason and cannot be requested. The
 * alternative - hiding it - is how fifteen minutes get spent at 03:00
 * discovering that the obvious action was never possible.
 *
 * `compact` is the cockpit (S9): the prototype's card - the action in a line,
 * the rationale under it, the safety verdict, one button - with the arithmetic
 * and the policy facts behind "Plan and risk detail". The card also says where
 * the action *is*: requested, waiting for whom, approved and ready to send,
 * done, refused. Before this the only place that was visible was the ledger
 * three panels down, and "I pressed approve and nothing happened" was the
 * sentence it produced.
 */
export function NextBestAction({
  incidentId,
  nba,
  canCommand,
  onChanged,
  compact = false,
  ledger = null,
  names = {},
}: {
  incidentId: string;
  nba: Nba | null;
  canCommand: boolean;
  onChanged: () => void;
  compact?: boolean;
  /** The execution ledger, so the card can say where the primary action is. */
  ledger?: Ledger | null;
  /** user id -> display name, from the room's members. */
  names?: Record<string, string>;
}) {
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  /* Arguments the application says the action needs, keyed by action then by
     property. Asked for rather than guessed: a screen that invented a region
     would be choosing which half of an estate to act on. */
  const [args, setArgs] = useState<Record<string, Record<string, string>>>({});

  if (!nba) {
    return (
      <p className="text-xs text-ink-muted">
        No ranked actions yet. Actions come from the application&apos;s own
        manifest, so an application with none published has none to offer.
      </p>
    );
  }

  async function request(candidate: Candidate) {
    setBusy(candidate.action_id);
    setError(null);
    setNotice(null);
    try {
      const result = await requestExecution(incidentId, {
        action_id: candidate.action_id,
        args: coerce(candidate.params_schema, args[candidate.action_id] ?? {}),
        note: candidate.rationale.slice(0, 200),
      });
      if (result.duplicate) {
        // Nothing was sent. Said out loud, because a button that does nothing
        // silently is how the same request gets pressed four times.
        setNotice(
          `This exact request is already on the ledger as an attempt that is ${result.execution.state.replace(/_/g, " ")}. Nothing was sent again; follow that attempt below.`,
        );
      }
      onChanged();
    } catch (caught) {
      setError(
        caught instanceof ApiError ? caught.message : "The request was refused",
      );
    } finally {
      setBusy(null);
    }
  }

  async function send(execution: Execution) {
    setBusy(`send:${execution.execution_id}`);
    setError(null);
    try {
      await runExecution(incidentId, execution.execution_id);
      onChanged();
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "Could not send it");
    } finally {
      setBusy(null);
    }
  }

  const argsFor = (candidate: Candidate) => args[candidate.action_id] ?? {};
  const onArgFor = (candidate: Candidate) => (name: string, value: string) =>
    setArgs((current) => ({
      ...current,
      [candidate.action_id]: {
        ...(current[candidate.action_id] ?? {}),
        [name]: value,
      },
    }));

  const others = [...nba.alternatives, ...nba.ineligible];

  return (
    <div className="space-y-3">
      {error && (
        <p className="border border-accent bg-brand-100 px-2 py-1.5 text-xs text-accent-strong">
          {error}
        </p>
      )}
      {notice && (
        <p className="border border-warn bg-warn-bg px-2 py-1.5 text-xs text-warn-ink">
          {notice}
        </p>
      )}

      {nba.primary ? (
        <Primary
          candidate={nba.primary}
          canCommand={canCommand}
          busy={busy === nba.primary.action_id}
          onRequest={() => void request(nba.primary as Candidate)}
          args={argsFor(nba.primary)}
          onArg={onArgFor(nba.primary)}
          compact={compact}
          latest={latestFor(ledger, nba.primary.action_id)}
          sending={busy?.startsWith("send:") ?? false}
          onSend={(execution) => void send(execution)}
          names={names}
        />
      ) : (
        <p className="text-xs text-ink-muted">
          Nothing is currently performable. Every published action is blocked by
          policy, missing a prerequisite, or has nobody who could approve it -
          the rows below say which.
        </p>
      )}

      {!compact && nba.fallback && (
        <div className="rounded-card border border-line px-2.5 py-2">
          <h4 className="text-[10px] font-semibold uppercase tracking-wide text-ink-muted">
            If you are not ready for that
          </h4>
          <p className="mt-1 text-xs">
            <span className="font-medium">{nba.fallback.name}</span>{" "}
            <RiskChip riskClass={nba.fallback.risk_class} /> — lower risk,{" "}
            {nba.fallback.score >= 0 ? "+" : ""}
            {nba.fallback.score.toFixed(2)}
          </p>
        </div>
      )}

      {others.length > 0 &&
        (compact ? (
          <details className="border-t border-line pt-2">
            <summary className="cursor-pointer text-small font-semibold text-accent hover:underline">
              Other options ({others.length})
              {nba.fallback ? ` · lower-risk fallback: ${nba.fallback.name}` : ""}
            </summary>
            <ul className="mt-2 space-y-1">
              {others.map((candidate) => (
                <Row
                  key={candidate.action_id}
                  candidate={candidate}
                  canCommand={canCommand}
                  busy={busy === candidate.action_id}
                  onRequest={() => void request(candidate)}
                  args={argsFor(candidate)}
                  onArg={onArgFor(candidate)}
                  latest={latestFor(ledger, candidate.action_id)}
                  names={names}
                />
              ))}
            </ul>
          </details>
        ) : (
          <div className="space-y-1.5">
            <h4 className="text-[10px] font-semibold uppercase tracking-wide text-ink-muted">
              Alternatives
            </h4>
            <ul className="space-y-1">
              {others.map((candidate) => (
                <Row
                  key={candidate.action_id}
                  candidate={candidate}
                  canCommand={canCommand}
                  busy={busy === candidate.action_id}
                  onRequest={() => void request(candidate)}
                  args={argsFor(candidate)}
                  onArg={onArgFor(candidate)}
                  latest={latestFor(ledger, candidate.action_id)}
                  names={names}
                />
              ))}
            </ul>
          </div>
        ))}
    </div>
  );
}

/** The most recent ledger row for an action, or null. */
function latestFor(ledger: Ledger | null, actionId: string): Execution | null {
  if (!ledger) return null;
  const rows = ledger.executions
    .filter((execution) => execution.action_id === actionId)
    .sort((a, b) => b.requested_at.localeCompare(a.requested_at));
  return rows[0] ?? null;
}

/** A form field is a string; the application's schema says what the value is.
 *
 * Coerced here rather than left to the Tool Gateway to be lenient about, because
 * the gateway validating strictly is the point: an integer parameter that
 * accepted "1" would be a validator that had stopped checking. The screen types
 * what it collected and the gateway still checks it. */
function coerce(
  schema: Candidate["params_schema"],
  values: Record<string, string>,
): Record<string, unknown> {
  const properties = schema?.properties ?? {};
  const out: Record<string, unknown> = {};
  for (const [name, raw] of Object.entries(values)) {
    if (raw === "") continue;
    const type = properties[name]?.type;
    if (type === "integer") out[name] = Number.parseInt(raw, 10);
    else if (type === "number") out[name] = Number(raw);
    else if (type === "boolean") out[name] = raw === "true";
    else out[name] = raw;
  }
  return out;
}

/**
 * What the application says this action needs before it can be run.
 *
 * Rendered from the manifest's own JSON schema, so a new action with a new
 * parameter gets a field here with no cockpit change. Only the *required*
 * properties are asked for: an optional argument left out is the application's
 * default, which is the application's decision rather than this screen's.
 */
function ActionArgs({
  schema,
  values,
  onChange,
}: {
  schema: Candidate["params_schema"];
  values: Record<string, string>;
  onChange: (name: string, value: string) => void;
}) {
  const required = schema?.required ?? [];
  if (required.length === 0) return null;
  const properties = schema?.properties ?? {};
  return (
    <div className="mt-2 flex flex-wrap gap-3 border-t border-line pt-2">
      {required.map((name) => {
        const spec = properties[name] ?? {};
        const options = spec.enum ?? [];
        return (
          <label key={name} className="flex flex-col gap-1 text-tiny">
            <span className="uppercase tracking-label text-neutral-600">
              {name}
              {spec.description ? ` — ${spec.description}` : ""}
            </span>
            {options.length > 0 ? (
              <select
                value={values[name] ?? ""}
                onChange={(event) => onChange(name, event.target.value)}
                className="min-h-8 border border-line bg-surface px-2 py-1 text-small"
              >
                <option value="">choose…</option>
                {options.map((option) => (
                  <option key={option} value={option}>
                    {option}
                  </option>
                ))}
              </select>
            ) : (
              <input
                value={values[name] ?? ""}
                onChange={(event) => onChange(name, event.target.value)}
                className="min-h-8 border border-line bg-surface px-2 py-1 text-small"
                placeholder={spec.type ?? "value"}
              />
            )}
          </label>
        );
      })}
    </div>
  );
}

/** The safety verdict in the platform's own words, from the policy decision. */
function verdict(candidate: Candidate): { text: string; tone: string } {
  const outcome = candidate.policy.outcome;
  if (outcome === "block") {
    return { text: `BLOCKED — ${candidate.policy.reason}`, tone: "text-accent-strong" };
  }
  if (outcome === "approval_required" || candidate.requires_approval) {
    const who = candidate.approver_roles.map((role) => role.replace(/_/g, " ")).join(" or ");
    return {
      text: `ALLOW WITH APPROVAL — a ${who || "person holding approve_high_risk"} must approve it; the requester cannot approve their own request`,
      tone: "text-accent-strong",
    };
  }
  return { text: "ALLOW — no approval needed; it runs when requested", tone: "text-ok" };
}

/**
 * Where the most recent attempt at this action stands, as a sentence and, when
 * there is one, the control that moves it on. Every state the ledger can be in
 * is spelled out: an action that is "approved" and sitting there is the one a
 * commander most needs to see.
 */
function Progress({
  latest,
  canCommand,
  sending,
  onSend,
  names,
}: {
  latest: Execution | null;
  canCommand: boolean;
  sending: boolean;
  onSend: (execution: Execution) => void;
  names: Record<string, string>;
}) {
  if (!latest) return null;
  const who = names[latest.requested_by] ?? latest.requested_by;
  const at = latest.requested_at.slice(11, 16);
  const state = latest.state;
  const line = (tone: string, text: string, control?: React.ReactNode) => (
    <div className={`flex flex-wrap items-center gap-2 border-t border-line pt-2 text-small ${tone}`}>
      <span className="font-mono text-tiny text-ink-muted">{at}</span>
      <span>{text}</span>
      {control && <span className="ml-auto">{control}</span>}
    </div>
  );
  switch (state) {
    case "approval_required":
      return line("text-warn-ink", `Requested by ${who}. Waiting for an approver - the approval sheet is at the top of the room.`);
    case "approved":
      return line(
        "text-ok",
        "Approved. It has not been sent yet.",
        canCommand ? (
          <Button variant="primary" size="small" disabled={sending} onClick={() => onSend(latest)}>
            {sending ? "Sending…" : "Send it now"}
          </Button>
        ) : (
          <span className="text-tiny text-ink-muted">incident command sends it</span>
        ),
      );
    case "pending":
    case "running":
      return line("text-info", "Sent to the application; waiting for it to answer.");
    case "succeeded":
    case "completed_late":
      return line("text-ok", `Done - the application confirmed it${latest.attempt > 1 ? ` (attempt ${latest.attempt})` : ""}. Request it again if it has to be done once more.`);
    case "failed":
      return line("text-accent-strong", `The application rejected it: ${latest.error || "no detail"}. A new request is a fresh attempt.`);
    case "uncertain":
    case "partial":
    case "reconciling":
      return line("text-accent-strong", "Outcome unknown - reconcile it in the Execution Ledger before anything is retried.");
    case "blocked":
      return line("text-ink-muted", `Refused: ${latest.decision_reason || "blocked by policy"}.`);
    case "cancelled":
      return line("text-ink-muted", "The last request was cancelled before it was sent.");
    default:
      return line("text-ink-muted", `Last attempt: ${state.replace(/_/g, " ")}.`);
  }
}

function Primary({
  candidate,
  canCommand,
  busy,
  onRequest,
  args,
  onArg,
  compact,
  latest,
  sending,
  onSend,
  names,
}: {
  candidate: Candidate;
  canCommand: boolean;
  busy: boolean;
  onRequest: () => void;
  args: Record<string, string>;
  onArg: (name: string, value: string) => void;
  compact: boolean;
  latest: Execution | null;
  sending: boolean;
  onSend: (execution: Execution) => void;
  names: Record<string, string>;
}) {
  const safety = verdict(candidate);
  // While an attempt is open the button would only produce a duplicate; the
  // progress line says what to do instead.
  const open =
    latest !== null &&
    ["pending", "approval_required", "approved", "running", "uncertain", "reconciling", "partial", "compensating"].includes(latest.state);
  const again = latest !== null && !open;

  const facts = (
    <>
      <div className="mt-2.5 space-y-1">
        <ScoreBar label="urgency" value={candidate.urgency} />
        <ScoreBar label="impact" value={candidate.impact} />
        <ScoreBar label="information" value={candidate.information_gain} />
        <ScoreBar label="risk" value={candidate.risk} negative />
      </div>

      <dl className="mt-2.5 grid gap-1 text-[11px] sm:grid-cols-2">
        <div>
          <dt className="inline font-medium">Policy: </dt>
          <dd className="inline text-ink-muted">{candidate.policy.reason}</dd>
        </div>
        <div>
          <dt className="inline font-medium">Approver: </dt>
          <dd className="inline text-ink-muted">
            {candidate.approver_roles.join(", ") || "not required"}
            {candidate.policy.approval_ttl_s > 0 &&
              ` · expires ${Math.round(candidate.policy.approval_ttl_s / 60)} min after granting`}
          </dd>
        </div>
        <div>
          <dt className="inline font-medium">Validated in: </dt>
          <dd className="inline text-ink-muted">
            about {Math.round(candidate.expected_validation_s / 60)} min, once
            the stability window has held
          </dd>
        </div>
        <div>
          <dt className="inline font-medium">If it goes wrong: </dt>
          <dd className="inline text-ink-muted">{candidate.rollback_summary}</dd>
        </div>
      </dl>

      <div className="mt-2 flex flex-wrap gap-1">
        {candidate.policy.rules_hit.map((hit) => (
          <RuleChip
            key={hit.rule_id}
            ruleId={hit.rule_id}
            source={hit.source}
            detail={hit.detail}
          />
        ))}
      </div>
    </>
  );

  return (
    <article className={compact ? "" : "rounded-card border border-accent/40 bg-accent/5 px-3 py-2.5"}>
      <header className="flex flex-wrap items-center gap-2">
        <h3 className={compact ? "font-display text-h5 font-extrabold leading-tight" : "text-sm font-semibold"}>
          {candidate.name}
        </h3>
        <span className="ml-auto flex flex-wrap items-center gap-1.5">
          {candidate.risk_class === "high_risk" ? (
            <Badge label="HIGH RISK" size="nano" />
          ) : (
            <RiskChip riskClass={candidate.risk_class} />
          )}
          {!compact && <OutcomeChip outcome={candidate.policy.outcome} />}
          {!compact && (
            <span className="font-mono text-sm font-semibold">
              {candidate.score >= 0 ? "+" : ""}
              {candidate.score.toFixed(2)}
            </span>
          )}
        </span>
      </header>

      <p className={`mt-1.5 ${compact ? "text-small" : "text-xs text-ink-muted"}`}>
        {candidate.rationale}
      </p>
      <div className="mt-1.5 flex flex-wrap gap-1">
        <ProvenanceChip degraded={candidate.rationale_degraded} />
        {candidate.hypothesis_label && (
          <Chip title={candidate.hypothesis_key}>
            addresses: {candidate.hypothesis_label} (
            {Math.round(candidate.hypothesis_confidence * 100)}%)
          </Chip>
        )}
        {/* The causal verdict, in the platform's own words. "Not causally
            supported" is the chip an incident commander most needs to see on a
            rollback that happens to be the most powerful action in the table. */}
        {candidate.root_cause && candidate.causal_reason && (
          <Chip
            tone={candidate.causally_supported === false ? "warn" : "ok"}
            title={`root cause ${candidate.root_cause}${
              candidate.causal_mechanism ? ` via ${candidate.causal_mechanism}` : ""
            }`}
          >
            {candidate.causally_supported === false
              ? "not causally supported"
              : "causally supported"}
            : {candidate.causal_reason}
          </Chip>
        )}
      </div>

      {compact ? (
        <details className="mt-2">
          <summary className="cursor-pointer text-small font-semibold text-accent hover:underline">
            Plan and risk detail
            <span className="ml-1 font-mono text-tiny font-normal text-ink-muted">
              score {candidate.score >= 0 ? "+" : ""}
              {candidate.score.toFixed(2)}
            </span>
          </summary>
          {facts}
        </details>
      ) : (
        facts
      )}

      <ActionArgs
        schema={candidate.params_schema}
        values={args}
        onChange={onArg}
      />

      <p className={`mt-2.5 text-small font-bold ${safety.tone}`}>
        <span className="text-nano font-bold uppercase tracking-label text-neutral-600">
          Safety verdict
        </span>{" "}
        {safety.text}
      </p>

      <Progress latest={latest} canCommand={canCommand} sending={sending} onSend={onSend} names={names} />

      {/* One control, and it is the only one. A high-risk action's path is
          Request approval; there is no auto-execute affordance on this card for
          any action, at any risk class. */}
      <div className="mt-2.5 flex flex-wrap items-center gap-2">
        <Button
          variant="primary"
          disabled={!canCommand || !candidate.eligible || busy || open}
          onClick={onRequest}
        >
          {busy
            ? "Requesting…"
            : candidate.requires_approval
              ? again
                ? "Request approval again"
                : "Request approval"
              : again
                ? "Run it again"
                : "Run this action"}
        </Button>
        {!canCommand && (
          <span className="text-[11px] text-ink-muted">
            Incident command requests actions.
          </span>
        )}
        {!candidate.eligible && (
          <span className="text-[11px] text-warn-ink">
            {candidate.eligibility_reason}
          </span>
        )}
      </div>
    </article>
  );
}

function Row({
  candidate,
  canCommand,
  busy,
  onRequest,
  args,
  onArg,
  latest,
  names,
}: {
  candidate: Candidate;
  canCommand: boolean;
  busy: boolean;
  onRequest: () => void;
  args: Record<string, string>;
  onArg: (name: string, value: string) => void;
  latest: Execution | null;
  names: Record<string, string>;
}) {
  const open =
    latest !== null &&
    ["pending", "approval_required", "approved", "running", "uncertain", "reconciling", "partial", "compensating"].includes(latest.state);
  return (
    <li className="flex flex-wrap items-center gap-2 border border-line px-2 py-1.5 text-xs">
      <span className="font-mono text-[11px] text-ink-muted">
        {candidate.score >= 0 ? "+" : ""}
        {candidate.score.toFixed(2)}
      </span>
      <span className={candidate.eligible ? "font-medium" : "text-ink-muted"}>
        {candidate.name}
      </span>
      <RiskChip riskClass={candidate.risk_class} />
      <OutcomeChip outcome={candidate.policy.outcome} />
      {candidate.root_cause && candidate.causal_reason && (
        <Chip tone={candidate.causally_supported === false ? "warn" : "ok"} title={candidate.causal_reason}>
          {candidate.causally_supported === false ? "not causally supported" : "causally supported"}
        </Chip>
      )}
      {!candidate.eligible && (
        <span className="text-[11px] text-warn-ink">
          {candidate.eligibility_reason}
        </span>
      )}
      {latest && (
        <span className="text-[11px] text-ink-muted">
          last: {latest.state.replace(/_/g, " ")} ·{" "}
          {names[latest.requested_by] ?? latest.requested_by}
        </span>
      )}
      <span className="flex-1" />
      {candidate.eligible && (
        <button
          type="button"
          disabled={!canCommand || busy || open}
          onClick={onRequest}
          className="rounded-card border border-line px-2 py-0.5 text-[11px] hover:border-accent disabled:opacity-50"
        >
          {candidate.requires_approval ? "Request" : "Run"}
        </button>
      )}
      {candidate.eligible && (
        <div className="w-full">
          <ActionArgs
            schema={candidate.params_schema}
            values={args}
            onChange={onArg}
          />
        </div>
      )}
    </li>
  );
}
