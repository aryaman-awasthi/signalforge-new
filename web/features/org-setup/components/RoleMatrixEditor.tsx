"use client";

import { useState } from "react";

import { ApiError } from "@/lib/api/client";
import { OrgUser, RolesMatrix, setUserRoles } from "@/features/org-setup/api";
import { FormError } from "@/features/auth/components/Field";

/** Edits org-wide role bindings per user. The permission matrix itself is a
 * frozen contract - shown as tooltips, never editable. Per-app bindings keep
 * their rows intact and are managed with the apps feature (S3). */
export function RoleMatrixEditor({
  users,
  matrix,
  onChanged,
}: {
  users: OrgUser[];
  matrix: RolesMatrix;
  onChanged: () => void;
}) {
  const roles = Object.keys(matrix.roles);
  const [error, setError] = useState<string>();
  const [savingUser, setSavingUser] = useState<string>();

  async function toggle(user: OrgUser, role: string, granted: boolean) {
    setError(undefined);
    setSavingUser(user.id);
    const orgWide = user.bindings.filter((b) => b.app_id === null).map((b) => b.role);
    const next = granted ? [...orgWide, role] : orgWide.filter((r) => r !== role);
    const appScoped = user.bindings.filter((b) => b.app_id !== null);
    try {
      await setUserRoles(user.id, [
        ...next.map((r) => ({ role: r, app_id: null })),
        ...appScoped,
      ]);
      onChanged();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Something went wrong");
    } finally {
      setSavingUser(undefined);
    }
  }

  return (
    <section className="space-y-3">
      <FormError message={error} />
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-line text-left text-ink-muted">
              <th className="py-2 pr-3 font-medium">Member</th>
              {roles.map((role) => (
                <th
                  key={role}
                  className="px-2 py-2 text-center font-medium"
                  title={matrix.roles[role].join(", ") || "no permissions"}
                >
                  {role.replace(/_/g, " ")}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {users.map((user) => {
              const orgWide = new Set(
                user.bindings.filter((b) => b.app_id === null).map((b) => b.role),
              );
              return (
                <tr key={user.id} className="border-b border-line/60">
                  <td className="py-2 pr-3">
                    <div>{user.display_name}</div>
                    <div className="text-xs text-ink-muted">{user.email}</div>
                  </td>
                  {roles.map((role) => (
                    <td key={role} className="px-2 py-2 text-center">
                      <input
                        type="checkbox"
                        checked={orgWide.has(role)}
                        disabled={savingUser === user.id}
                        onChange={(event) => void toggle(user, role, event.target.checked)}
                        aria-label={`${user.display_name}: ${role}`}
                      />
                    </td>
                  ))}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <p className="text-xs text-ink-muted">
        Hover a role for its permissions. The permission matrix is fixed by
        contract; app-scoped bindings are managed per app once apps onboard.
      </p>
    </section>
  );
}
