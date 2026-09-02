"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError } from "@/lib/api/client";
import { getReplay, type Replay } from "@/features/audit/api";

export type ReplayState =
  | { kind: "loading" }
  | { kind: "ready"; replay: Replay }
  | { kind: "error"; message: string };

/**
 * The replay, and the scrubber's position.
 *
 * The scrubber is a *server* parameter, not a client filter. `?until_seq=` is
 * the same code path with a different bound, so a scrubbed view is a byte-prefix
 * of the whole rather than a subset this screen chose - which is what makes
 * "the situation as it was" a claim the platform can stand behind.
 *
 * The full replay is fetched once so the scrubber knows its own range, and the
 * bounded one is fetched as the reader drags. The last request wins: dragging
 * fast issues several, and a slow early response arriving after a fast late one
 * would otherwise show the wrong instant.
 */
export function useReplay(incidentId: string) {
  const [full, setFull] = useState<Replay | null>(null);
  const [state, setState] = useState<ReplayState>({ kind: "loading" });
  const [seq, setSeq] = useState<number | null>(null);
  const request = useRef(0);

  const load = useCallback(
    async (untilSeq?: number) => {
      const ticket = ++request.current;
      try {
        const replay = await getReplay(incidentId, untilSeq);
        if (ticket !== request.current) return;
        setState({ kind: "ready", replay });
        if (untilSeq === undefined) {
          setFull(replay);
          setSeq(replay.to_seq);
        }
      } catch (caught) {
        if (ticket !== request.current) return;
        setState({
          kind: "error",
          message:
            caught instanceof ApiError
              ? caught.message
              : "The audit replay could not be read.",
        });
      }
    },
    [incidentId],
  );

  useEffect(() => {
    void load();
  }, [load]);

  const scrub = useCallback(
    (to: number) => {
      setSeq(to);
      void load(to);
    },
    [load],
  );

  return { state, full, seq, scrub, reload: () => void load() };
}
