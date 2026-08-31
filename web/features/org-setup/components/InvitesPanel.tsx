"use client";

import { useState } from "react";

import { ApiError } from "@/lib/api/client";
import {
  CreatedInvite,
  Invite,
  createInvite,
  revokeInvite,
} from "@/features/org-setup/api";
import { FormError } from "@/features/auth/components/Field";

const STATUS_TONE: Record<Invite["status"], string> = {
  open: "text-ok",
  accepted: "text-ink-muted",
  revoked: "text-danger",
  expired: "text-warn",
};

export function InvitesPanel({
  invites,
  roles,
  onChanged,
}: {
  invites: Invite[];
  roles: string[];
  onChanged: () => void;
}) {
  const [email, setEmail] = useState("");
  const [selectedRoles, setSelectedRoles] = useState<string[]>([]);
  const [appId, setAppId] = useState("");
  const [error, setError] = useState<string>();
  const [created, setCreated] = useState<CreatedInvite>();

  async function submit() {
    setError(undefined);
    try {
      const invite = await createInvite({
        email,
        roles: selectedRoles,
        app_id: appId.trim() === "" ? null : appId.trim(),
      });
      setCreated(invite);
      setEmail("");
      setSelectedRoles([]);
      setAppId("");
      onChanged();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Something went wrong");
    }
  }

  return (
    <section className="space-y-4">
      <form
        className="space-y-3 rounded-card border border-line bg-surface-raised p-4"
        onSubmit={(event) => {
          event.preventDefault();
          void submit();
        }}
      >
        <h3 className="font-semibold">Invite a teammate</h3>
        <div className="flex flex-wrap gap-3">
          <input
            type="email"
            required
            value={email}
            onChange={(event) => setEmail(event.target.value)}
            placeholder="teammate@example.com"
            className="min-w-56 flex-1 rounded-card border border-line bg-surface px-3 py-2 text-sm"
          />
          <input
            value={appId}
            onChange={(event) => setAppId(event.target.value)}
            placeholder="App id (optional, scopes the roles)"
            className="min-w-56 flex-1 rounded-card border border-line bg-surface px-3 py-2 text-sm"
          />
        </div>
        <div className="flex flex-wrap gap-2">
          {roles.map((role) => (
            <label
              key={role}
              className={`cursor-pointer rounded-card border px-2 py-1 text-xs ${
                selectedRoles.includes(role)
                  ? "border-accent text-accent"
                  : "border-line text-ink-muted"
              }`}
            >
              <input
                type="checkbox"
                className="sr-only"
                checked={selectedRoles.includes(role)}
                onChange={(event) =>
                  setSelectedRoles((current) =>
                    event.target.checked
                      ? [...current, role]
                      : current.filter((r) => r !== role),
                  )
                }
              />
              {role}
            </label>
          ))}
        </div>
        <FormError message={error} />
        <button
          type="submit"
          disabled={selectedRoles.length === 0}
          className="rounded-card bg-accent px-3 py-2 text-sm font-medium text-surface-raised hover:bg-accent-strong disabled:opacity-60"
        >
          Create invite
        </button>
        {created && (
          <div className="rounded-card border border-ok/40 bg-ok/10 p-3 text-sm">
            <p className="font-medium">
              Invite for {created.email} created - share this link now, it is shown only once:
            </p>
            <code className="mt-1 block break-all font-mono text-xs">
              {typeof window === "undefined" ? "" : window.location.origin}/invite/{created.token}
            </code>
          </div>
        )}
      </form>

      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-line text-left text-ink-muted">
            <th className="py-2 pr-3 font-medium">Email</th>
            <th className="py-2 pr-3 font-medium">Roles</th>
            <th className="py-2 pr-3 font-medium">Status</th>
            <th className="py-2 font-medium">Actions</th>
          </tr>
        </thead>
        <tbody>
          {invites.map((invite) => (
            <tr key={invite.id} className="border-b border-line/60">
              <td className="py-2 pr-3">{invite.email}</td>
              <td className="py-2 pr-3 text-ink-muted">
                {invite.roles.join(", ")}
                {invite.app_id ? ` @ ${invite.app_id}` : ""}
              </td>
              <td className={`py-2 pr-3 ${STATUS_TONE[invite.status]}`}>{invite.status}</td>
              <td className="py-2">
                {invite.status === "open" && (
                  <button
                    onClick={() => void revokeInvite(invite.id).then(onChanged)}
                    className="text-xs text-danger hover:underline"
                  >
                    Revoke
                  </button>
                )}
              </td>
            </tr>
          ))}
          {invites.length === 0 && (
            <tr>
              <td colSpan={4} className="py-4 text-ink-muted">
                No invites yet.
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </section>
  );
}
