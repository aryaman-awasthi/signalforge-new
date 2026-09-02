"use client";

import { useState } from "react";

import { ApiError } from "@/lib/api/client";
import {
  Candidate,
  Nba,
  requestExecution,
} from "@/features/governance/api";
import {
  Chip,
  OutcomeChip,
  ProvenanceChip,
  RiskChip,
  RuleChip,
  ScoreBar,
} from "@/features/governance/components/Chips";

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
 */
export function NextBestAction({
  incidentId,
  nba,
  canCommand,
  onChanged,
}: {
  incidentId: string;
  nba: Nba | null;
  canCommand: boolean;
  onChanged: () => void;
}) {
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
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
    try {
      await requestExecution(incidentId, {
        action_id: candidate.action_id,
        args: coerce(candidate.params_schema, args[candidate.action_id] ?? {}),
        note: candidate.rationale.slice(0, 200),
      });
      onChanged();
    } catch (caught) {
      setError(
        caught instanceof ApiError ? caught.message : "The request was refused",
      );
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="space-y-3">
      {error && (
        <p className="rounded-card border border-danger/40 bg-danger/10 px-2 py-1.5 text-xs text-danger">
          {error}
        </p>
      )}

      {nba.primary ? (
        <Primary
          candidate={nba.primary}
          canCommand={canCommand}
          busy={busy === nba.primary.action_id}
          onRequest={() => void request(nba.primary as Candidate)}
          args={args[nba.primary.action_id] ?? {}}
          onArg={(name, value) =>
            setArgs((current) => ({
              ...current,
              [nba.primary!.action_id]: {
                ...(current[nba.primary!.action_id] ?? {}),
                [name]: value,
              },
            }))
          }
        />
      ) : (
        <p className="text-xs text-ink-muted">
          Nothing is currently performable. Every published action is blocked by
          policy, missing a prerequisite, or has nobody who could approve it -
          the rows below say which.
        </p>
      )}

      {nba.fallback && (
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

      {(nba.alternatives.length > 0 || nba.ineligible.length > 0) && (
        <div className="space-y-1.5">
          <h4 className="text-[10px] font-semibold uppercase tracking-wide text-ink-muted">
            Alternatives
          </h4>
          <ul className="space-y-1">
            {nba.alternatives.map((candidate) => (
              <Row
                key={candidate.action_id}
                candidate={candidate}
                canCommand={canCommand}
                busy={busy === candidate.action_id}
                onRequest={() => void request(candidate)}
                args={args[candidate.action_id] ?? {}}
                onArg={(name, value) =>
                  setArgs((current) => ({
                    ...current,
                    [candidate.action_id]: {
                      ...(current[candidate.action_id] ?? {}),
                      [name]: value,
                    },
                  }))
                }
              />
            ))}
            {nba.ineligible.map((candidate) => (
              <Row
                key={candidate.action_id}
                candidate={candidate}
                canCommand={canCommand}
                busy={false}
                onRequest={() => undefined}
                args={{}}
                onArg={() => undefined}
              />
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

/**
 * What the application says this action needs before it can be run.
 *
 * Rendered from the manifest's own JSON schema, so a new action with a new
 * parameter gets a field here with no cockpit change. Only the *required*
 * properties are asked for: an optional argument left out is the application's
 * default, which is the application's decision rather than this screen's.
 */
/**
 * A form field is a string; the application's schema says what the value is.
 *
 * Coerced here rather than left to the Tool Gateway to be lenient about, because
 * the gateway validating strictly is the point: an integer parameter that
 * accepted "1" would be a validator that had stopped checking. The screen types
 * what it collected and the gateway still checks it.
 */
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

function Primary({
  candidate,
  canCommand,
  busy,
  onRequest,
  args,
  onArg,
}: {
  candidate: Candidate;
  canCommand: boolean;
  busy: boolean;
  onRequest: () => void;
  args: Record<string, string>;
  onArg: (name: string, value: string) => void;
}) {
  return (
    <article className="rounded-card border border-accent/40 bg-accent/5 px-3 py-2.5">
      <header className="flex flex-wrap items-center gap-2">
        <h3 className="text-sm font-semibold">{candidate.name}</h3>
        <RiskChip riskClass={candidate.risk_class} />
        <OutcomeChip outcome={candidate.policy.outcome} />
        <span className="flex-1" />
        <span className="font-mono text-sm font-semibold">
          {candidate.score >= 0 ? "+" : ""}
          {candidate.score.toFixed(2)}
        </span>
      </header>

      <p className="mt-1.5 text-xs text-ink-muted">{candidate.rationale}</p>
      <div className="mt-1 flex flex-wrap gap-1">
        <ProvenanceChip degraded={candidate.rationale_degraded} />
        {candidate.hypothesis_label && (
          <Chip title={candidate.hypothesis_key}>
            addresses: {candidate.hypothesis_label} (
            {Math.round(candidate.hypothesis_confidence * 100)}%)
          </Chip>
        )}
      </div>

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

      <ActionArgs
        schema={candidate.params_schema}
        values={args}
        onChange={onArg}
      />

      {/* One control, and it is the only one. A high-risk action's path is
          Request approval; there is no auto-execute affordance on this card for
          any action, at any risk class. */}
      <div className="mt-2.5 flex flex-wrap items-center gap-2">
        <button
          type="button"
          disabled={!canCommand || !candidate.eligible || busy}
          onClick={onRequest}
          className="rounded-card bg-accent px-3 py-1.5 text-xs font-medium text-surface-raised hover:bg-accent-strong disabled:opacity-50"
        >
          {candidate.requires_approval
            ? "Request approval"
            : "Run this action"}
        </button>
        {!canCommand && (
          <span className="text-[11px] text-ink-muted">
            Incident command requests actions.
          </span>
        )}
        {!candidate.eligible && (
          <span className="text-[11px] text-warn">
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
}: {
  candidate: Candidate;
  canCommand: boolean;
  busy: boolean;
  onRequest: () => void;
  args: Record<string, string>;
  onArg: (name: string, value: string) => void;
}) {
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
      {!candidate.eligible && (
        <span className="text-[11px] text-warn">
          {candidate.eligibility_reason}
        </span>
      )}
      <span className="flex-1" />
      {candidate.eligible && (
        <button
          type="button"
          disabled={!canCommand || busy}
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
