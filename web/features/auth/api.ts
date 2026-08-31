/** Auth data access - all through the typed client (CLAUDE.md web rules). */

import { authApi, proxyApi } from "@/lib/api/client";

export type RoleBinding = { role: string; app_id: string | null };

export type SessionUser = {
  id: string;
  email: string;
  display_name: string;
  bindings: RoleBinding[];
};

export type Org = { id: string; name: string; slug: string; created_at: string };

export type AuthPayload = { user: SessionUser; org: Org };

export type Me = {
  user_id: string;
  tenant_id: string;
  email: string;
  display_name: string;
  bindings: RoleBinding[];
  permissions: string[];
};

export function signup(input: {
  org_name: string;
  email: string;
  password: string;
  display_name: string;
}) {
  return authApi.post<AuthPayload>("/signup", input);
}

export function login(input: { org_slug: string; email: string; password: string }) {
  return authApi.post<AuthPayload>("/login", input);
}

export function acceptInvite(input: {
  token: string;
  password: string;
  display_name: string;
}) {
  return authApi.post<AuthPayload>("/accept-invite", input);
}

export function logout() {
  return authApi.post<{ ok: boolean }>("/logout");
}

export function fetchMe() {
  return proxyApi.get<Me>("/auth/me");
}
