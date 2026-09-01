import { AppDetail } from "@/features/incidents/components/AppDetail";

export default function AppDetailPage({ params }: { params: { appId: string } }) {
  return <AppDetail appId={params.appId} />;
}
