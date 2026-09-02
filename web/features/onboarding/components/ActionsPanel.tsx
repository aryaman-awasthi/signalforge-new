"use client";

import { useEffect, useState } from "react";

import { ApiError } from "@/lib/api/client";
import { Action, importActions, patchAction } from "@/features/apps/api";
import { rolesMatrix } from "@/features/org-setup/api";
import { relativeTime } from "@/lib/format";

const RISKS = ["low", "medium", "high", "critical"] as const;
const MODES = ["manual", "approval", "auto"] as const;

const APPROVE_HIGH_RISK = "approve_high_risk";

/**
 * Actions are imported from the application's own manifest, never typed.
 *
 * What an operator may change is narrow and the platform enforces it, not this
 * screen: a risk override may raise risk and never lower it, an approver must
 * actually hold approve_high_risk in the frozen roles matrix, and a high-risk
 * class can never be set to auto-execute - refused for an org owner exactly as
 * firmly as for anyone else. The controls below are shaped to match those rules
 * so the common case is not a 422, but the refusal is the API's and is shown
 * verbatim when it comes.
 *
 * The manifest's own declaration is kept beside the override permanently, which
 * is what lets a re-import refresh one and preserve the other.
 */
export function ActionsPanel({
  appId,
  actions,
  onChanged,
}: {
  appId: string;
  actions: Action[];
  onChanged: () => void;
}) {
  const [approverRoles, setApproverRoles] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  useEffect(() => {
    // Which roles may approve is the frozen matrix's answer, not a list kept
    // here: naming a role that cannot approve is refused at save time anyway.
    rolesMatrix()
      .then((matrix) =>
        setApproverRoles(
          Object.entries(matrix.roles)
            .filter(([, permissions]) => permissions.includes(APPROVE_HIGH_RISK))
            .map(([role]) => role),
        ),
      )
      .catch(() => setApproverRoles([]));
  }, []);

  const run = async (id: string, action: () => Promise<void>) => {
    setBusy(id);
    setError(null);
    try {
      await action();
      onChanged();
    } catch (failure) {
      setError(failure instanceof ApiError ? failure.message : "That did not work");
    } finally {
      setBusy(null);
    }
  };

  return (
    <section className="space-y-3">
      <div className="flex flex-wrap items-center gap-3">
        <h2 className="text-lg font-medium">Actions</h2>
        <span className="text-xs text-ink-muted">
          {actions.length} imported from the application&apos;s manifest
        </span>
        <button
          disabled={busy !== null}
          onClick={() =>
            void run("import", async () => {
              const result = await importActions(appId);
              setNotice(
                `${result.imported} new · ${result.refreshed} refreshed · ${result.withdrawn} withdrawn. ` +
                  "Overrides were preserved; anything reclassified into a high-risk class lost its auto setting.",
              );
            })
          }
          className="rounded-card border border-line px-2.5 py-1 text-xs hover:border-accent hover:text-accent-strong disabled:opacity-50"
        >
          {busy === "import" ? "Importing…" : "Import from manifest"}
        </button>
      </div>

      {error && (
        <p className="rounded-card border border-danger/40 bg-danger/10 px-3 py-2 text-sm text-danger">
          {error}
        </p>
      )}
      {notice && (
        <p className="rounded-card border border-ok/40 bg-ok/10 px-3 py-2 text-xs text-ok">
          {notice}
        </p>
      )}

      <ul className="divide-y divide-line rounded-card border border-line bg-surface-raised">
        {actions.map((action) => (
          <li key={action.action_id} className="space-y-2 px-3 py-3 text-sm">
            <div className="flex flex-wrap items-center gap-2">
              <span className="font-medium">{action.name}</span>
              <span className="font-mono text-xs text-ink-muted">
                {action.action_id}
              </span>
              <span className="border border-line px-2 py-0.5 text-[11px] uppercase text-ink-muted">
                {action.action_class}
              </span>
              {action.high_risk && (
                <span className="border border-danger/40 bg-danger/10 px-2 py-0.5 text-[11px] uppercase text-danger">
                  high risk
                </span>
              )}
              {!action.reversible && (
                <span className="border border-warn/40 bg-warn/10 px-2 py-0.5 text-[11px] text-warn">
                  irreversible
                </span>
              )}
              <span className="text-xs text-ink-muted">
                manifest says {action.manifest_risk} · effective{" "}
                {action.effective_risk} · imported {relativeTime(action.imported_at)}
              </span>
            </div>

            <div className="flex flex-wrap items-end gap-3">
              <label className="space-y-1">
                <span className="text-[11px] text-ink-muted">risk override</span>
                <select
                  value={action.risk_override ?? ""}
                  disabled={busy !== null}
                  onChange={(event) =>
                    void run(action.action_id, async () => {
                      await patchAction(appId, action.action_id,
                        event.target.value
                          ? { risk_override: event.target.value }
                          : { clear_risk_override: true },
                      );
                    })
                  }
                  className="block rounded-card border border-line bg-surface px-2 py-1 text-sm"
                >
                  <option value="">none (use the manifest)</option>
                  {RISKS.map((risk) => (
                    <option key={risk} value={risk}>
                      {risk}
                    </option>
                  ))}
                </select>
              </label>

              <label className="space-y-1">
                <span className="text-[11px] text-ink-muted">execution</span>
                <select
                  value={action.execution_mode}
                  disabled={busy !== null}
                  onChange={(event) =>
                    void run(action.action_id, async () => {
                      await patchAction(appId, action.action_id, {
                        execution_mode: event.target.value,
                      });
                    })
                  }
                  className="block rounded-card border border-line bg-surface px-2 py-1 text-sm"
                >
                  {MODES.map((mode) => (
                    <option
                      key={mode}
                      value={mode}
                      // The floor, made visible at the point of choosing rather
                      // than only at the point of refusing.
                      disabled={mode === "auto" && action.high_risk}
                    >
                      {mode}
                      {mode === "auto" && action.high_risk
                        ? " - never, for a high-risk class"
                        : ""}
                    </option>
                  ))}
                </select>
              </label>

              <div className="space-y-1">
                <span className="block text-[11px] text-ink-muted">approvers</span>
                <div className="flex flex-wrap gap-1">
                  {approverRoles.map((role) => {
                    const selected = action.approver_roles.includes(role);
                    return (
                      <button
                        key={role}
                        disabled={busy !== null}
                        onClick={() =>
                          void run(action.action_id, async () => {
                            await patchAction(appId, action.action_id, {
                              approver_roles: selected
                                ? action.approver_roles.filter((r) => r !== role)
                                : [...action.approver_roles, role],
                            });
                          })
                        }
                        className={`rounded-card border px-2 py-0.5 text-[11px] ${
                          selected
                            ? "border-accent text-accent"
                            : "border-line text-ink-muted"
                        }`}
                      >
                        {role}
                      </button>
                    );
                  })}
                </div>
              </div>

              <label className="flex items-center gap-1 text-xs">
                <input
                  type="checkbox"
                  checked={action.enabled}
                  disabled={busy !== null}
                  onChange={(event) =>
                    void run(action.action_id, async () => {
                      await patchAction(appId, action.action_id, {
                        enabled: event.target.checked,
                      });
                    })
                  }
                />
                enabled
              </label>
            </div>
            {action.description && (
              <p className="text-xs text-ink-muted">{action.description}</p>
            )}
          </li>
        ))}
        {actions.length === 0 && (
          <li className="px-3 py-6 text-sm text-ink-muted">
            Nothing imported yet. Actions come from the application&apos;s own action
            manifest through its bound actions connector - they are never typed
            here, so what SignalForge can do is exactly what the application says
            it can do.
          </li>
        )}
      </ul>
    </section>
  );
}
