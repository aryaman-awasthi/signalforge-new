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
 *
 * `compact` is the cockpit (S9): the three rows most worth a glance -
 * contradictions first, then pinned warnings, then the closest prior incident -
 * and the caveat as one line under them.
 */
export function GuidancePanel({
  guidance,
  compact = false,
}: {
  guidance: Guidance | null;
  compact?: boolean;
}) {
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

  if (compact) {
    const rows: { node: BrainNode; kind: string; tone: string }[] = [
      ...guidance.contradictions.map((node) => ({ node, kind: "contradiction", tone: "text-conflict-ink" })),
      ...guidance.warnings.map((node) => ({ node, kind: "warning", tone: "text-warn-ink" })),
      ...guidance.similar.map((node) => ({ node, kind: "similar", tone: "text-ink-muted" })),
    ].slice(0, 3);
    return (
      <div className="flex flex-col gap-2">
        {rows.length === 0 ? (
          <p className="text-small text-ink-muted">
            Nothing yet: no prior incident is above the relevance floor, and
            nothing said on the bridge contradicts what is measured.
          </p>
        ) : (
          <ul className="flex flex-col">
            {rows.map(({ node, kind, tone }) => {
              const source = String(node.detail.source_reference ?? "");
              const relevance = Number(node.detail.relevance ?? 0);
              return (
                <li key={node.id} className="border-b border-line py-2 text-small last:border-b-0">
                  <span className={`text-nano font-bold uppercase tracking-label ${tone}`}>
                    {kind}
                  </span>
                  {source && <span className="ml-1.5 font-mono text-tiny text-ink-muted">{source}</span>}
                  {relevance > 0 && (
                    <span className="ml-1.5 text-tiny text-ink-muted">
                      {Math.round(relevance * 100)}% relevance
                    </span>
                  )}
                  <p className="mt-0.5">{node.label}</p>
                </li>
              );
            })}
          </ul>
        )}
        <p className="text-tiny text-ink-muted">{guidance.caveat}</p>
      </div>
    );
  }

  return (
    <div className="space-y-2">
      <p className="border border-line px-2 py-1 text-[11px] text-ink-muted">
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
                <span className="ml-1 border border-line px-1 text-[10px] text-ink-muted">
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
