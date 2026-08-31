"use client";

import { useState } from "react";

import { useOrgSetup } from "@/features/org-setup/hooks/useOrgSetup";
import { InvitesPanel } from "@/features/org-setup/components/InvitesPanel";
import { RoleMatrixEditor } from "@/features/org-setup/components/RoleMatrixEditor";
import { OnCallPanel } from "@/features/org-setup/components/OnCallPanel";
import { EscalationPanel } from "@/features/org-setup/components/EscalationPanel";

const TABS = ["Invites", "Roles", "On-call", "Escalation"] as const;
type Tab = (typeof TABS)[number];

export function OrgSetupScreen() {
  const { state, reload } = useOrgSetup();
  const [tab, setTab] = useState<Tab>("Invites");

  if (state.kind === "loading") {
    return <p className="text-ink-muted">Loading organisation…</p>;
  }
  if (state.kind === "error") {
    return (
      <p className="rounded-card border border-danger/40 bg-danger/10 px-3 py-2 text-danger">
        {state.message}
      </p>
    );
  }

  const { users, invites, matrix, rotations, policies } = state.data;
  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-semibold">Organisation setup</h1>
      <nav className="flex gap-1 border-b border-line">
        {TABS.map((t) => (
          <button
            key={t}
            onClick={() => setTab(t)}
            className={`px-4 py-2 text-sm ${
              tab === t
                ? "border-b-2 border-accent font-medium text-ink"
                : "text-ink-muted hover:text-ink"
            }`}
          >
            {t}
          </button>
        ))}
      </nav>
      {tab === "Invites" && (
        <InvitesPanel invites={invites} roles={Object.keys(matrix.roles)} onChanged={() => void reload()} />
      )}
      {tab === "Roles" && (
        <RoleMatrixEditor users={users} matrix={matrix} onChanged={() => void reload()} />
      )}
      {tab === "On-call" && (
        <OnCallPanel users={users} rotations={rotations} onChanged={() => void reload()} />
      )}
      {tab === "Escalation" && (
        <EscalationPanel
          users={users}
          rotations={rotations}
          policies={policies}
          onChanged={() => void reload()}
        />
      )}
    </div>
  );
}
