"use client";

import { namespaceOf } from "@/features/timeline/store";

/**
 * Filter chips over the topics actually present in the loaded window, plus one
 * wildcard chip per namespace. Filtering is client-side over what is loaded;
 * narrowing the server-side query is a separate control, because an operator
 * filtering a live timeline expects the events they already have to stay.
 */
export function TopicFilter({
  topics,
  selected,
  onChange,
}: {
  topics: string[];
  selected: string[];
  onChange: (next: string[]) => void;
}) {
  const namespaces = Array.from(new Set(topics.map(namespaceOf))).sort();
  const toggle = (value: string) =>
    onChange(
      selected.includes(value)
        ? selected.filter((item) => item !== value)
        : [...selected, value],
    );

  return (
    <div className="flex flex-wrap items-center gap-1.5">
      <button
        onClick={() => onChange([])}
        className={`border px-2.5 py-1 text-xs ${
          selected.length === 0
            ? "border-accent bg-accent/10 text-accent-strong"
            : "border-line text-ink-muted hover:text-ink"
        }`}
      >
        All ({topics.length})
      </button>
      {namespaces.map((namespace) => {
        const pattern = `${namespace}.*`;
        return (
          <button
            key={pattern}
            onClick={() => toggle(pattern)}
            className={`border px-2.5 py-1 text-xs ${
              selected.includes(pattern)
                ? "border-accent bg-accent/10 text-accent-strong"
                : "border-line text-ink-muted hover:text-ink"
            }`}
          >
            {pattern}
          </button>
        );
      })}
      {topics.map((topic) => (
        <button
          key={topic}
          onClick={() => toggle(topic)}
          className={`border px-2.5 py-1 text-xs font-mono ${
            selected.includes(topic)
              ? "border-accent bg-accent/10 text-accent-strong"
              : "border-line text-ink-muted hover:text-ink"
          }`}
        >
          {topic}
        </button>
      ))}
    </div>
  );
}
