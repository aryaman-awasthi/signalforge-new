"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

import { ApiError } from "@/lib/api/client";
import { AppTier, Environment, registerApp } from "@/features/apps/api";

const TIERS: AppTier[] = ["tier_1", "tier_2", "tier_3"];
const ENVIRONMENTS: Environment[] = ["production", "staging", "development"];

/**
 * Step 1 of onboarding, reachable from the apps overview so registering and
 * onboarding are one motion.
 *
 * The base URL is not decoration: auto-discovery walks it for the observe
 * contract and it pre-fills every adapter config that declares one. The API
 * refuses anything that is not an absolute http(s) root, and that refusal is
 * shown here rather than swallowed - the platform later fetches whatever is
 * stored.
 */
export function RegisterAppForm({ onRegistered }: { onRegistered: () => void }) {
  const router = useRouter();
  const [appId, setAppId] = useState("");
  const [name, setName] = useState("");
  const [tier, setTier] = useState<AppTier>("tier_2");
  const [environment, setEnvironment] = useState<Environment>("production");
  const [baseUrl, setBaseUrl] = useState("");
  const [ownerTeam, setOwnerTeam] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit() {
    setBusy(true);
    setError(null);
    try {
      const detail = await registerApp({
        app_id: appId.trim(),
        name: name.trim(),
        tier,
        environment,
        ...(baseUrl.trim() ? { base_url: baseUrl.trim() } : {}),
        ...(ownerTeam.trim() ? { owner_team: ownerTeam.trim() } : {}),
      });
      onRegistered();
      router.push(`/apps/${encodeURIComponent(detail.application.app_id)}/onboard/2`);
    } catch (failure) {
      setError(
        failure instanceof ApiError
          ? failure.message
          : "Could not register that application",
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <form
      className="space-y-3 rounded-card border border-line bg-surface-raised p-4"
      onSubmit={(event) => {
        event.preventDefault();
        void submit();
      }}
    >
      <h2 className="font-semibold">Onboard an application</h2>
      <p className="max-w-prose text-xs text-ink-muted">
        The app id is the identifier everything else is scoped by - roles,
        connectors, ingest keys, events and the audit chain. It cannot be changed
        later.
      </p>
      <div className="flex flex-wrap gap-3">
        <input
          required
          minLength={3}
          value={appId}
          onChange={(event) => setAppId(event.target.value)}
          placeholder="app id (e.g. checkout)"
          className="min-w-48 flex-1 rounded-card border border-line bg-surface px-3 py-2 text-sm"
        />
        <input
          required
          value={name}
          onChange={(event) => setName(event.target.value)}
          placeholder="Display name"
          className="min-w-48 flex-1 rounded-card border border-line bg-surface px-3 py-2 text-sm"
        />
      </div>
      <div className="flex flex-wrap gap-3">
        <select
          value={tier}
          onChange={(event) => setTier(event.target.value as AppTier)}
          className="rounded-card border border-line bg-surface px-3 py-2 text-sm"
        >
          {TIERS.map((option) => (
            <option key={option} value={option}>
              {option}
            </option>
          ))}
        </select>
        <select
          value={environment}
          onChange={(event) => setEnvironment(event.target.value as Environment)}
          className="rounded-card border border-line bg-surface px-3 py-2 text-sm"
        >
          {ENVIRONMENTS.map((option) => (
            <option key={option} value={option}>
              {option}
            </option>
          ))}
        </select>
        <input
          value={ownerTeam}
          onChange={(event) => setOwnerTeam(event.target.value)}
          placeholder="Owning team"
          className="min-w-40 flex-1 rounded-card border border-line bg-surface px-3 py-2 text-sm"
        />
      </div>
      <input
        value={baseUrl}
        onChange={(event) => setBaseUrl(event.target.value)}
        placeholder="Base URL (https://app.internal) - auto-discovery probes it"
        className="w-full rounded-card border border-line bg-surface px-3 py-2 text-sm"
      />
      {error && (
        <p className="rounded-card border border-danger/40 bg-danger/10 px-3 py-2 text-sm text-danger">
          {error}
        </p>
      )}
      <button
        type="submit"
        disabled={busy}
        className="rounded-card bg-accent px-3 py-2 text-sm font-medium text-surface-raised hover:bg-accent-strong disabled:opacity-60"
      >
        {busy ? "Registering…" : "Register and start the wizard"}
      </button>
    </form>
  );
}
