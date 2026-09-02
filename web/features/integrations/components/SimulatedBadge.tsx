/**
 * The SIMULATED marker (REQ-033). One component, one wording, everywhere a
 * simulated integration is shown - a simulated channel that reads like a real
 * one is the most dangerous thing a demo platform can do, so the label is never
 * left to a caller's discretion.
 */
export function SimulatedBadge({ simulated }: { simulated: boolean }) {
  if (!simulated) {
    return <span className="text-xs text-ink-muted">-</span>;
  }
  return (
    <span className="border border-warn/40 bg-warn/10 px-2 py-0.5 text-[10px] uppercase tracking-wide text-warn">
      simulated
    </span>
  );
}
