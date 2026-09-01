"use client";

import { useState } from "react";

import { ApiError } from "@/lib/api/client";
import {
  Application,
  AppTier,
  Environment,
  patchApp,
} from "@/features/apps/api";

const TIERS: AppTier[] = ["tier_0", "tier_1", "tier_2", "tier_3"];
const ENVIRONMENTS: Environment[] = ["production", "staging", "development"];

/**
 * Step 1. Everything here except the app id can be corrected later; the app id
 * cannot, because roles, connectors, ingest keys, events and audit entries are
 * all already scoped by it.
 */
export function BasicsStep({
  application,
  onChanged,
}: {
  application: Application;
  onChanged: () => void;
}) {
  const [name, setName] = useState(application.name);
  const [tier, setTier] = useState<AppTier>(application.tier);
  const [environment, setEnvironment] = useState<Environment>(application.environment);
  const [baseUrl, setBaseUrl] = useState(application.base_url ?? "");
  const [ownerTeam, setOwnerTeam] = useState(application.owner_team);
  const [description, setDescription] = useState(application.description);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const [busy, setBusy] = useState(false);

  async function save() {
    setBusy(true);
    setError(null);
    setSaved(false);
    try {
      const trimmed = baseUrl.trim();
      await patchApp(application.app_id, {
        name,
        tier,
        environment,
        owner_team: ownerTeam,
        description,
        ...(trimmed
          ? { base_url: trimmed }
          : application.base_url
            ? { clear_base_url: true }
            : {}),
      });
      setSaved(true);
      onChanged();
    } catch (failure) {
      setError(failure instanceof ApiError ? failure.message : "Could not save");
    } finally {
      setBusy(false);
    }
  }

  return (
    <form
      className="max-w-2xl space-y-3"
      onSubmit={(event) => {
        event.preventDefault();
        void save();
      }}
    >
      <div className="rounded-card border border-line bg-surface px-3 py-2 text-xs text-ink-muted">
        App id <span className="font-mono text-ink">{application.app_id}</span> - fixed.
        Roles, connectors, ingest keys, events and the audit chain are already
        scoped by it.
      </div>

      <label className="block space-y-1">
        <span className="text-xs font-medium">Display name</span>
        <input
          required
          value={name}
          onChange={(event) => setName(event.target.value)}
          className="w-full rounded-card border border-line bg-surface px-3 py-2 text-sm"
        />
      </label>

      <div className="flex flex-wrap gap-3">
        <label className="space-y-1">
          <span className="text-xs font-medium">Tier</span>
          <select
            value={tier}
            onChange={(event) => setTier(event.target.value as AppTier)}
            className="block rounded-card border border-line bg-surface px-3 py-2 text-sm"
          >
            {TIERS.map((option) => (
              <option key={option} value={option}>
                {option}
              </option>
            ))}
          </select>
        </label>
        <label className="space-y-1">
          <span className="text-xs font-medium">Environment</span>
          <select
            value={environment}
            onChange={(event) => setEnvironment(event.target.value as Environment)}
            className="block rounded-card border border-line bg-surface px-3 py-2 text-sm"
          >
            {ENVIRONMENTS.map((option) => (
              <option key={option} value={option}>
                {option}
              </option>
            ))}
          </select>
        </label>
        <label className="flex-1 space-y-1">
          <span className="text-xs font-medium">Owning team</span>
          <input
            value={ownerTeam}
            onChange={(event) => setOwnerTeam(event.target.value)}
            className="w-full rounded-card border border-line bg-surface px-3 py-2 text-sm"
          />
        </label>
      </div>

      <label className="block space-y-1">
        <span className="text-xs font-medium">Base URL</span>
        <input
          value={baseUrl}
          onChange={(event) => setBaseUrl(event.target.value)}
          placeholder="https://app.internal"
          className="w-full rounded-card border border-line bg-surface px-3 py-2 text-sm"
        />
        <span className="block text-[11px] text-ink-muted">
          Auto-discovery probes the observe contract under this URL, and every
          adapter config that declares a base_url is pre-filled from it. It must be
          an absolute http(s) root with no query or fragment - the platform later
          fetches whatever is stored here.
        </span>
      </label>

      <label className="block space-y-1">
        <span className="text-xs font-medium">Description</span>
        <textarea
          value={description}
          rows={2}
          onChange={(event) => setDescription(event.target.value)}
          className="w-full rounded-card border border-line bg-surface px-3 py-2 text-sm"
        />
      </label>

      {error && (
        <p className="rounded-card border border-danger/40 bg-danger/10 px-3 py-2 text-sm text-danger">
          {error}
        </p>
      )}
      {saved && !error && (
        <p className="rounded-card border border-ok/40 bg-ok/10 px-3 py-2 text-sm text-ok">
          Saved.
        </p>
      )}
      <button
        type="submit"
        disabled={busy}
        className="rounded-card bg-accent px-3 py-2 text-sm font-medium text-surface-raised hover:bg-accent-strong disabled:opacity-60"
      >
        {busy ? "Saving…" : "Save basics"}
      </button>
    </form>
  );
}
