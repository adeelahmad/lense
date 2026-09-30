import type { ChatScope, Namespace, RecordingSummary } from "@/app/openapi-client/types.gen";
import { count, shortDate } from "@/lib/format";

/** What a conversation may draw on. Every field narrows it; an empty scope is everything you can read. */
export type Scope = { namespaces?: string[]; recordings?: number[]; speakers?: number[]; from?: string; to?: string };

/** The scope as the API stores it (dropping empty parts). */
export function toApiScope(s: Scope): ChatScope {
  const out: ChatScope = {};
  if (s.namespaces?.length) out.namespaces = s.namespaces;
  if (s.recordings?.length) out.recordings = s.recordings;
  if (s.speakers?.length) out.speakers = s.speakers;
  if (s.from) out.from = s.from;
  if (s.to) out.to = s.to;
  return out;
}

/** A stored scope (untyped JSON) read back. */
export function fromApiScope(raw: Record<string, unknown> | null | undefined): Scope {
  const r = raw ?? {};
  const strs = (v: unknown) => (Array.isArray(v) ? v.map(String) : undefined);
  const ints = (v: unknown) => (Array.isArray(v) ? v.map(Number).filter(Number.isFinite) : undefined);
  const s: Scope = { namespaces: strs(r.namespaces), recordings: ints(r.recordings), speakers: ints(r.speakers) };
  if (typeof r.from === "string") s.from = r.from;
  if (typeof r.to === "string") s.to = r.to;
  return toScope(toApiScope(s));
}

function toScope(c: ChatScope): Scope {
  return {
    ...(c.namespaces ? { namespaces: c.namespaces } : {}),
    ...(c.recordings ? { recordings: c.recordings } : {}),
    ...(c.speakers ? { speakers: c.speakers } : {}),
    ...(c.from ? { from: c.from } : {}),
    ...(c.to ? { to: c.to } : {}),
  };
}

export function isEverything(s: Scope): boolean {
  return !s.namespaces?.length && !s.recordings?.length && !s.speakers?.length && !s.from && !s.to;
}

/** Scope from a link (Search's "Ask in chat", a recording's chat, a collection): ns, recording(s), speaker(s), from, to. */
export function scopeFromParams(p: URLSearchParams): Scope {
  const list = (k: string) =>
    p
      .getAll(k)
      .flatMap((v) => v.split(","))
      .map((v) => v.trim())
      .filter(Boolean);
  const ints = (k: string) => list(k).map(Number).filter((n) => Number.isInteger(n) && n > 0);
  return toScope(
    toApiScope({
      namespaces: list("ns"),
      recordings: [...ints("recording"), ...ints("recordings")],
      speakers: [...ints("speaker"), ...ints("speakers")],
      from: p.get("from") ?? undefined,
      to: p.get("to") ?? undefined,
    }),
  );
}

/** "1,382 recordings · 702 h" for the namespaces in scope (or all of yours). */
export function namespaceSize(nss: Pick<Namespace, "recordings" | "ms">[]): string {
  const recs = nss.reduce((a, n) => a + ((n.recordings as number) ?? 0), 0);
  const ms = nss.reduce((a, n) => a + ((n.ms as number) ?? 0), 0);
  return `${count(recs)} ${recs === 1 ? "recording" : "recordings"} · ${hoursShort(ms)}`;
}

export function hoursShort(ms: number): string {
  const h = ms / 3.6e6;
  if (h >= 10) return `${Math.round(h)} h`;
  if (h >= 1) return `${h.toFixed(1).replace(/\.0$/, "")} h`;
  const m = Math.round(ms / 60000);
  return m >= 1 ? `${m} min` : `${Math.max(0, Math.round(ms / 1000))} s`;
}

/** "Episode 12" or "3 recordings · 1.2 h". */
export function recordingsLabel(ids: number[], byId: Map<number, Pick<RecordingSummary, "title" | "duration_ms">>): { label: string; size?: string } {
  if (ids.length === 1) return { label: byId.get(ids[0])?.title ?? `Recording #${ids[0]}` };
  const ms = ids.reduce((a, id) => a + (byId.get(id)?.duration_ms ?? 0), 0);
  return { label: `${count(ids.length)} recordings`, size: ms ? hoursShort(ms) : undefined };
}

export function datesLabel(from?: string, to?: string): string {
  if (from && to) return `${shortDate(from)} – ${shortDate(to)}`;
  if (from) return `From ${shortDate(from)}`;
  return `Until ${shortDate(to)}`;
}

/** Words for the scope in sentences: "podcasts", "podcasts and customer-calls", "all your namespaces". */
export function scopeWords(s: Scope): string {
  const ns = s.namespaces ?? [];
  const base = ns.length === 0 ? "all your namespaces" : ns.length === 1 ? ns[0] : `${ns.slice(0, -1).join(", ")} and ${ns[ns.length - 1]}`;
  const extra = [s.recordings?.length ? `${s.recordings.length === 1 ? "one recording" : `${s.recordings.length} recordings`}` : null, s.speakers?.length ? "chosen speakers" : null]
    .filter(Boolean)
    .join(", ");
  return extra ? `${base} (${extra})` : base;
}
