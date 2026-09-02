import { Suspense } from "react";

import { SignupForm } from "@/features/auth/components/SignupForm";
import { Landing } from "@/features/shell/components/Landing";

export default function HomePage() {
  return (
    <Landing>
      <Suspense>
        <SignupForm />
      </Suspense>
    </Landing>
  );
}
