"use client";

import { useState } from "react";

import { ApiError } from "@/lib/api/client";
import { StatusPill } from "@/features/platform/components/StatusPill";
import {
  ConfigFields,
  coerceConfig,
} from "@/features/onboarding/components/ConfigFields";
import { runConnectionTests, type ConnectionTest } from "@/features/apps/api";
import {
  Adapter,
  Connector,
  CredentialScope,
  createConnector,
  deleteConnector,
} from "@/features/integrations/api";
import { relativeTime } from "@/lib/format";

const OUTCOME_TONE: Record<ConnectionTest["outcome"], string> = {
  passed: "border-ok/40 bg-ok/10 text-ok",
  failed: "border-danger/40 bg-danger/10 text-danger",
  pending: "border-warn/40 bg-warn/10 text-warn",
  skipped: "border-line bg-surface text-ink-muted",
};

/**
 * A pull or invoke adapter: SignalForge does the calling, so the operator gives
 * it config and a credential and then tests it.
 *
 * The credential inputs are write-only. They are posted once, encrypted at rest
 * under the binding, and never come back - so this card can tell you whether a
 * credential worked and never what it was. Re-entering it is how it is changed;
 * there is no "show" because there is nothing to show.
 *
 * The test is direction-correct: a pull adapter is pinged and then made to
 * perform one read-only capability, and an invoke adapter has its manifest read
 * and is never executed to prove it can be.
 */
export function PullConnectorCard({
  adapter,
  binding,
  appId,
  suggestedConfig,
  onChanged,
}: {
  adapter: Adapter;
  binding?: Connector;
  appId: string;
  suggestedConfig?: Record<string, unknown>;
  onChanged: () => void;
}) {
  const [scope, setScope] = useState<CredentialScope>(adapter.credential_scopes[0]);
  const [config, setConfig] = useState<Record<string, string>>(() =>
    Object.fromEntries(
      Object.entries(suggestedConfig ?? {}).map(([key, value]) => [key, String(value)]),
    ),
  );
  const [credentials, setCredentials] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [test, setTest] = useState<ConnectionTest | null>(null);

  const run = async (action: () => Promise<void>) => {
    setBusy(true);
    setError(null);
    try {
      await action();
      onChanged();
    } catch (failure) {
      setError(failure instanceof ApiError ? failure.message : "That did not work");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-3 rounded-card border border-line bg-surface-raised p-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-medium">{adapter.name}</span>
        <span className="font-mono text-xs text-ink-muted">{adapter.id}</span>
        <span className="border border-line px-2 py-0.5 text-[11px] uppercase text-ink-muted">
          {adapter.kind}
        </span>
        <span className="text-xs text-ink-muted">{adapter.direction}</span>
        {binding && <StatusPill status={binding.status} />}
      </div>
      <p className="max-w-prose text-xs text-ink-muted">{adapter.description}</p>

      {!adapter.runnable && (
        <p className="rounded-card border border-warn/40 bg-warn/10 px-2 py-1 text-xs text-warn">
          No runtime for this adapter on this node - it can be bound, and it will
          report unknown until one is installed.
        </p>
      )}

      {binding ? (
        <div className="space-y-2">
          <p className="text-xs text-ink-muted">
            Bound as <span className="font-medium">{binding.name}</span> at scope{" "}
            {binding.credential_scope} · credentials held:{" "}
            {binding.credential_fields.join(", ") || "none"} · last ok{" "}
            {relativeTime(binding.last_ok_at)}
          </p>
          {binding.status_detail && (
            <p className="text-xs text-ink-muted">{binding.status_detail}</p>
          )}
          {binding.last_error && (
            <p className="text-xs text-danger">{binding.last_error}</p>
          )}
          {test && (
            <div
              className={`space-y-1 rounded-card border px-2 py-1.5 text-xs ${
                OUTCOME_TONE[test.outcome]
              }`}
            >
              <p className="font-medium">
                {test.outcome} · {test.detail}
              </p>
              <ul className="space-y-0.5">
                {test.checks.map((check) => (
                  <li key={check.name}>
                    {check.name}: {check.outcome} - {check.detail}
                  </li>
                ))}
              </ul>
            </div>
          )}
          <div className="flex gap-2">
            <button
              disabled={busy}
              onClick={() =>
                void run(async () => {
                  const results = await runConnectionTests(appId, binding.id);
                  setTest(results[0] ?? null);
                })
              }
              className="rounded-card border border-line px-2.5 py-1 text-xs hover:border-accent hover:text-accent-strong disabled:opacity-50"
            >
              {busy ? "Testing…" : "Test connection"}
            </button>
            <button
              disabled={busy}
              onClick={() =>
                void run(async () => {
                  await deleteConnector(binding.id);
                  setTest(null);
                })
              }
              className="rounded-card border border-line px-2.5 py-1 text-xs text-danger hover:border-danger disabled:opacity-50"
            >
              Unbind
            </button>
          </div>
        </div>
      ) : (
        <form
          className="space-y-3"
          onSubmit={(event) => {
            event.preventDefault();
            void run(async () => {
              await createConnector({
                app_id: appId,
                adapter_id: adapter.id,
                name: `${appId} ${adapter.id}`,
                credential_scope: scope,
                config: coerceConfig(adapter.config_schema, config),
                credentials,
              });
            });
          }}
        >
          <ConfigFields
            schema={adapter.config_schema}
            values={config}
            onChange={(field, value) =>
              setConfig((current) => ({ ...current, [field]: value }))
            }
          />

          {adapter.credential_scopes.length > 1 && (
            <label className="block space-y-1">
              <span className="text-xs font-medium">Credential scope</span>
              <select
                value={scope}
                onChange={(event) => setScope(event.target.value as CredentialScope)}
                className="w-full rounded-card border border-line bg-surface px-2 py-1.5 text-sm"
              >
                {adapter.credential_scopes.map((option) => (
                  <option key={option} value={option}>
                    {option}
                  </option>
                ))}
              </select>
              <span className="block text-[11px] text-ink-muted">
                Read-only, remediation and drill credentials are separate bindings
                with separate blast radii. A read-only credential can never drive
                remediation.
              </span>
            </label>
          )}

          {adapter.auth.credential_fields.map((field) => (
            <label key={field} className="block space-y-1">
              <span className="text-xs font-medium">
                {field} <span className="text-danger">*</span>
              </span>
              <input
                type="password"
                required
                autoComplete="off"
                value={credentials[field] ?? ""}
                onChange={(event) =>
                  setCredentials((current) => ({
                    ...current,
                    [field]: event.target.value,
                  }))
                }
                className="w-full rounded-card border border-line bg-surface px-2 py-1.5 text-sm"
              />
            </label>
          ))}
          {adapter.auth.credential_fields.length > 0 && (
            <p className="text-[11px] text-ink-muted">
              Encrypted at rest and never returned. To change it, enter it again.
            </p>
          )}

          {error && (
            <p className="rounded-card border border-danger/40 bg-danger/10 px-2 py-1 text-xs text-danger">
              {error}
            </p>
          )}
          <button
            type="submit"
            disabled={busy}
            className="rounded-card bg-accent px-3 py-1.5 text-xs font-medium text-surface-raised hover:bg-accent-strong disabled:opacity-60"
          >
            {busy ? "Binding…" : "Connect"}
          </button>
        </form>
      )}

      {error && binding && (
        <p className="rounded-card border border-danger/40 bg-danger/10 px-2 py-1 text-xs text-danger">
          {error}
        </p>
      )}
    </div>
  );
}
