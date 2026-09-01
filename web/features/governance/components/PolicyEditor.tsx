"use client";

import { useCallback, useEffect, useState } from "react";

import { ApiError } from "@/lib/api/client";
import {
  DryRun,
  Policy,
  dryRunPolicy,
  getPolicy,
  savePolicy,
} from "@/features/governance/api";
import { Chip, OutcomeChip, RiskChip } from "@/features/governance/components/Chips";

/**
 * S21 - the policy editor.
 *
 * The high-risk rows are locked, and the lock is not a browser decision. The
 * server computes `locked` from the domain classification and sends the floor's
 * own sentence in `locked_reason`; this screen renders it. Removing the padlock
 * from this file would change nothing about what the platform permits - a
 * document setting a rollback to auto-execute is refused at construction, and
 * the refusal is written to the audit chain before the error comes back.
 *
 * That is worth saying on the screen too, and it is: the banner under the table
 * says the floor is enforced in the domain, not here.
 *
 * Dry run is the first-class control rather than an afterthought, because the
 * question an operator actually has is "what would this change" - and it is
 * answered by the same engine that decides, so the answer cannot be wrong in a
 * different way from production.
 */
export function PolicyEditor({ appId }: { appId: string }) {
  const [policy, setPolicy] = useState<Policy | null>(null);
  const [source, setSource] = useState("");
  const [dryRun, setDryRun] = useState<DryRun | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [confirmMidIncident, setConfirmMidIncident] = useState(false);

  const load = useCallback(async () => {
    try {
      const loaded = await getPolicy(appId);
      setPolicy(loaded);
      setSource(loaded.source);
      setError(null);
    } catch (caught) {
      setError(
        caught instanceof ApiError ? caught.message : "Could not load the policy",
      );
    }
  }, [appId]);

  useEffect(() => {
    void load();
  }, [load]);

  async function check() {
    setBusy(true);
    setError(null);
    setSaved(null);
    try {
      setDryRun(await dryRunPolicy(appId, source));
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "Dry run failed");
    } finally {
      setBusy(false);
    }
  }

  async function save() {
    setBusy(true);
    setError(null);
    setSaved(null);
    try {
      const updated = await savePolicy(appId, source, {
        confirmActiveIncident: confirmMidIncident,
      });
      setPolicy(updated);
      setSource(updated.source);
      setDryRun(null);
      setSaved(`Saved as version ${updated.version}.`);
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "Save refused");
    } finally {
      setBusy(false);
    }
  }

  if (!policy) {
    return (
      <p className="text-sm text-ink-muted">
        {error ?? "Loading the policy…"}
      </p>
    );
  }

  return (
    <div className="space-y-4">
      <header className="flex flex-wrap items-center gap-2">
        <h2 className="text-lg font-semibold">Execution policy</h2>
        <Chip>version {policy.version}</Chip>
        <Chip title={`floor ${policy.floor.floor_version}`}>
          🔒 floor {policy.floor.floor_version}
        </Chip>
      </header>

      {policy.open_incidents.length > 0 && (
        <p className="rounded-card border border-warn/40 bg-warn/10 px-3 py-2 text-xs text-warn">
          {policy.open_incidents.length} incident
          {policy.open_incidents.length === 1 ? " is" : "s are"} open on this
          application ({policy.open_incidents.slice(0, 3).join(", ")}). Editing
          now changes the rules under a running response and invalidates any
          approval already granted under version {policy.version}.
        </p>
      )}

      <section aria-label="Actions and what policy does with them" className="space-y-1.5">
        <h3 className="text-xs font-semibold uppercase tracking-wide text-ink-muted">
          What this policy does, action by action
        </h3>
        <ul className="space-y-1">
          {policy.actions.map((row) => (
            <li
              key={row.action_id}
              className={`rounded-card border px-2.5 py-2 text-xs ${
                row.locked ? "border-danger/40 bg-danger/5" : "border-line"
              }`}
            >
              <div className="flex flex-wrap items-center gap-2">
                {row.locked && (
                  <span aria-label="locked by the platform safety floor">🔒</span>
                )}
                <span className="font-medium">{row.name}</span>
                <code className="font-mono text-[10px] text-ink-muted">
                  {row.action_id}
                </code>
                <RiskChip riskClass={row.decision.risk_class} />
                <OutcomeChip outcome={row.decision.outcome} />
                {row.reversible && <Chip tone="ok">reversible</Chip>}
                {!row.enabled && <Chip>disabled</Chip>}
              </div>
              <p className="mt-0.5 text-[11px] text-ink-muted">
                {row.decision.reason}
              </p>
              {row.locked && (
                <p className="mt-0.5 text-[11px] text-danger">
                  Cannot auto-execute. {row.locked_reason}
                </p>
              )}
            </li>
          ))}
        </ul>
      </section>

      <section aria-label="The platform safety floor" className="space-y-1">
        <h3 className="text-xs font-semibold uppercase tracking-wide text-ink-muted">
          The platform safety floor
        </h3>
        <ul className="space-y-0.5 text-[11px] text-ink-muted">
          {policy.floor.rules.map((rule) => (
            <li key={rule.rule_id}>
              <span className="font-mono text-[10px]">{rule.rule_id}</span> —{" "}
              {rule.statement}
            </li>
          ))}
        </ul>
        <p className="text-[11px] text-ink-muted">
          These are enforced in the domain, not on this screen. A document that
          contradicts one of them is refused before it is stored, and the attempt
          is written to the audit chain.
        </p>
      </section>

      <section aria-label="Policy document" className="space-y-2">
        <h3 className="text-xs font-semibold uppercase tracking-wide text-ink-muted">
          Policy document
        </h3>
        <textarea
          value={source}
          onChange={(event) => setSource(event.target.value)}
          spellCheck={false}
          rows={16}
          className="w-full rounded-card border border-line bg-surface px-3 py-2 font-mono text-xs"
        />
        <div className="flex flex-wrap items-center gap-2">
          <button
            type="button"
            disabled={busy}
            onClick={() => void check()}
            className="rounded-card border border-line px-3 py-1.5 text-xs hover:border-accent disabled:opacity-50"
          >
            Dry run
          </button>
          <button
            type="button"
            disabled={busy}
            onClick={() => void save()}
            className="rounded-card bg-accent px-3 py-1.5 text-xs font-medium text-surface-raised hover:bg-accent-strong disabled:opacity-50"
          >
            Save a new version
          </button>
          {policy.open_incidents.length > 0 && (
            <label className="flex items-center gap-1.5 text-[11px]">
              <input
                type="checkbox"
                checked={confirmMidIncident}
                onChange={(event) => setConfirmMidIncident(event.target.checked)}
              />
              I understand this changes the rules mid-incident
            </label>
          )}
        </div>

        {error && (
          <p className="rounded-card border border-danger/40 bg-danger/10 px-3 py-2 text-xs text-danger">
            {error}
          </p>
        )}
        {saved && (
          <p className="rounded-card border border-ok/40 bg-ok/10 px-3 py-2 text-xs text-ok">
            {saved}
          </p>
        )}

        {dryRun && (
          <div
            className={`rounded-card border px-3 py-2 text-xs ${
              dryRun.accepted
                ? "border-line"
                : "border-danger/40 bg-danger/10 text-danger"
            }`}
          >
            {dryRun.accepted ? (
              <>
                <p className="font-medium">
                  {dryRun.changes.length === 0
                    ? "No decision would change."
                    : `${dryRun.changes.length} decision${dryRun.changes.length === 1 ? "" : "s"} would change.`}
                </p>
                <ul className="mt-1 space-y-0.5">
                  {dryRun.changes.map((change) => (
                    <li key={change.action_id} className="text-[11px]">
                      <span className="font-medium">{change.name}</span>:{" "}
                      {change.from.replace("_", " ")} →{" "}
                      {change.to.replace("_", " ")}
                    </li>
                  ))}
                </ul>
              </>
            ) : (
              <>
                <p className="font-medium">
                  Refused by {dryRun.floor_rule}. Nothing was stored.
                </p>
                <p className="mt-1 text-[11px]">{dryRun.error}</p>
              </>
            )}
          </div>
        )}
      </section>

      <section aria-label="Version history" className="space-y-1">
        <h3 className="text-xs font-semibold uppercase tracking-wide text-ink-muted">
          Versions
        </h3>
        <ul className="space-y-0.5 text-[11px] text-ink-muted">
          {policy.versions.map((version) => (
            <li key={version.version}>
              v{version.version} · {version.created_at.slice(0, 16).replace("T", " ")}{" "}
              · {version.author_label || version.author_id}
              {version.note && ` — ${version.note}`}
            </li>
          ))}
          {policy.versions.length === 0 && (
            <li>
              Nobody has edited this policy. The platform default is in force:
              nothing auto-executes.
            </li>
          )}
        </ul>
      </section>
    </div>
  );
}
