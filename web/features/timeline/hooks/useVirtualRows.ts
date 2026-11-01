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
  // A callback ref rather than a ref object: the list is rendered only once
  // the timeline has loaded, which is after this hook's first effect has run.
  // An effect with no dependencies saw `ref.current === null` on mount and
  // never ran again, so no scroll listener was ever attached and the window
  // stayed on the first rows however far the person scrolled.
  const [element, setElement] = useState<HTMLDivElement | null>(null);
  const elementRef = useRef<HTMLDivElement | null>(null);
  const ref = useCallback((node: HTMLDivElement | null) => {
    elementRef.current = node;
    setElement(node);
  }, []);
  const [scrollTop, setScrollTop] = useState(0);
  const [viewportHeight, setViewportHeight] = useState(480);

  useEffect(() => {
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
  }, [element]);

  const first = Math.max(0, Math.floor(scrollTop / rowHeight) - OVERSCAN);
  const visibleCount = Math.ceil(viewportHeight / rowHeight) + OVERSCAN * 2;
  const last = Math.min(count, first + visibleCount);

  const scrollToIndex = useCallback(
    (index: number) => {
      const target = elementRef.current;
      if (!target) return;
      target.scrollTop = Math.max(0, index * rowHeight);
      setScrollTop(target.scrollTop);
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
