"use client";

import { useState } from "react";

import { ApiError } from "@/lib/api/client";
import { CopyField } from "@/features/integrations/components/CopyField";
import { IngestCredentialOnce } from "@/features/integrations/components/IngestCredentialOnce";
import {
  ConfigFields,
  coerceConfig,
} from "@/features/onboarding/components/ConfigFields";
import {
  Adapter,
  Connector,
  IngestKey,
  IngestStream,
  IssuedIngestKey,
  createConnector,
  issueIngestKey,
  revokeIngestKey,
  rotateIngestKey,
} from "@/features/integrations/api";
import { API_BASE_URL } from "@/lib/api/client";
import { freshness } from "@/lib/format";

/**
 * A push integration: the application posts to SignalForge, so there is nothing
 * to dial out to and nothing to ping. What the operator needs is the endpoint,
 * a credential to hand to whoever is sending, and a way to replace that
 * credential without dropping requests.
 *
 * Three things this card refuses to do:
 *
 * - it never shows a stored token. The platform holds only a sha256, so the
 *   masked prefix is genuinely all there is; the full value exists once, in the
 *   panel that appears immediately after issuing or rotating.
 * - it never presents rotation as an atomic swap. Rotate issues a *successor*
 *   and leaves both keys live, because the sender is a config change in
 *   somebody else's system and cannot be switched in the same instant.
 * - it never reports a push binding as healthy because this server answered.
 *   Freshness is when something last arrived.
 */
export function PushConnectorCard({
  stream,
  adapter,
  binding,
  keys,
  appId,
  onChanged,
}: {
  stream: IngestStream;
  adapter?: Adapter;
  binding?: Connector;
  keys: IngestKey[];
  appId: string;
  onChanged: () => void;
}) {
  const [config, setConfig] = useState<Record<string, string>>({});
  const [credentials, setCredentials] = useState<Record<string, string>>({});
  const [keyName, setKeyName] = useState(`${appId} ${stream}`);
  const [issued, setIssued] = useState<IssuedIngestKey | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const path = `/api/v1/ingest/${stream}`;
  const needsBinding = Boolean(adapter) && !binding;

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
        <span className="font-medium">{adapter ? adapter.name : `Canonical ${stream}`}</span>
        <span className="font-mono text-xs text-ink-muted">
          {adapter ? adapter.id : path}
        </span>
        <span className="rounded-full border border-line px-2 py-0.5 text-[11px] uppercase text-ink-muted">
          {stream}
        </span>
        <span className="text-xs text-ink-muted">inbound (the app posts)</span>
      </div>
      <p className="max-w-prose text-xs text-ink-muted">
        {adapter
          ? adapter.description
          : "For a sender that already speaks the canonical contract shape. No " +
            "adapter is needed - the key is the credential, the adapter is only " +
            "ever a translator."}
      </p>

      <CopyField
        label="Ingest URL"
        value={`${API_BASE_URL}${path}`}
        hint="POST a batch of up to 500 items here, signed. 202 means accepted."
      />

      {needsBinding && adapter ? (
        <form
          className="space-y-3"
          onSubmit={(event) => {
            event.preventDefault();
            void run(async () => {
              await createConnector({
                app_id: appId,
                adapter_id: adapter.id,
                name: `${appId} ${adapter.id}`,
                credential_scope: adapter.credential_scopes[0],
                config: coerceConfig(adapter.config_schema, config),
                credentials,
              });
            });
          }}
        >
          {!adapter.runnable && (
            <p className="rounded-card border border-warn/40 bg-warn/10 px-2 py-1 text-xs text-warn">
              No runtime for this translator on this node, so a vendor-shaped
              payload would be refused rather than normalised. A canonical sender
              needs no translator and works regardless.
            </p>
          )}
          <ConfigFields
            schema={adapter.config_schema}
            values={config}
            onChange={(field, value) =>
              setConfig((current) => ({ ...current, [field]: value }))
            }
          />
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
            {busy ? "Binding…" : "Add the translator"}
          </button>
          <p className="text-[11px] text-ink-muted">
            Binding this adapter lets a vendor-shaped payload be normalised into
            the canonical model. A canonical sender needs only a key.
          </p>
        </form>
      ) : (
        <>
          {issued && (
            <IngestCredentialOnce issued={issued} onDismiss={() => setIssued(null)} />
          )}

          <ul className="space-y-2">
            {keys.map((key) => (
              <li key={key.id} className="space-y-1 rounded-card border border-line p-2">
                <div className="flex flex-wrap items-center gap-2 text-xs">
                  <span className="font-medium">{key.name}</span>
                  <span className="font-mono text-ink-muted">
                    {key.token_prefix}… (masked - the token was shown once)
                  </span>
                  {key.rotated_from && (
                    <span className="rounded-full border border-warn/40 bg-warn/10 px-2 py-0.5 text-[10px] text-warn">
                      successor
                    </span>
                  )}
                  <span className="flex-1" />
                  <button
                    disabled={busy}
                    onClick={() =>
                      void run(async () => {
                        setIssued(await rotateIngestKey(key.id));
                      })
                    }
                    className="rounded-card border border-line px-2 py-0.5 hover:border-accent hover:text-accent-strong disabled:opacity-50"
                  >
                    Rotate
                  </button>
                  <button
                    disabled={busy}
                    onClick={() =>
                      void run(async () => {
                        await revokeIngestKey(key.id);
                      })
                    }
                    className="rounded-card border border-line px-2 py-0.5 text-danger hover:border-danger disabled:opacity-50"
                  >
                    Revoke
                  </button>
                </div>
                <p className="text-[11px] text-ink-muted">
                  last event {freshness(key.freshness_s)} · {key.accepted_count}{" "}
                  accepted · {key.duplicate_count} duplicate · {key.rejected_count}{" "}
                  rejected
                  {key.binding_id ? " · normalised by the bound adapter" : ""}
                </p>
                {key.last_error && (
                  <p className="text-[11px] text-danger">
                    last refusal: {key.last_error}
                  </p>
                )}
              </li>
            ))}
            {keys.length === 0 && (
              <li className="text-xs text-ink-muted">
                No key issued yet, so nothing can post to this stream. Until real
                traffic arrives this integration reads pending, not green -
                SignalForge is the server here and would always answer its own ping.
              </li>
            )}
          </ul>

          {error && (
            <p className="rounded-card border border-danger/40 bg-danger/10 px-2 py-1 text-xs text-danger">
              {error}
            </p>
          )}

          <form
            className="flex flex-wrap gap-2"
            onSubmit={(event) => {
              event.preventDefault();
              void run(async () => {
                setIssued(
                  await issueIngestKey({
                    app_id: appId,
                    stream,
                    name: keyName.trim(),
                    binding_id: binding?.id ?? null,
                  }),
                );
              });
            }}
          >
            <input
              required
              value={keyName}
              onChange={(event) => setKeyName(event.target.value)}
              placeholder="Key name (who holds it)"
              className="min-w-48 flex-1 rounded-card border border-line bg-surface px-2 py-1.5 text-sm"
            />
            <button
              type="submit"
              disabled={busy}
              className="rounded-card bg-accent px-3 py-1.5 text-xs font-medium text-surface-raised hover:bg-accent-strong disabled:opacity-60"
            >
              {busy ? "Issuing…" : "Issue ingest key"}
            </button>
          </form>
        </>
      )}
    </div>
  );
}
