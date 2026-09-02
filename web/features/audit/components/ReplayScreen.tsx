"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { ApiError } from "@/lib/api/client";
import {
  generatePir,
  getPir,
  type Lesson,
  type Pir,
  type ReplayEntry,
} from "@/features/audit/api";
import { useReplay } from "@/features/audit/hooks/useReplay";
import { Badge, EmptyState, Pill } from "@/lib/ui/primitives";
import { Button } from "@/lib/ui/patterns";

/**
 * S20 - the closed incident: audit replay and the post-incident review.
 *
 * Everything on this screen is derived. The replay is reconstructed from the
 * hash-chained audit log and the review is generated from the replay, so
 * nothing here is a document somebody saved and could have edited. That is the
 * point of the screen and it is why the integrity banner is at the top rather
 * than in a footnote: if the chain does not verify, every line below it is
 * suspect, and the platform says so before showing them.
 *
 * The six columns are the six questions an auditor actually asks, and they are
 * the ones the replay answers rather than six the screen invented: what
 * happened, when, who did it, what justified it, what changed, and what can be
 * opened from here. A row that cannot answer one of them shows an em dash
 * rather than a blank, because "we did not record that" and "nothing changed"
 * are different findings.
 */

const COLUMNS = [
  "When",
  "What happened",
  "Who",
  "What justified it",
  "What changed",
  "Open",
] as const;

function short(hash: string): string {
  return hash.length > 16 ? `${hash.slice(0, 10)}…${hash.slice(-4)}` : hash;
}

/**
 * The situation model at the scrubbed instant, counted from the entries the
 * replay returned rather than from a second source. A count computed here from
 * a different read could disagree with the rows underneath it, and a screen that
 * disagrees with itself is worse than one that shows less.
 */
function situation(entries: ReplayEntry[]) {
  const count = (prefix: string) =>
    entries.filter((entry) => entry.topic.startsWith(prefix)).length;
  return [
    { label: "brain", value: count("brain.") },
    { label: "decisions", value: count("incident.") },
    { label: "actions", value: count("action.") },
    { label: "approvals", value: count("approval.") },
    { label: "comms", value: count("comms.") },
    { label: "paging", value: count("warroom.page") },
  ];
}

export function ReplayScreen({ incidentId }: { incidentId: string }) {
  const { state, full, seq, scrub } = useReplay(incidentId);
  const [pir, setPir] = useState<Pir | null>(null);
  const [pirError, setPirError] = useState<string | null>(null);
  const [generating, setGenerating] = useState(false);

  useEffect(() => {
    void getPir(incidentId)
      .then((review) => {
        setPir(review);
        setPirError(null);
      })
      .catch((caught) => {
        setPir(null);
        setPirError(
          caught instanceof ApiError
            ? caught.message
            : "The review could not be read.",
        );
      });
  }, [incidentId]);

  async function generate() {
    setGenerating(true);
    setPirError(null);
    try {
      setPir(await generatePir(incidentId));
    } catch (caught) {
      setPirError(
        caught instanceof ApiError
          ? caught.message
          : "The review could not be generated.",
      );
    } finally {
      setGenerating(false);
    }
  }

  if (state.kind === "loading") {
    return <p className="text-ink-muted">Reading the sealed record…</p>;
  }
  if (state.kind === "error") {
    return (
      <p className="border border-accent bg-brand-100 px-3 py-2 text-accent-strong">
        {state.message}
      </p>
    );
  }

  const { replay } = state;
  const chain = replay.chain;
  const latest = replay.entries[replay.entries.length - 1];
  const min = full?.from_seq ?? replay.from_seq;
  const max = full?.to_seq ?? replay.to_seq;
  const seal = replay.seals[replay.seals.length - 1];

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-wrap items-baseline gap-4">
        <h6 className="m-0 text-ink-muted">Audit replay &amp; review</h6>
        <span className="font-mono text-dense">
          {replay.count} entr{replay.count === 1 ? "y" : "ies"} · seq {replay.from_seq}–
          {replay.to_seq}
          {seal ? ` · sealed ${short(seal.seal_hash)}` : ""}
        </span>
        <Link
          href={`/incidents/${encodeURIComponent(incidentId)}`}
          className="ml-auto text-small font-semibold text-accent hover:underline"
        >
          ← War room
        </Link>
      </div>

      {/* The integrity banner. First, and never a footnote. */}
      <div
        className={`flex flex-wrap items-baseline gap-3 border px-4 py-3 ${
          chain.valid
            ? "border-ok bg-ok-bg text-ok"
            : "border-accent bg-brand-100 text-accent-strong"
        }`}
      >
        <Badge label={chain.valid ? "VERIFIED" : "CONTRADICTED"} size="micro" />
        <strong className="text-dense">
          {chain.valid
            ? `The hash chain verifies over ${chain.checked} entries.`
            : `The hash chain does not verify. First broken seq: ${chain.first_broken_seq ?? "unknown"}.`}
        </strong>
        <span className="text-small">{chain.reason}</span>
        {!chain.valid && chain.broken_seqs.length > 1 && (
          <span className="font-mono text-small">
            broken at {chain.broken_seqs.join(", ")}
          </span>
        )}
      </div>

      {/* The scrubber. `?until_seq=` is a server parameter, so what is drawn
          below is a byte-prefix of the whole record rather than a filtered view
          this screen chose. */}
      <section aria-label="Replay scrubber" className="flex flex-col gap-3 bg-surface-raised p-4">
        <div className="flex flex-wrap items-baseline justify-between gap-3">
          <span className="text-tiny uppercase tracking-panel text-accent">
            Replay — drag to re-render the record as it stood
          </span>
          <span className="font-mono text-dense">
            {latest ? latest.at.slice(11, 19) : "--:--:--"}
          </span>
        </div>
        <input
          type="range"
          min={min}
          max={max}
          value={seq ?? max}
          onChange={(event) => scrub(Number(event.target.value))}
          className="w-full accent-accent"
          aria-label="Audit sequence"
        />
        <div className="flex justify-between font-mono text-micro text-ink-muted">
          <span>seq {min}</span>
          <span>seq {seq ?? max}</span>
          <span>seq {max}</span>
        </div>
        {latest && (
          <p className="text-h5">{latest.headline}</p>
        )}
        <dl className="grid grid-cols-2 gap-2 border-t border-line pt-3 sm:grid-cols-3 lg:grid-cols-6">
          {situation(replay.entries).map((item) => (
            <div key={item.label} className="flex flex-col">
              <dt className="text-nano uppercase tracking-panel text-neutral-600">
                {item.label}
              </dt>
              <dd className="font-display text-h4 font-extrabold leading-none tabular-nums">
                {item.value}
              </dd>
            </div>
          ))}
        </dl>
      </section>

      <section aria-label="Audit timeline" className="flex flex-col gap-2">
        <h2 className="text-h6 uppercase tracking-label text-ink-muted">
          The record, line by line
        </h2>
        {replay.entries.length === 0 ? (
          <EmptyState>
            Nothing has been written to this incident&rsquo;s chain yet.
          </EmptyState>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[900px] text-small">
              <thead>
                <tr>
                  {COLUMNS.map((column) => (
                    <th
                      key={column}
                      className="border-b-2 border-line px-2 py-2 text-left text-micro uppercase tracking-label text-ink-soft"
                    >
                      {column}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {[...replay.entries].reverse().map((entry) => (
                  <tr
                    key={entry.seq}
                    className={
                      chain.broken_seqs.includes(entry.seq) ? "bg-conflict" : ""
                    }
                  >
                    <td className="border-b border-line px-2 py-2 align-top font-mono text-ink-muted">
                      {entry.at.slice(11, 19)}
                      <span className="block text-nano">#{entry.seq}</span>
                    </td>
                    <td className="border-b border-line px-2 py-2 align-top">
                      <span className="font-semibold">{entry.headline}</span>
                      <span className="block font-mono text-nano text-neutral-600">
                        {entry.topic}
                      </span>
                    </td>
                    <td className="border-b border-line px-2 py-2 align-top">
                      {entry.actor_label || entry.actor_id}
                      <span className="block text-nano uppercase tracking-label text-neutral-600">
                        {entry.actor_type}
                      </span>
                    </td>
                    <td className="border-b border-line px-2 py-2 align-top">
                      {entry.justification.length === 0 ? (
                        <span className="text-neutral-500">—</span>
                      ) : (
                        <ul className="flex flex-col gap-0.5">
                          {entry.justification.map((line) => (
                            <li key={line}>{line}</li>
                          ))}
                        </ul>
                      )}
                    </td>
                    <td className="border-b border-line px-2 py-2 align-top">
                      {entry.effect || entry.consequence || (
                        <span className="text-neutral-500">—</span>
                      )}
                    </td>
                    <td className="border-b border-line px-2 py-2 align-top font-mono text-nano">
                      {entry.references.length === 0 ? (
                        <span className="text-neutral-500">—</span>
                      ) : (
                        entry.references.join(", ")
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <section aria-label="Post-incident review" className="flex flex-col gap-3">
        <div className="flex flex-wrap items-baseline gap-3">
          <h2 className="text-h6 uppercase tracking-label text-ink-muted">
            Post-incident review
          </h2>
          {pir && (
            <>
              <Pill size="tiny" className="border border-neutral-600 text-neutral-700">
                {pir.verified_lesson_count} verified
              </Pill>
              <Pill size="tiny" className="border border-neutral-600 text-neutral-700">
                {pir.proposed_lesson_count} proposed
              </Pill>
              {pir.degraded && <Badge label="DEGRADED" size="tiny" />}
            </>
          )}
          <span className="ml-auto">
            <Button
              variant="secondary"
              size="small"
              disabled={generating}
              onClick={() => void generate()}
            >
              {pir ? "Regenerate" : "Generate the review"}
            </Button>
          </span>
        </div>

        {/* The refusal is rendered, not hidden. A review is the artefact people
            quote six months later, so it is not produced at all from records
            that no longer match their hashes - and saying so is more useful
            than an empty page. */}
        {!pir && pirError && (
          <div className="border border-warn bg-warn-bg px-4 py-3 text-dense text-warn-ink">
            <strong className="font-semibold">No review.</strong> {pirError}
            {!chain.valid && (
              <p className="mt-1">
                The chain does not verify, and a review is refused outright rather
                than generated with a warning on it. Repair the record first: a
                banner is not enough for the document somebody quotes six months
                from now.
              </p>
            )}
          </div>
        )}

        {pir && (
          <div className="grid items-start gap-6 lg:grid-cols-[1.4fr_1fr]">
            <div className="flex flex-col gap-4 bg-surface-raised p-4">
              {pir.sections.map((section) => (
                <div key={section.key} className="flex flex-col gap-1">
                  <h3 className="text-h6 uppercase tracking-label">{section.title}</h3>
                  {section.lines.length === 0 ? (
                    <p className="text-small text-neutral-500">
                      Nothing in the record answers this.
                    </p>
                  ) : (
                    <ul className="flex flex-col gap-0.5 text-dense">
                      {section.lines.map((line, index) => (
                        <li key={`${section.key}-${index}`}>{line}</li>
                      ))}
                    </ul>
                  )}
                  {section.citations.length > 0 && (
                    <p className="font-mono text-nano text-neutral-600">
                      {section.citations.map((citation) => citation.ref).join(" · ")}
                    </p>
                  )}
                </div>
              ))}
            </div>

            <div className="flex flex-col gap-3 bg-surface-raised p-4">
              <span className="text-tiny uppercase tracking-panel text-accent">
                Lessons — a lesson is verified or it is not a lesson
              </span>
              {pir.lessons.length === 0 ? (
                <EmptyState>
                  Nothing has been learned from this incident yet. A lesson becomes
                  VERIFIED only when every citation resolves to a record this
                  incident produced.
                </EmptyState>
              ) : (
                <ul className="flex flex-col">
                  {pir.lessons.map((lesson) => (
                    <LessonRow key={lesson.id} lesson={lesson} />
                  ))}
                </ul>
              )}
              {pir.followups.length > 0 && (
                <div className="border-t border-line pt-2">
                  <h3 className="text-h6 uppercase tracking-label text-ink-muted">
                    Follow-ups
                  </h3>
                  <ul className="flex flex-col gap-0.5 text-dense">
                    {pir.followups.map((item) => (
                      <li key={item}>{item}</li>
                    ))}
                  </ul>
                </div>
              )}
            </div>
          </div>
        )}
      </section>
    </div>
  );
}

function LessonRow({ lesson }: { lesson: Lesson }) {
  const verified = lesson.status === "verified";
  return (
    <li className="flex flex-col gap-1 border-t border-line py-2.5 first:border-t-0">
      <div className="flex items-start gap-2">
        <Badge label={verified ? "VERIFIED" : "PROPOSED"} size="nano" />
        <span className={`text-dense ${verified ? "" : "text-ink-muted"}`}>
          {lesson.text}
        </span>
      </div>
      {verified ? (
        <p className="font-mono text-nano text-neutral-600">
          {lesson.citations.map((citation) => citation.ref).join(" · ")}
        </p>
      ) : (
        <p className="font-mono text-nano text-warn-ink">
          unresolved: {lesson.unresolved.join(", ") || "no citation was offered"}
        </p>
      )}
    </li>
  );
}
