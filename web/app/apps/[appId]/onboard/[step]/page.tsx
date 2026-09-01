import { OnboardWizard } from "@/features/onboarding/components/OnboardWizard";
import { stepFromParam } from "@/features/onboarding/steps";

export default function OnboardStepPage({
  params,
}: {
  params: { appId: string; step: string };
}) {
  return (
    <OnboardWizard
      appId={decodeURIComponent(params.appId)}
      step={stepFromParam(params.step)}
    />
  );
}
