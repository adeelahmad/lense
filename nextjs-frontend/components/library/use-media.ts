"use client";

import { useEffect, useState } from "react";

/** Whether the viewport is phone-sized (the design's 390px frames); false until mounted, or without matchMedia. */
export function useIsNarrow(query = "(max-width: 767px)"): boolean {
  const [narrow, setNarrow] = useState(false);
  useEffect(() => {
    if (typeof window.matchMedia !== "function") return; // e.g. jsdom: laid out wide
    const m = window.matchMedia(query);
    const on = () => setNarrow(m.matches);
    on();
    m.addEventListener("change", on);
    return () => m.removeEventListener("change", on);
  }, [query]);
  return narrow;
}
