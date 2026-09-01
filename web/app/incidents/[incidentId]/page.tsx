import { WarRoomShell } from "@/features/warroom/components/WarRoomShell";

export default function WarRoomPage({
  params,
}: {
  params: { incidentId: string };
}) {
  return <WarRoomShell incidentId={params.incidentId} />;
}
