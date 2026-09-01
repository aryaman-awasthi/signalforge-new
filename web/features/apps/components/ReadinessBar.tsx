/**
 * The readiness score, and the one thing it is never allowed to do: read
 * "ready" while a blocking check is red. The score and the verdict are two
 * separate pieces of information on purpose - 90 with no SLO is not watchable,
 * and a bar alone would suggest it is.
 */
export function ReadinessBar({
  score,
  ready,
  threshold,
  compact = false,
}: {
  score: number;
  ready: boolean;
  threshold: number;
  compact?: boolean;
}) {
  const tone = ready ? "bg-ok" : score >= threshold ? "bg-warn" : "bg-accent";
  return (
    <div className={compact ? "flex items-center gap-2" : "space-y-1"}>
      <div className="h-1.5 w-full min-w-24 overflow-hidden rounded-full bg-line">
        <div
          className={`h-full ${tone}`}
          style={{ width: `${Math.max(0, Math.min(100, score))}%` }}
        />
      </div>
      <div className="flex shrink-0 items-center gap-2 text-xs">
        <span className="font-mono">{score}/100</span>
        <span
          className={
            ready
              ? "rounded-full border border-ok/40 bg-ok/10 px-2 py-0.5 text-ok"
              : "rounded-full border border-warn/40 bg-warn/10 px-2 py-0.5 text-warn"
          }
        >
          {ready ? "ready" : "not ready"}
        </span>
      </div>
    </div>
  );
}
