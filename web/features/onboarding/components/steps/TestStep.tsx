"use client";

import { useState } from "react";

import { ApiError } from "@/lib/api/client";
import { ReadinessBar } from "@/features/apps/components/ReadinessBar";
import {
  CheckOutcome,
  Readiness,
  TestPage,
  runTestPage,
} from "@/features/apps/api";

const OUTCOME_TONE: Record<CheckOutcome, string> = {
  passed: "text-ok",
  failed: "text-danger",
  pending: "text-warn",
  skipped: "text-ink-muted",
};

/**
 * Step 7 - Test, and the readiness score.
 *
 * Every connector is tested in its own direction and every journey probe runs in
 * every declared region. The four outcomes are shown as four different things
 * rather than collapsed into pass/fail: a push connector reading **pending** is
 * not broken, it is waiting for real traffic, and an adapter with no runtime on
 * this node is **skipped**, not red.
 *
 * The score is the API's. `ready` needs every blocking check green *and* the
 * score over the threshold, so a high number can never read ready over a red
 * connector - the outstanding list is ordered blocking-first for that reason.
 */
export function TestStep({
  appId,
  readiness,
  onChanged,
}: {
  appId: string;
  readiness: Readiness;
  onChanged: () => void;
}) {
  const [report, setReport] = useState<TestPage | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function run() {
    setBusy(true);
    setError(null);
    try {
      setReport(await runTestPage(appId));
      onChanged();
    } catch (failure) {
      setError(
        failure instanceof ApiError ? failure.message : "Could not run the tests",
      );
    } finally {
      setBusy(false);
    }
  }

  const current = report?.readiness ?? readiness;
  const outstanding = new Set(current.outstanding);

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center gap-4">
        <button
          onClick={() => void run()}
          disabled={busy}
          className="rounded-card bg-accent px-3 py-2 text-sm font-medium text-surface-raised hover:bg-accent-strong disabled:opacity-60"
        >
          {busy ? "Running…" : "Run every test"}
        </button>
        <div className="w-64">
          <ReadinessBar
            score={current.score}
            ready={current.ready}
            threshold={current.threshold}
          />
        </div>
      </div>

      {error && (
        <p className="rounded-card border border-danger/40 bg-danger/10 px-3 py-2 text-sm text-danger">
          {error}
        </p>
      )}

      {report && (
        <div className="space-y-4">
          <p className="text-sm">
            <span className="text-ok">{report.summary.passed} passed</span> ·{" "}
            <span className="text-danger">{report.summary.failed} failed</span> ·{" "}
            <span className="text-warn">{report.summary.pending} pending</span> ·{" "}
            <span className="text-ink-muted">{report.summary.skipped} skipped</span>
          </p>

          <section className="space-y-2">
            <h3 className="text-sm font-medium">Connectors</h3>
            <ul className="divide-y divide-line rounded-card border border-line bg-surface-raised text-xs">
              {report.connectors.map((test) => (
                <li key={test.binding_id} className="space-y-1 px-3 py-2">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className={`w-16 uppercase ${OUTCOME_TONE[test.outcome]}`}>
                      {test.outcome}
                    </span>
                    <span className="font-medium">{test.adapter_name}</span>
                    <span className="text-ink-muted">{test.direction}</span>
                    {test.simulated && (
                      <span className="rounded-full border border-warn/40 bg-warn/10 px-2 py-0.5 text-[10px] uppercase text-warn">
                        simulated
                      </span>
                    )}
                  </div>
                  <p className="text-ink-muted">{test.detail}</p>
                </li>
              ))}
            </ul>
          </section>

          <section className="space-y-2">
            <h3 className="text-sm font-medium">Journey probes</h3>
            <ul className="divide-y divide-line rounded-card border border-line bg-surface-raised text-xs">
              {report.probes.map((probe, index) => (
                <li
                  key={`${probe.probe_id}-${index}`}
                  className="flex flex-wrap items-center gap-2 px-3 py-2"
                >
                  <span className={`w-16 uppercase ${OUTCOME_TONE[probe.outcome]}`}>
                    {probe.outcome}
                  </span>
                  <span className="font-mono">{probe.target}</span>
                  <span className="text-ink-muted">
                    {probe.status_code ?? "-"} · {Math.round(probe.latency_ms)}ms ·{" "}
                    {probe.detail}
                  </span>
                </li>
              ))}
              {report.probes.length === 0 && (
                <li className="px-3 py-2 text-ink-muted">No probes mapped.</li>
              )}
            </ul>
          </section>
        </div>
      )}

      <section className="space-y-2">
        <h3 className="text-sm font-medium">Readiness checks</h3>
        <ul className="divide-y divide-line rounded-card border border-line bg-surface-raised text-xs">
          {current.checks.map((check) => (
            <li key={check.key} className="flex flex-wrap items-center gap-2 px-3 py-2">
              <span
                className={`w-16 uppercase ${
                  check.passed ? "text-ok" : check.blocking ? "text-danger" : "text-warn"
                }`}
              >
                {check.passed ? "ok" : check.blocking ? "blocking" : "missing"}
              </span>
              <span className="w-40 font-medium">{check.label}</span>
              <span className="font-mono text-ink-muted">
                {check.step} · {check.weight}pt
              </span>
              <span className="min-w-0 flex-1 text-ink-muted">{check.detail}</span>
              {outstanding.has(check.key) && !check.passed && (
                <span className="rounded-full border border-line px-2 py-0.5 text-[10px] uppercase text-ink-muted">
                  outstanding
                </span>
              )}
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}
