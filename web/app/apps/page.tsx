import { Suspense } from "react";

import { AppsOverview } from "@/features/apps/components/AppsOverview";

export default function AppsPage() {
  return (
    <Suspense>
      <AppsOverview />
    </Suspense>
  );
}
