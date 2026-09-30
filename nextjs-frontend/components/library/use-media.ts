"use client";

import { useEffect, useState } from "react";

/** Whether the viewport is phone-sized (the design's 390px frames); false until mounted. */
export function useIsNarrow(query = "(max-width: 767px)"): boolean {
  const [narrow, setNarrow] = useState(false);
  useEffect(() => {
    const m = window.matchMedia(query);
    const on = () => setNarrow(m.matches);
    on();
    m.addEventListener("change", on);
    return () => m.removeEventListener("change", on);
  }, [query]);
  return narrow;
}
