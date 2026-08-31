/** Org setup data access - everything through the typed cookie-proxy client. */

import { proxyApi } from "@/lib/api/client";

export type RoleBinding = { role: string; app_id: string | null };

export type OrgUser = {
  id: string;
  email: string;
  display_name: string;
  is_active: boolean;
  bindings: RoleBinding[];
};

export type Invite = {
  id: string;
  email: string;
  roles: string[];
  app_id: string | null;
  expires_at: string;
  status: "open" | "accepted" | "revoked" | "expired";
};

export type CreatedInvite = Invite & { token: string };

export type RolesMatrix = { roles: Record<string, string[]> };

export type Rotation = {
  id: string;
  name: string;
  user_ids: string[];
  app_id: string | null;
  handoff_interval_hours: number;
};

export type EscalationStep = {
  target_type: "user" | "rotation";
  target_id: string;
  timeout_s: number;
};

export type Policy = {
  id: string;
  name: string;
  steps: EscalationStep[];
  app_id: string | null;
  page_offsets_s: number[];
};

export type PagePlan = {
  policy_id: string;
  simulated: true;
  plan: {
    order: number;
    target_type: string;
    target_id: string;
    resolved_user_id: string;
    page_at_offset_s: number;
  }[];
};

export const listUsers = () => proxyApi.get<OrgUser[]>("/users");
export const setUserRoles = (userId: string, bindings: RoleBinding[]) =>
  proxyApi.put<OrgUser>(`/users/${userId}/roles`, { bindings });

export const listInvites = () => proxyApi.get<Invite[]>("/invites");
export const createInvite = (input: {
  email: string;
  roles: string[];
  app_id: string | null;
}) => proxyApi.post<CreatedInvite>("/invites", input);
export const revokeInvite = (inviteId: string) =>
  proxyApi.del<Invite>(`/invites/${inviteId}`);

export const rolesMatrix = () => proxyApi.get<RolesMatrix>("/roles/matrix");

export const listRotations = () => proxyApi.get<Rotation[]>("/oncall/rotations");
export const saveRotation = (
  input: Omit<Rotation, "id">,
  rotationId?: string,
) =>
  rotationId
    ? proxyApi.put<Rotation>(`/oncall/rotations/${rotationId}`, input)
    : proxyApi.post<Rotation>("/oncall/rotations", input);
export const deleteRotation = (rotationId: string) =>
  proxyApi.del<void>(`/oncall/rotations/${rotationId}`);

export const listPolicies = () => proxyApi.get<Policy[]>("/escalation/policies");
export const savePolicy = (
  input: { name: string; steps: EscalationStep[]; app_id: string | null },
  policyId?: string,
) =>
  policyId
    ? proxyApi.put<Policy>(`/escalation/policies/${policyId}`, input)
    : proxyApi.post<Policy>("/escalation/policies", input);
export const deletePolicy = (policyId: string) =>
  proxyApi.del<void>(`/escalation/policies/${policyId}`);
export const testFirePolicy = (policyId: string) =>
  proxyApi.post<PagePlan>(`/escalation/policies/${policyId}/test`);
