"use client";

import { useState } from "react";

import { ApiError } from "@/lib/api/client";
import { Answer, BrainNode, askCopilot } from "@/features/brain/api";

/**
 * S18 — IC Copilot (REQ-055).
 *
 * Ask a question about this incident; get an answer drawn only from its graph,
 * with a chip per citation that resolves to the node it came from.
 *
 * The verdict badge is the most important element on the panel and it is
 * rendered before the prose, because the four states mean genuinely different
 * things to an operator:
 *
 * - **grounded** — every claim resolves to a node in this incident's record.
 * - **insufficient evidence** — the record does not answer it, and the assistant
 *   said so rather than filling the gap.
 * - **ungrounded** — the model answered and cited something that does not exist,
 *   so the answer was *discarded*. The panel shows the refusal, never the
 *   discarded text: an ungrounded answer displayed with a warning is displayed.
 * - **degraded** — no model. The record is listed instead, with citations.
 *
 * The suggested questions are not decoration. An empty box invites "what should
 * I do", which is a question no grounded assistant can answer; the suggestions
 * point at questions the graph can actually settle.
 */
const SUGGESTED = [
  "What do we know so far?",
  "What changed just before this started?",
  "Which services are confirmed affected, and which are only predicted?",
  "What is the strongest hypothesis and what would disprove it?",
];

export function CopilotPanel({
  incidentId,
  nodes,
}: {
  incidentId: string;
  nodes: BrainNode[];
}) {
  const [question, setQuestion] = useState("");
  const [asking, setAsking] = useState(false);
  const [answer, setAnswer] = useState<Answer | null>(null);
  const [error, setError] = useState<string | null>(null);

  const byId = new Map(nodes.map((node) => [node.id, node]));

  async function ask(text: string) {
    const asked = text.trim();
    if (!asked) return;
    setAsking(true);
    setError(null);
    try {
      setAnswer(await askCopilot(incidentId, asked));
      setQuestion("");
    } catch (caught) {
      setError(
        caught instanceof ApiError
          ? caught.message
          : "The copilot could not be reached",
      );
    } finally {
      setAsking(false);
    }
  }

  return (
    <div className="space-y-2">
      <form
        onSubmit={(event) => {
          event.preventDefault();
          void ask(question);
        }}
        className="flex gap-1.5"
      >
        <input
          value={question}
          onChange={(event) => setQuestion(event.target.value)}
          placeholder="Ask about this incident…"
          aria-label="Ask the incident copilot"
          maxLength={500}
          className="flex-1 rounded-card border border-line bg-surface px-2 py-1 text-xs"
        />
        <button
          type="submit"
          disabled={asking || !question.trim()}
          className="rounded-card bg-accent px-2.5 py-1 text-xs font-medium text-surface-raised hover:bg-accent-strong disabled:opacity-60"
        >
          {asking ? "…" : "Ask"}
        </button>
      </form>

      {!answer && !asking && (
        <ul className="flex flex-wrap gap-1">
          {SUGGESTED.map((suggestion) => (
            <li key={suggestion}>
              <button
                type="button"
                onClick={() => void ask(suggestion)}
                className="rounded-full border border-line px-2 py-0.5 text-[11px] text-ink-muted hover:border-accent hover:text-accent-strong"
              >
                {suggestion}
              </button>
            </li>
          ))}
        </ul>
      )}

      {error && <p className="text-[11px] text-danger">{error}</p>}

      {answer && (
        <article className="space-y-1.5 rounded-card border border-line bg-surface p-2">
          <header className="flex flex-wrap items-center gap-1.5">
            <Verdict verdict={answer.verdict} />
            <span className="text-[11px] text-ink-muted">{answer.question}</span>
            <span className="flex-1" />
            <span className="text-[10px] text-ink-muted">
              {answer.considered} node{answer.considered === 1 ? "" : "s"} read
              {answer.model ? ` · ${answer.model}` : ""}
            </span>
          </header>

          <p className="whitespace-pre-wrap text-xs">{answer.answer}</p>

          {answer.injection_observed && (
            <p className="rounded border border-danger/40 bg-danger/10 px-2 py-1 text-[11px] text-danger">
              Content in this incident tried to instruct the assistant. It was
              treated as data and nothing in it was acted on — the copilot holds
              no tools and can change no incident state.
            </p>
          )}

          {answer.citations.length > 0 && (
            <ul className="flex flex-wrap gap-1">
              {answer.citations.map((citation) => {
                const node = byId.get(citation);
                return (
                  <li
                    key={citation}
                    title={
                      node
                        ? `${node.node_type} · ${node.provenance.authority} · ${node.provenance.source}${
                            node.provenance.result_ref
                              ? ` · ${node.provenance.result_ref}`
                              : ""
                          }`
                        : citation
                    }
                    className="rounded-full border border-line px-2 py-0.5 text-[10px]"
                  >
                    {node ? node.label.slice(0, 48) : citation}
                    {node?.confirmation === "predicted" && (
                      <span className="ml-1 text-warn">predicted</span>
                    )}
                  </li>
                );
              })}
            </ul>
          )}

          {answer.caveats.length > 0 && (
            <ul className="space-y-0.5 border-t border-line pt-1 text-[11px] text-warn">
              {answer.caveats.map((caveat) => (
                <li key={caveat}>{caveat}</li>
              ))}
            </ul>
          )}
        </article>
      )}
    </div>
  );
}

function Verdict({ verdict }: { verdict: Answer["verdict"] }) {
  const map: Record<Answer["verdict"], { label: string; className: string }> = {
    grounded: {
      label: "grounded",
      className: "border-ok/50 text-ok",
    },
    insufficient_evidence: {
      label: "insufficient evidence",
      className: "border-warn/50 text-warn",
    },
    ungrounded: {
      label: "answer discarded — not grounded",
      className: "border-danger/50 text-danger",
    },
    degraded: {
      label: "AI degraded — showing the record",
      className: "border-warn/50 text-warn",
    },
  };
  const chip = map[verdict];
  return (
    <span
      className={`rounded-full border px-2 py-0.5 text-[10px] uppercase ${chip.className}`}
    >
      {chip.label}
    </span>
  );
}
