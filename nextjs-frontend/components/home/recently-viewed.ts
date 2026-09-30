"use client";

import { useEffect, useState } from "react";

/**
 * "Recently viewed" on Home. The archive doesn't record what each person opened, so this is a per-browser convenience
 * kept in localStorage. Any screen can call `rememberView` when someone opens a recording, speaker or chat.
 */
export type ViewedKind = "recording" | "speaker" | "chat" | "report";
export type Viewed = {
  kind: ViewedKind;
  href: string;
  title: string;
  at: number;
};

const KEY = "lens.recent";
const MAX = 8;
const EVENT = "lens:recent";

export function readViews(): Viewed[] {
  try {
    const raw = JSON.parse(localStorage.getItem(KEY) || "[]");
    return Array.isArray(raw)
      ? raw.filter((v) => v && typeof v.href === "string" && typeof v.title === "string").slice(0, MAX)
      : [];
  } catch {
    return [];
  }
}

export function rememberView(v: Omit<Viewed, "at">): void {
  try {
    const next = [{ ...v, at: Date.now() }, ...readViews().filter((x) => x.href !== v.href)].slice(0, MAX);
    localStorage.setItem(KEY, JSON.stringify(next));
    window.dispatchEvent(new Event(EVENT));
  } catch {
    /* storage unavailable: nothing to remember */
  }
}

export function useRecentViews(): Viewed[] {
  const [views, setViews] = useState<Viewed[]>([]);
  useEffect(() => {
    const load = () => setViews(readViews());
    load();
    window.addEventListener(EVENT, load);
    window.addEventListener("storage", load);
    return () => {
      window.removeEventListener(EVENT, load);
      window.removeEventListener("storage", load);
    };
  }, []);
  return views;
}
