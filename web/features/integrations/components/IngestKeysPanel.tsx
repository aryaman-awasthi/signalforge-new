"use client";

import { useCallback, useEffect, useState } from "react";

import { ApiError } from "@/lib/api/client";
import { CopyField } from "@/features/integrations/components/CopyField";
import { IngestCredentialOnce } from "@/features/integrations/components/IngestCredentialOnce";
import {
  IngestKey,
  IssuedIngestKey,
  listIngestKeys,
  revokeIngestKey,
  rotateIngestKey,
} from "@/features/integrations/api";
import { API_BASE_URL } from "@/lib/api/client";
import { freshness, relativeTime } from "@/lib/format";

/**
 * S21, ingest section. Every credential a sending application holds, with the
 * endpoint it posts to, the masked prefix that identifies it, what has actually
 * arrived on it, and why the most recent request was refused.
 *
 * The reason a refusal is readable here and nowhere else is deliberate: the
 * sender itself only ever gets a bare 401, because a caller that could tell
 * "unknown token" from "bad signature" from "clock skew" has an oracle.
 *
 * Rotate issues a successor and leaves both keys live - a sender cannot be
 * switched atomically, it is a config change in somebody else's system. Revoke
 * destroys the shared secret, so it stops the next post dead.
 */
export function IngestKeysPanel({ appId }: { appId?: string }) {
  const [keys, setKeys] = useState<IngestKey[] | null>(null);
  const [issued, setIssued] = useState<IssuedIngestKey | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  const reload = useCallback(async () => {
    try {
      setKeys(await listIngestKeys(appId));
    } catch (failure) {
      setError(
        failure instanceof ApiError ? failure.message : "Could not load ingest keys",
      );
    }
  }, [appId]);

  useEffect(() => {
    void reload();
  }, [reload]);

  const act = async (id: string, run: () => Promise<void>) => {
    setBusy(id);
    setError(null);
    try {
      await run();
      await reload();
    } catch (failure) {
      setError(failure instanceof ApiError ? failure.message : "That did not work");
    } finally {
      setBusy(null);
    }
  };

  if (keys === null) {
    return <p className="text-ink-muted">Loading ingest keys…</p>;
  }

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-3">
        <h2 className="text-lg font-medium">Ingest keys</h2>
        <span className="text-xs text-ink-muted">
          {keys.length} active credential(s)
        </span>
      </div>

      {error && (
        <p className="rounded-card border border-danger/40 bg-danger/10 px-3 py-2 text-sm text-danger">
          {error}
        </p>
      )}
      {issued && (
        <IngestCredentialOnce issued={issued} onDismiss={() => setIssued(null)} />
      )}

      <ul className="divide-y divide-line rounded-card border border-line bg-surface-raised">
        {keys.map((key) => (
          <li key={key.id} className="space-y-2 px-3 py-3 text-sm">
            <div className="flex flex-wrap items-center gap-2">
              <span className="font-medium">{key.name}</span>
              <span className="border border-line px-2 py-0.5 text-[11px] uppercase text-ink-muted">
                {key.stream}
              </span>
              <span className="font-mono text-xs text-ink-muted">
                {key.app_id} · {key.token_prefix}…
              </span>
              {key.rotated_from && (
                <span className="border border-warn/40 bg-warn/10 px-2 py-0.5 text-[11px] text-warn">
                  successor
                </span>
              )}
              <span className="flex-1" />
              <button
                disabled={busy === key.id}
                onClick={() =>
                  void act(key.id, async () => {
                    setIssued(await rotateIngestKey(key.id));
                  })
                }
                className="rounded-card border border-line px-2.5 py-1 text-xs hover:border-accent hover:text-accent-strong disabled:opacity-50"
              >
                Rotate
              </button>
              <button
                disabled={busy === key.id}
                onClick={() =>
                  void act(key.id, async () => {
                    await revokeIngestKey(key.id);
                  })
                }
                className="rounded-card border border-line px-2.5 py-1 text-xs text-danger hover:border-danger disabled:opacity-50"
              >
                Revoke
              </button>
            </div>

            <CopyField label="Ingest URL" value={`${API_BASE_URL}${key.path}`} />

            <p className="text-xs text-ink-muted">
              last event {freshness(key.freshness_s)} · last request{" "}
              {relativeTime(key.last_used_at)} · {key.accepted_count} accepted ·{" "}
              {key.duplicate_count} duplicate · {key.rejected_count} rejected
            </p>
            {key.last_error && (
              <p className="text-xs text-danger">
                last refusal: {key.last_error} - the sender saw only a bare 401
              </p>
            )}
          </li>
        ))}
        {keys.length === 0 && (
          <li className="px-3 py-6 text-sm text-ink-muted">
            No ingest keys. Issue one from an application&apos;s Connect step, where
            it is bound to a stream and to a push adapter.
          </li>
        )}
      </ul>
    </div>
  );
}
