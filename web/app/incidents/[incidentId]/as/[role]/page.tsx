import { WarRoomShell } from "@/features/warroom/components/WarRoomShell";

/**
 * S17 - the same war room, as one role sees it.
 *
 * There is no second shell and no second set of components: the route passes a
 * role, and `ROLE_LAYOUTS` decides which panels are worth that person's
 * attention. Filtering is client side and the server enforces permissions
 * independently, so this can only ever hide something a role did not need - it
 * cannot show something they were not allowed to see.
 */
export default function RoleViewPage({
  params,
}: {
  params: { incidentId: string; role: string };
}) {
  return <WarRoomShell incidentId={params.incidentId} role={params.role} />;
}
