"use client";

import { useEffect } from "react";

import { EventRow } from "@/features/timeline/components/EventRow";
import { SeqAnchorBar } from "@/features/timeline/components/SeqAnchorBar";
import { TopicFilter } from "@/features/timeline/components/TopicFilter";
import { useTimeline } from "@/features/timeline/hooks/useTimeline";
import { useVirtualRows } from "@/features/timeline/hooks/useVirtualRows";
import { indexOfSeq } from "@/features/timeline/store";
import type { Plane } from "@/features/timeline/api";

/**
 * The timeline primitive the war room, the settings screen and the replay
 * scrubber all reuse: a virtualised event list, topic filters and a seq anchor
 * over one replay query.
 */
export function EventTimeline({
  incidentId,
  plane,
  height = 480,
}: {
  incidentId?: string;
  plane?: Plane;
  height?: number;
}) {
  const timeline = useTimeline({ incidentId, plane });
  const rows = useVirtualRows(timeline.events.length);
  const { anchorSeq, events } = timeline;
  const { scrollToIndex } = rows;

  useEffect(() => {
    if (anchorSeq !== null && events.length > 0) {
      scrollToIndex(indexOfSeq(events, anchorSeq));
    }
  }, [anchorSeq, events, scrollToIndex]);

  if (timeline.status === "loading") {
    return <p className="text-ink-muted">Loading timeline…</p>;
  }
  if (timeline.status === "error") {
    return (
      <p className="rounded-card border border-danger/40 bg-danger/10 px-3 py-2 text-danger">
        {timeline.message}
      </p>
    );
  }

  return (
    <div className="space-y-3">
      <TopicFilter
        topics={timeline.availableTopics}
        selected={timeline.selectedTopics}
        onChange={timeline.setSelectedTopics}
      />
      <SeqAnchorBar
        anchorSeq={timeline.anchorSeq}
        lastSeq={timeline.lastSeq}
        shown={timeline.events.length}
        total={timeline.total}
        onAnchor={timeline.anchor}
      />
      <div
        ref={rows.ref}
        style={{ height }}
        className="overflow-y-auto rounded-card border border-line bg-surface-raised"
        data-testid="event-list"
      >
        {timeline.events.length === 0 ? (
          <p className="px-3 py-6 text-sm text-ink-muted">
            No events match this filter yet.
          </p>
        ) : (
          <>
            <div style={{ height: rows.padTop }} />
            {timeline.events.slice(rows.first, rows.last).map((event) => (
              <EventRow
                key={event.event_id}
                event={event}
                anchored={event.seq === timeline.anchorSeq}
                onAnchor={timeline.anchor}
              />
            ))}
            <div style={{ height: rows.padBottom }} />
          </>
        )}
      </div>
      {timeline.hasMore && (
        <button
          onClick={() => void timeline.loadMore()}
          className="rounded-card border border-line px-3 py-1.5 text-sm hover:border-accent hover:text-accent-strong"
        >
          Load older history (from #{timeline.lastSeq})
        </button>
      )}
    </div>
  );
}
