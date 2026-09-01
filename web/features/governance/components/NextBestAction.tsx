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
              />
            ))}
            {nba.ineligible.map((candidate) => (
              <Row
                key={candidate.action_id}
                candidate={candidate}
                canCommand={canCommand}
                busy={false}
                onRequest={() => undefined}
              />
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

function Primary({
  candidate,
  canCommand,
  busy,
  onRequest,
}: {
  candidate: Candidate;
  canCommand: boolean;
  busy: boolean;
  onRequest: () => void;
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
}: {
  candidate: Candidate;
  canCommand: boolean;
  busy: boolean;
  onRequest: () => void;
}) {
  return (
    <li className="flex flex-wrap items-center gap-2 rounded-card border border-line px-2 py-1.5 text-xs">
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
    </li>
  );
}
