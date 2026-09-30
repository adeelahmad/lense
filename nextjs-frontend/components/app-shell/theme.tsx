"use client";

import { useCallback, useEffect, useState } from "react";

export type ThemeChoice = "light" | "dark" | "system";
const KEY = "lens.theme";

/** Applied before first paint (in the root layout) so there's no flash of the wrong theme. */
export const THEME_SCRIPT = `try{var t=localStorage.getItem("${KEY}");if(t==="light"||t==="dark")document.documentElement.dataset.theme=t}catch(e){}`;

export function useTheme(): [ThemeChoice, (t: ThemeChoice) => void] {
  const [theme, setThemeState] = useState<ThemeChoice>("system");
  useEffect(() => {
    try {
      const t = localStorage.getItem(KEY);
      if (t === "light" || t === "dark") setThemeState(t);
    } catch {
      /* storage unavailable */
    }
  }, []);
  const setTheme = useCallback((t: ThemeChoice) => {
    setThemeState(t);
    const el = document.documentElement;
    if (t === "system") delete el.dataset.theme;
    else el.dataset.theme = t;
    try {
      if (t === "system") localStorage.removeItem(KEY);
      else localStorage.setItem(KEY, t);
    } catch {
      /* storage unavailable */
    }
  }, []);
  return [theme, setTheme];
}
