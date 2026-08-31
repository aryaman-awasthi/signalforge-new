"use client";

import { useState } from "react";

import { acceptInvite } from "@/features/auth/api";
import { useAuthForm } from "@/features/auth/hooks/useAuthForm";
import { Field, FormError, SubmitButton } from "@/features/auth/components/Field";

export function AcceptInviteForm({ token }: { token: string }) {
  const { status, run } = useAuthForm("/");
  const [displayName, setDisplayName] = useState("");
  const [password, setPassword] = useState("");

  return (
    <form
      className="mx-auto max-w-sm space-y-4 rounded-card border border-line bg-surface-raised p-6"
      onSubmit={(event) => {
        event.preventDefault();
        void run(() => acceptInvite({ token, password, display_name: displayName }));
      }}
    >
      <h1 className="text-xl font-semibold">Join the organisation</h1>
      <p className="text-sm text-ink-muted">
        Set up your account to accept this invite. Expired or revoked invites
        cannot be accepted - ask your org owner for a fresh one.
      </p>
      <Field label="Your name" value={displayName} onChange={setDisplayName}
        autoComplete="name" />
      <Field label="Password (12+ characters)" type="password" value={password}
        onChange={setPassword} autoComplete="new-password" />
      <FormError message={status.kind === "error" ? status.message : undefined} />
      <SubmitButton label="Accept invite" busy={status.kind === "submitting"} />
    </form>
  );
}
