import { Suspense } from "react";

import { AcceptInviteForm } from "@/features/auth/components/AcceptInviteForm";

export default function InvitePage({ params }: { params: { token: string } }) {
  return (
    <Suspense>
      <AcceptInviteForm token={params.token} />
    </Suspense>
  );
}
