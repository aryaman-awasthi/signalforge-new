"use client";

import Link from "next/link";

import { ReadinessBar } from "@/features/apps/components/ReadinessBar";
import { WizardStepper } from "@/features/onboarding/components/WizardStepper";
import { BasicsStep } from "@/features/onboarding/components/steps/BasicsStep";
import { ConnectStep } from "@/features/onboarding/components/steps/ConnectStep";
import { MapStep } from "@/features/onboarding/components/steps/MapStep";
import { TopologyStep } from "@/features/onboarding/components/steps/TopologyStep";
import { GovernanceStep } from "@/features/onboarding/components/steps/GovernanceStep";
import { BaselineStep } from "@/features/onboarding/components/steps/BaselineStep";
import { TestStep } from "@/features/onboarding/components/steps/TestStep";
import { useOnboarding } from "@/features/onboarding/hooks/useOnboarding";
import { WIZARD_STEPS } from "@/features/onboarding/steps";

/**
 * Screen 3 - the onboarding wizard shell.
 *
 * Steps are routes, not tab state, so a half-finished onboarding is a URL an
 * operator can send to whoever owns the credential for step 2. Nothing is
 * staged across steps: each step writes its own section through its own
 * endpoint and the whole application is re-read afterwards, because the domain
 * can refuse or adjust what a step sent (a policy edit the floor rejects, a
 * topology that turns out to be unchanged, a risk override a re-import raised).
 */
export function OnboardWizard({ appId, step }: { appId: string; step: number }) {
  const { state, reload } = useOnboarding(appId);
  const definition = WIZARD_STEPS[step - 1];

  if (state.kind === "loading") {
    return <p className="text-ink-muted">Loading {appId}…</p>;
  }
  if (state.kind === "error") {
    return (
      <div className="space-y-3">
        <p className="rounded-card border border-danger/40 bg-danger/10 px-3 py-2 text-danger">
          {state.message}
        </p>
        {state.status === 403 && (
          <p className="text-sm text-ink-muted">
            Your permission on this application changed. Everything you already
            saved is intact - somebody who holds manage_apps for it can carry on.
          </p>
        )}
        {state.status === 404 && (
          <p className="text-sm text-ink-muted">
            No application with this id in your organisation.
          </p>
        )}
        <Link href="/apps" className="text-sm text-accent hover:underline">
          Back to applications
        </Link>
      </div>
    );
  }

  const { detail } = state.data;
  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center gap-3">
        <Link href="/apps" className="text-sm text-ink-muted hover:text-ink">
          ← Applications
        </Link>
        <h1 className="text-2xl font-semibold">{detail.application.name}</h1>
        <span className="font-mono text-xs text-ink-muted">{appId}</span>
        <span className="flex-1" />
        <div className="w-56">
          <ReadinessBar
            score={detail.readiness.score}
            ready={detail.readiness.ready}
            threshold={detail.readiness.threshold}
          />
        </div>
      </div>

      <WizardStepper appId={appId} current={step} readiness={detail.readiness} />

      <section className="space-y-1">
        <h2 className="text-lg font-medium">
          {step}. {definition.title}
        </h2>
        <p className="max-w-prose text-sm text-ink-muted">{definition.blurb}</p>
      </section>

      {step === 1 && (
        <BasicsStep application={detail.application} onChanged={() => void reload()} />
      )}
      {step === 2 && <ConnectStep data={state.data} onChanged={() => void reload()} />}
      {step === 3 && (
        <MapStep
          appId={appId}
          profile={detail.profile}
          onChanged={() => void reload()}
        />
      )}
      {step === 4 && (
        <TopologyStep
          appId={appId}
          profile={detail.profile}
          onChanged={() => void reload()}
        />
      )}
      {step === 5 && (
        <GovernanceStep
          appId={appId}
          profile={detail.profile}
          onChanged={() => void reload()}
        />
      )}
      {step === 6 && (
        <BaselineStep
          appId={appId}
          profile={detail.profile}
          onChanged={() => void reload()}
        />
      )}
      {step === 7 && (
        <TestStep
          appId={appId}
          readiness={detail.readiness}
          onChanged={() => void reload()}
        />
      )}

      <nav className="flex items-center gap-2 border-t border-line pt-4 text-sm">
        {step > 1 && (
          <Link
            href={`/apps/${encodeURIComponent(appId)}/onboard/${step - 1}`}
            className="rounded-card border border-line px-3 py-1.5 hover:border-accent hover:text-accent-strong"
          >
            ← {WIZARD_STEPS[step - 2].title}
          </Link>
        )}
        <span className="flex-1" />
        {step < WIZARD_STEPS.length && (
          <Link
            href={`/apps/${encodeURIComponent(appId)}/onboard/${step + 1}`}
            className="rounded-card border border-line px-3 py-1.5 hover:border-accent hover:text-accent-strong"
          >
            {WIZARD_STEPS[step].title} →
          </Link>
        )}
        {/* The last step used to dead-end on Integration health. The thing
            onboarding was for is the application page, so that is where the
            wizard lets go. */}
        {step === WIZARD_STEPS.length && (
          <Link
            href={`/apps/${encodeURIComponent(appId)}`}
            className="rounded-card bg-accent px-3 py-1.5 font-medium text-surface-raised hover:bg-accent-strong"
          >
            Open the application →
          </Link>
        )}
        <Link
          href="/integration-health"
          className="rounded-card border border-line px-3 py-1.5 text-xs hover:border-accent hover:text-accent-strong"
        >
          Integration health
        </Link>
      </nav>
    </div>
  );
}
