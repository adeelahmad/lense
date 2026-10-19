import type { Clip, Playlist } from "@/app/openapi-client/types.gen";
import { tc } from "@/lib/format";

/** The playlist a clip goes in by default: your latest used one (the list has yours first, the latest used first). */
export function defaultPlaylist<P extends Pick<Playlist, "id" | "mine">>(list: P[] | undefined): P | null {
  return list?.find((p) => p.mine) ?? null;
}

/** The name a first playlist gets when you keep a clip and have none (as the API's agents get). */
export const FIRST_PLAYLIST = "Clips";

/** A clip's title: its own, else the resource's with where it starts. */
export function clipTitle(c: Pick<Clip, "title" | "recording_title" | "recording" | "t0">): string {
  return c.title || `${c.recording_title || `Asset ${c.recording}`} at ${tc(c.t0)}`;
}

/** A title for a clip of selected words: the words, cut to a short line. */
export function titleFromQuote(quote: string, most = 80): string {
  const q = quote.replace(/\s+/g, " ").trim();
  return q.length > most ? `${q.slice(0, most - 1).trimEnd()}…` : q;
}

/** How long clips play back to back, in ms. */
export function totalMs(items: Pick<Clip, "t0" | "t1">[]): number {
  return items.reduce((n, c) => n + Math.max(0, c.t1 - c.t0), 0);
}

/** The next clip to play after `index` that has media; null at the end. Clips of text (no audio) are skipped. */
export function nextPlayable(items: Pick<Clip, "audio">[], index: number): number | null {
  for (let i = index + 1; i < items.length; i++) if (items[i].audio) return i;
  return null;
}

/** The first clip with media at or after `index`; null when none is. */
export function firstPlayable(items: Pick<Clip, "audio">[], index = 0): number | null {
  return index < items.length && items[index]?.audio ? index : nextPlayable(items, index);
}

/** Whether the clip playing has reached its end (ms into the resource), so the player goes on to the next. */
export function reachedEnd(c: Pick<Clip, "t1">, ms: number): boolean {
  return ms >= c.t1 - 50; // a frame's slack: timeupdate fires a few times a second
}

/** The list with the item at `from` moved to `to` (both clamped). */
export function moveTo<T>(items: T[], from: number, to: number): T[] {
  if (from < 0 || from >= items.length) return items;
  const at = Math.max(0, Math.min(to, items.length - 1));
  if (at === from) return items;
  const next = items.slice();
  const [it] = next.splice(from, 1);
  next.splice(at, 0, it);
  return next;
}

/** The list with item `id` moved up (-1) or down (+1). */
export function moveBy<T extends { id: number }>(items: T[], id: number, delta: -1 | 1): T[] {
  const i = items.findIndex((x) => x.id === id);
  return i < 0 ? items : moveTo(items, i, i + delta);
}

export type Removed<T> = { item: T; index: number };

/** The list without item `id`, and what Undo needs to put it back. */
export function removeItem<T extends { id: number }>(
  items: T[],
  id: number,
): { items: T[]; removed: Removed<T> | null } {
  const index = items.findIndex((x) => x.id === id);
  if (index < 0) return { items, removed: null };
  return { items: items.filter((x) => x.id !== id), removed: { item: items[index], index } };
}

/** Undo: the removed item back where it was (or at the end, when the list got shorter). */
export function restoreItem<T>(items: T[], removed: Removed<T>): T[] {
  const next = items.slice();
  next.splice(Math.min(removed.index, next.length), 0, removed.item);
  return next;
}

/** The body that adds a removed clip again in its place (Undo, through the API). */
export function readdBody(removed: Removed<Clip>) {
  const c = removed.item;
  return {
    recording: c.recording,
    t0: c.t0,
    t1: c.t1,
    title: c.title ?? undefined,
    note: c.note ?? undefined,
    quote: c.quote ?? undefined,
    highlight: c.highlight ?? undefined,
    position: removed.index,
  };
}

/** Where the clip playing is after a reorder or a removal: the same clip's new place, else the one now there. */
export function followCurrent(
  before: { id: number }[],
  after: { id: number }[],
  current: number | null,
): number | null {
  if (current == null) return null;
  const id = before[current]?.id;
  const at = after.findIndex((x) => x.id === id);
  if (at >= 0) return at;
  return after.length ? Math.min(current, after.length - 1) : null;
}
