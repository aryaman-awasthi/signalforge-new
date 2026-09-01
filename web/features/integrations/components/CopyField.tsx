"use client";

import { useState } from "react";

/**
 * A value the operator has to paste into somebody else's system: the ingest
 * URL, the bearer token, the HMAC secret. Copying is a button rather than a
 * select-and-drag because these are single-use moments - a half-selected token
 * is a support ticket at 3am.
 *
 * `secret` masks the value until it is deliberately revealed. The masked form
 * is the default for anything that authenticates, so a screen share or a
 * screenshot does not hand out a credential.
 */
export function CopyField({
  label,
  value,
  secret = false,
  hint,
}: {
  label: string;
  value: string;
  secret?: boolean;
  hint?: string;
}) {
  const [revealed, setRevealed] = useState(!secret);
  const [copied, setCopied] = useState(false);

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      // Clipboard access can be refused (insecure origin, denied permission).
      // Revealing the value is the fallback that always works.
      setRevealed(true);
    }
  };

  return (
    <div className="space-y-1">
      <div className="flex items-center gap-2">
        <span className="text-xs font-medium text-ink-muted">{label}</span>
        {secret && (
          <button
            type="button"
            onClick={() => setRevealed((current) => !current)}
            className="text-[11px] text-ink-muted hover:text-ink"
          >
            {revealed ? "Hide" : "Reveal"}
          </button>
        )}
        <span className="flex-1" />
        <button
          type="button"
          onClick={() => void copy()}
          className="rounded-card border border-line px-2 py-0.5 text-[11px] hover:border-accent hover:text-accent-strong"
        >
          {copied ? "Copied" : "Copy"}
        </button>
      </div>
      <code className="block break-all rounded-card border border-line bg-surface px-2 py-1 font-mono text-xs">
        {revealed ? value : "•".repeat(Math.min(value.length, 40))}
      </code>
      {hint && <p className="text-[11px] text-ink-muted">{hint}</p>}
    </div>
  );
}
