"use client";

import { useState } from "react";
import Link from "next/link";

import { login } from "@/features/auth/api";
import { useAuthForm } from "@/features/auth/hooks/useAuthForm";
import { Field, FormError, SubmitButton } from "@/features/auth/components/Field";

export function LoginForm() {
  const { status, run } = useAuthForm("/setup");
  const [orgSlug, setOrgSlug] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");

  return (
    <form
      className="mx-auto flex w-full max-w-sm flex-col gap-4 bg-surface-raised p-6"
      onSubmit={(event) => {
        event.preventDefault();
        void run(() => login({ org_slug: orgSlug, email, password }));
      }}
    >
      <h1 className="text-xl font-semibold">Sign in</h1>
      <Field label="Organisation slug" value={orgSlug} onChange={setOrgSlug}
        placeholder="acme-corp" autoComplete="organization" />
      <Field label="Email" type="email" value={email} onChange={setEmail}
        autoComplete="email" />
      <Field label="Password" type="password" value={password} onChange={setPassword}
        autoComplete="current-password" />
      <FormError message={status.kind === "error" ? status.message : undefined} />
      <SubmitButton label="Sign in" busy={status.kind === "submitting"} />
      <p className="text-sm text-ink-muted">
        New organisation?{" "}
        <Link href="/signup" className="text-accent hover:text-accent-strong">
          Create one
        </Link>
      </p>
    </form>
  );
}
