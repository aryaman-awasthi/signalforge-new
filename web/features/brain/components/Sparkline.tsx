"use client";

/**
 * Confidence over time, as a line an operator can read at a glance.
 *
 * Inline SVG rather than a charting library: it is one polyline over at most a
 * few dozen points, it has to render inside a card at 140px wide, and a
 * dependency for it would be a dependency in the bundle of every incident
 * screen.
 *
 * Fixed to the full 0..1 range rather than scaled to the data. A sparkline that
 * auto-scaled would make a wobble between 61% and 63% look identical to a
 * collapse from 65% to 32%, which is precisely the distinction the panel exists
 * to show.
 */
export function Sparkline({
  values,
  label,
}: {
  values: number[];
  label: string;
}) {
  if (values.length === 0) {
    return (
      <p className="text-[11px] text-ink-muted">
        No movement recorded yet — this is the first score.
      </p>
    );
  }

  const width = 140;
  const height = 32;
  const points =
    values.length === 1
      ? // One point is a flat line rather than a dot, so the card's height does
        // not change when the second entry arrives.
        [
          [0, height - values[0] * height],
          [width, height - values[0] * height],
        ]
      : values.map((value, index) => [
          (index / (values.length - 1)) * width,
          height - Math.min(1, Math.max(0, value)) * height,
        ]);

  const last = values[values.length - 1];
  const first = values[0];
  const direction = last > first ? "rose" : last < first ? "fell" : "held";
  const stroke =
    direction === "rose"
      ? "rgb(var(--sf-danger))"
      : direction === "fell"
        ? "rgb(var(--sf-ok))"
        : "rgb(var(--sf-ink-muted))";

  return (
    <svg
      viewBox={`0 0 ${width} ${height}`}
      width={width}
      height={height}
      role="img"
      aria-label={`${label}: confidence ${direction} from ${Math.round(
        first * 100,
      )}% to ${Math.round(last * 100)}% over ${values.length} recomputation${
        values.length === 1 ? "" : "s"
      }`}
      className="overflow-visible"
    >
      {/* Even odds, so an operator can see which side of it the line is on. */}
      <line
        x1={0}
        y1={height / 2}
        x2={width}
        y2={height / 2}
        stroke="rgb(var(--sf-line))"
        strokeDasharray="2 3"
        strokeWidth={1}
      />
      <polyline
        points={points.map(([x, y]) => `${x},${y}`).join(" ")}
        fill="none"
        stroke={stroke}
        strokeWidth={1.5}
        strokeLinejoin="round"
      />
      <circle
        cx={points[points.length - 1][0]}
        cy={points[points.length - 1][1]}
        r={2.5}
        fill={stroke}
      />
    </svg>
  );
}
