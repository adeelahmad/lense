"use client";

import { useEffect, useState } from "react";

/**
 * While there are unsaved changes, leaving asks first (Settings ST1: "Keep editing / Discard"): in-app links are held
 * until you choose, and closing or reloading the tab gets the browser's own prompt.
 * Returns the held destination (or null) and a way to clear it.
 */
export function useUnsavedGuard(dirty: boolean): [string | null, (v: string | null) => void] {
  const [held, setHeld] = useState<string | null>(null);
  useEffect(() => {
    if (!dirty) return;
    const beforeUnload = (e: BeforeUnloadEvent) => {
      e.preventDefault();
      e.returnValue = "";
    };
    const click = (e: MouseEvent) => {
      if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
      const a = (e.target as Element | null)?.closest?.("a[href]") as HTMLAnchorElement | null;
      if (!a || a.target === "_blank" || a.hasAttribute("download")) return;
      const url = new URL(a.href, window.location.href);
      if (url.origin !== window.location.origin) return;
      if (url.pathname === window.location.pathname && url.search === window.location.search) return;
      e.preventDefault();
      e.stopPropagation();
      setHeld(url.pathname + url.search + url.hash);
    };
    window.addEventListener("beforeunload", beforeUnload);
    document.addEventListener("click", click, true);
    return () => {
      window.removeEventListener("beforeunload", beforeUnload);
      document.removeEventListener("click", click, true);
    };
  }, [dirty]);
  return [held, setHeld];
}
