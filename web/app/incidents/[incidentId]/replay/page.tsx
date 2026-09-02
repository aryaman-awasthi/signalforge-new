import { ReplayScreen } from "@/features/audit/components/ReplayScreen";

export default function ReplayPage({
  params,
}: {
  params: { incidentId: string };
}) {
  return <ReplayScreen incidentId={params.incidentId} />;
}
