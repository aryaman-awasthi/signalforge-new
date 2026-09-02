"use client";

import { useState } from "react";
import Link from "next/link";

import { signup } from "@/features/auth/api";
import { useAuthForm } from "@/features/auth/hooks/useAuthForm";
import { Field, FormError, SubmitButton } from "@/features/auth/components/Field";

export function SignupForm() {
  const { status, run } = useAuthForm("/setup");
  const [orgName, setOrgName] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");

  return (
    <form
      className="flex flex-col gap-4 bg-surface-raised p-6"
      onSubmit={(event) => {
        event.preventDefault();
        void run(() =>
          signup({ org_name: orgName, email, password, display_name: displayName }),
        );
      }}
    >
      <h2 className="text-tiny uppercase tracking-panel text-accent">Create your organisation</h2>
      <Field label="Organisation name" value={orgName} onChange={setOrgName}
        placeholder="Acme Corp" autoComplete="organization" />
      <Field label="Your name" value={displayName} onChange={setDisplayName}
        autoComplete="name" />
      <Field label="Email" type="email" value={email} onChange={setEmail}
        autoComplete="email" />
      <Field label="Password (12+ characters)" type="password" value={password}
        onChange={setPassword} autoComplete="new-password" />
      <FormError message={status.kind === "error" ? status.message : undefined} />
      <SubmitButton label="Create organisation" busy={status.kind === "submitting"} />
      <p className="text-sm text-ink-muted">
        You become the org owner and can invite the rest of the team.{" "}
        <Link href="/login" className="text-accent hover:text-accent-strong">
          Already set up?
        </Link>
      </p>
    </form>
  );
}
