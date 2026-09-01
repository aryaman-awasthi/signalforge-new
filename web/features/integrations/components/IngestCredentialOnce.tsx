"use client";

import { CopyField } from "@/features/integrations/components/CopyField";
import type { IssuedIngestKey } from "@/features/integrations/api";
import { API_BASE_URL } from "@/lib/api/client";

/**
 * The 201 body of an issue or a rotate, shown exactly once.
 *
 * Nothing here is persisted by the cockpit - no browser storage, no query
 * cache, no URL. The token's sha256 is all the platform stored and the secret is
 * Fernet ciphertext, so this panel is genuinely the last time either value
 * exists in readable form. The panel says so, because an operator who dismisses
 * it expecting to look it up later has to rotate instead.
 */
export function IngestCredentialOnce({
  issued,
  onDismiss,
}: {
  issued: IssuedIngestKey;
  onDismiss?: () => void;
}) {
  return (
    <div className="space-y-3 rounded-card border border-ok/40 bg-ok/10 p-3">
      <div className="flex items-start gap-2">
        <div className="min-w-0 flex-1">
          <p className="text-sm font-medium">
            {issued.rotated_from ? "Successor key issued" : "Ingest key issued"} -{" "}
            {issued.name}
          </p>
          <p className="text-xs text-ink-muted">
            Copy both values into the sending system now. SignalForge stores only
            the token&apos;s hash and the secret as ciphertext, so this is the last
            time either can be read. Lost one? Rotate - do not guess.
            {issued.rotated_from &&
              " The previous key stays live until you revoke it, so the sender can be moved without dropping a request."}
          </p>
        </div>
        {onDismiss && (
          <button
            type="button"
            onClick={onDismiss}
            className="text-xs text-ink-muted hover:text-ink"
          >
            Dismiss
          </button>
        )}
      </div>

      <CopyField
        label="Ingest URL"
        value={`${API_BASE_URL}${issued.path}`}
        hint={`POST a batch here. Stream ${issued.stream}, app ${issued.app_id}.`}
      />
      <CopyField
        label="Bearer token"
        value={issued.token}
        secret
        hint="Authorization: Bearer <token>"
      />
      <CopyField
        label="HMAC shared secret"
        value={issued.secret}
        secret
        hint={
          "Sign '<unix seconds>.<raw body>' with HMAC-SHA256 and send the hex " +
          "digest in X-SignalForge-Signature, with the same seconds in " +
          "X-SignalForge-Timestamp."
        }
      />
    </div>
  );
}
