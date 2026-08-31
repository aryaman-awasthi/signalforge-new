import { Suspense } from "react";

import { SignupForm } from "@/features/auth/components/SignupForm";

export default function SignupPage() {
  return (
    <Suspense>
      <SignupForm />
    </Suspense>
  );
}
