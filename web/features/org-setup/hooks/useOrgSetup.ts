"use client";

import { useCallback, useEffect, useState } from "react";

import { ApiError } from "@/lib/api/client";
import {
  Invite,
  OrgUser,
  Policy,
  RolesMatrix,
  Rotation,
  listInvites,
  listPolicies,
  listRotations,
  listUsers,
  rolesMatrix,
} from "@/features/org-setup/api";

export type SetupData = {
  users: OrgUser[];
  invites: Invite[];
  matrix: RolesMatrix;
  rotations: Rotation[];
  policies: Policy[];
};

export type SetupState =
  | { kind: "loading" }
  | { kind: "ready"; data: SetupData }
  | { kind: "error"; message: string };

/** One load for the whole setup screen; panels call reload() after mutations. */
export function useOrgSetup() {
  const [state, setState] = useState<SetupState>({ kind: "loading" });

  const reload = useCallback(async () => {
    try {
      const [users, invites, matrix, rotations, policies] = await Promise.all([
        listUsers(),
        listInvites(),
        rolesMatrix(),
        listRotations(),
        listPolicies(),
      ]);
      setState({ kind: "ready", data: { users, invites, matrix, rotations, policies } });
    } catch (error) {
      setState({
        kind: "error",
        message: error instanceof ApiError ? error.message : "Something went wrong",
      });
    }
  }, []);

  useEffect(() => {
    void reload();
  }, [reload]);

  return { state, reload };
}
