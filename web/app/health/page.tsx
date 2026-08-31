import { HealthPanel } from "@/features/health/components/HealthPanel";

export default function HealthPage() {
  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-semibold tracking-tight">Platform health</h1>
      <HealthPanel />
    </div>
  );
}
