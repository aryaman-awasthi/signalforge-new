"use client";

import { useCallback, useEffect, useRef, useState } from "react";

/**
 * Fixed-height windowing. An incident timeline runs to tens of thousands of
 * events and the replay scrubber will run over all of them, so only the rows
 * near the viewport are mounted; the rest is two spacer divs.
 *
 * Fixed row height rather than measured: it makes scrollTop -> index exact,
 * which is what lets the seq anchor jump straight to a position without
 * rendering everything above it first.
 */
export const ROW_HEIGHT = 56;
const OVERSCAN = 8;

export function useVirtualRows(count: number, rowHeight: number = ROW_HEIGHT) {
  const ref = useRef<HTMLDivElement | null>(null);
  const [scrollTop, setScrollTop] = useState(0);
  const [viewportHeight, setViewportHeight] = useState(480);

  useEffect(() => {
    const element = ref.current;
    if (!element) return;
    const onScroll = () => setScrollTop(element.scrollTop);
    const measure = () => setViewportHeight(element.clientHeight || 480);

    measure();
    element.addEventListener("scroll", onScroll, { passive: true });
    window.addEventListener("resize", measure);
    return () => {
      element.removeEventListener("scroll", onScroll);
      window.removeEventListener("resize", measure);
    };
  }, []);

  const first = Math.max(0, Math.floor(scrollTop / rowHeight) - OVERSCAN);
  const visibleCount = Math.ceil(viewportHeight / rowHeight) + OVERSCAN * 2;
  const last = Math.min(count, first + visibleCount);

  const scrollToIndex = useCallback(
    (index: number) => {
      const element = ref.current;
      if (!element) return;
      element.scrollTop = Math.max(0, index * rowHeight);
      setScrollTop(element.scrollTop);
    },
    [rowHeight],
  );

  return {
    ref,
    first,
    last,
    padTop: first * rowHeight,
    padBottom: Math.max(0, (count - last) * rowHeight),
    rowHeight,
    scrollToIndex,
  };
}
