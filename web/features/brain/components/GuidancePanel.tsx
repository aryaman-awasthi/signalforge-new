"use client";

import { BrainNode, Guidance } from "@/features/brain/api";

/**
 * S14 — Warning and Guidance (REQ-052).
 *
 * Four kinds of row, and every one of them is *advisory*: prior incidents that
 * resemble this one, hypotheses a prior incident chased and ruled out,
 * contradictions between what somebody said and what was measured, and content
 * that tried to instruct the assistant.
 *
 * The caveat is rendered first and comes from the server rather than being typed
 * here, so a client cannot show the rows without it. That is the whole
 * discipline of this panel: "we saw this three weeks ago and it was the
 * connection pool" saves twenty minutes when it is right and costs an hour when
 * it is wrong, and the only difference is whether anybody said it was a guess.
 *
 * Relevance movement is shown where the server computed one. An incident
 * becoming *more* relevant as evidence arrives is the interesting event, and a
 * static percentage would hide it.
 */
export function GuidancePanel({ guidance }: { guidance: Guidance | null }) {
  if (!guidance) {
    return (
      <p className="text-xs text-ink-muted">Guidance could not be read.</p>
    );
  }

  const empty =
    guidance.similar.length === 0 &&
    guidance.warnings.length === 0 &&
    guidance.contradictions.length === 0 &&
    guidance.injections.length === 0;

  return (
    <div className="space-y-2">
      <p className="rounded border border-line px-2 py-1 text-[11px] text-ink-muted">
        {guidance.caveat}
      </p>

      {empty && (
        <p className="text-xs text-ink-muted">
          Nothing yet: no prior incident is above the relevance floor, and
          nothing said on the bridge contradicts what is measured.
        </p>
      )}

      {guidance.contradictions.length > 0 && (
        <Section
          title="Contradictions"
          tone="danger"
          note="somebody said one thing and the platform measured another. Either the belief is stale and is driving the wrong work, or the measurement is pointed at the wrong thing."
          nodes={guidance.contradictions}
        />
      )}

      {guidance.warnings.length > 0 && (
        <Section
          title="Pinned mistake warnings"
          tone="warn"
          note="a similar incident pursued this and ruled it out."
          nodes={guidance.warnings}
          detail={(node) => ({
            source: String(node.detail.source_reference ?? ""),
            relevance: Number(node.detail.relevance ?? 0),
          })}
        />
      )}

      {guidance.similar.length > 0 && (
        <Section
          title="Similar prior incidents"
          tone="muted"
          note="ranked by relevance, which is similarity aged by how long ago it happened."
          nodes={guidance.similar}
          detail={(node) => ({
            source: String(node.detail.source_reference ?? ""),
            relevance: Number(node.detail.relevance ?? 0),
            movement: Number(node.detail.movement ?? 0),
          })}
        />
      )}

      {guidance.injections.length > 0 && (
        <Section
          title="Content that tried to instruct the assistant"
          tone="danger"
          note="recorded and kept verbatim; treated as data throughout. The assistant holds no tools and can change no state, so nothing inside it could have been executed."
          nodes={guidance.injections}
        />
      )}
    </div>
  );
}

function Section({
  title,
  note,
  tone,
  nodes,
  detail,
}: {
  title: string;
  note: string;
  tone: "danger" | "warn" | "muted";
  nodes: BrainNode[];
  detail?: (node: BrainNode) => {
    source?: string;
    relevance?: number;
    movement?: number;
  };
}) {
  const colour =
    tone === "danger"
      ? "text-danger"
      : tone === "warn"
        ? "text-warn"
        : "text-ink-muted";
  return (
    <section aria-label={title}>
      <h4 className={`text-[11px] font-semibold ${colour}`}>{title}</h4>
      <p className="text-[11px] text-ink-muted">{note}</p>
      <ul className="mt-1 space-y-1 text-[11px]">
        {nodes.map((node) => {
          const extra = detail?.(node);
          return (
            <li key={node.id}>
              <span>{node.label}</span>
              {extra?.source && (
                <span className="ml-1 rounded border border-line px-1 text-[10px] text-ink-muted">
                  {extra.source}
                </span>
              )}
              {typeof extra?.relevance === "number" && extra.relevance > 0 && (
                <span className="ml-1 text-ink-muted">
                  {Math.round(extra.relevance * 100)}% relevance
                </span>
              )}
              {typeof extra?.movement === "number" && extra.movement !== 0 && (
                <span
                  className={`ml-1 ${extra.movement > 0 ? "text-warn" : "text-ink-muted"}`}
                >
                  {extra.movement > 0 ? "▲" : "▼"}
                  {Math.abs(Math.round(extra.movement * 100))}%
                </span>
              )}
              {node.content && (
                <p className="text-ink-muted">{node.content}</p>
              )}
            </li>
          );
        })}
      </ul>
    </section>
  );
}
