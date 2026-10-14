"use client";

import { useCallback, useEffect, useState } from "react";

/** What selecting text does: show similar content at once ("show"), or offer a Similar button to open it ("button"). */
export type SelectMode = "show" | "button";
const KEY = "lens.similar.select";
const EVENT = "lens:similar-select";

function read(): SelectMode {
  try {
    return localStorage.getItem(KEY) === "button" ? "button" : "show";
  } catch {
    return "show";
  }
}

/** The person's choice for this browser (the account menu switches it), kept in step across the page. */
export function useSelectMode(): [SelectMode, (m: SelectMode) => void] {
  const [mode, setModeState] = useState<SelectMode>("show");
  useEffect(() => {
    setModeState(read());
    const on = () => setModeState(read());
    window.addEventListener(EVENT, on);
    window.addEventListener("storage", on);
    return () => {
      window.removeEventListener(EVENT, on);
      window.removeEventListener("storage", on);
    };
  }, []);
  const setMode = useCallback((m: SelectMode) => {
    try {
      if (m === "show") localStorage.removeItem(KEY);
      else localStorage.setItem(KEY, m);
    } catch {
      /* storage unavailable: this page keeps the choice */
    }
    setModeState(m);
    window.dispatchEvent(new CustomEvent(EVENT));
  }, []);
  return [mode, setMode];
}
