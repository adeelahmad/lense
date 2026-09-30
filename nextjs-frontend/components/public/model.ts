/**
 * A recording's public page (docs/access.md): what a visitor may see of it, and the words for what they can't.
 * Pure helpers for components/public.
 */
import type { AccessPart } from "@/components/access/model";
import { first, langName, type Meta, type Person } from "@/components/iiif/metadata-model";
import { rightsFor } from "@/components/iiif/rights";
import { shortDate } from "@/lib/format";

/** The public page of a recording, the link to share. */
export function publicPath(id: number): string {
  return `/explore/recordings/${id}`;
}

/** A collection's page for visitors. */
export function collectionPath(name: string): string {
  return `/explore/collections/${encodeURIComponent(name)}`;
}

const KIND_WORD: Record<string, string> = { audio: "Audio", video: "Video", transcript: "Transcript only" };

/** The line under a card's title: "12 Sep 2026 · Video". */
export function cardLine(card: { recorded_at?: string | null; media_kind: string }): string {
  return [card.recorded_at ? shortDate(card.recorded_at) : null, KIND_WORD[card.media_kind] ?? null]
    .filter(Boolean)
    .join(" · ");
}

/** Lower case, without accents: what "find in transcript" compares. */
function fold(s: string): string {
  return s.normalize("NFD").replace(/\p{M}/gu, "").toLowerCase();
}

function words(query: string): string[] {
  return fold(query).split(/\s+/).filter(Boolean);
}

/** The transcript lines that contain every word of the query (ignoring case and accents), as indices. */
export function findLines(segments: readonly { text: string }[], query: string): number[] {
  const w = words(query);
  if (!w.length) return [];
  const out: number[] = [];
  segments.forEach((s, i) => {
    const t = fold(s.text);
    if (w.every((x) => t.includes(x))) out.push(i);
  });
  return out;
}

/** A line split into plain and matching parts, to mark what the query found. */
export function markParts(text: string, query: string): { text: string; hit: boolean }[] {
  const w = words(query);
  if (!w.length) return [{ text, hit: false }];
  // folding keeps one character per character for the scripts transcripts use, so positions carry over
  const folded = fold(text);
  if (folded.length !== text.length) return [{ text, hit: false }];
  const hit = new Array<boolean>(text.length).fill(false);
  for (const x of w) {
    for (let at = folded.indexOf(x); at >= 0; at = folded.indexOf(x, at + x.length)) hit.fill(true, at, at + x.length);
  }
  const out: { text: string; hit: boolean }[] = [];
  for (let i = 0; i < text.length; i++) {
    const last = out[out.length - 1];
    if (last && last.hit === hit[i]) last.text += text[i];
    else out.push({ text: text[i], hit: hit[i] });
  }
  return out;
}

/** The line being spoken at `ms` (the last one that has started), or -1. */
export function lineAt(segments: readonly { t0: number }[], ms: number): number {
  let lo = 0;
  let hi = segments.length - 1;
  let found = -1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (segments[mid].t0 <= ms) {
      found = mid;
      lo = mid + 1;
    } else hi = mid - 1;
  }
  return found;
}

const PART_WORDS: Record<AccessPart, { what: (kind: string) => string; verb: (kind: string) => string }> = {
  media: {
    what: (kind) => (kind === "video" ? "The video" : "The audio"),
    verb: (kind) => (kind === "video" ? "watch it" : "listen to it"),
  },
  transcript: { what: () => "The transcript", verb: () => "read it" },
  index: { what: () => "The chapters", verb: () => "see them" },
};

/** What stands in for a part this visitor can't use. */
export function closedNote(
  part: AccessPart,
  opts: { ns?: string | null; signedIn: boolean; kind?: string | null },
): { title: string; body: string } {
  const w = PART_WORDS[part];
  const kind = opts.kind ?? "audio";
  const who = opts.ns ? `members of ${opts.ns}` : "members of its namespace";
  const title = `${w.what(kind)} ${part === "index" ? "aren’t" : "isn’t"} open to everyone`;
  return {
    title,
    body: opts.signedIn
      ? `Only ${who}, and people it’s shared with, can ${w.verb(kind)}.`
      : `Only ${who}, and people it’s shared with, can ${w.verb(kind)}. Sign in if that’s you.`,
  };
}

export type Row = { label: string; items: { text: string; href?: string | null }[] };

function people(list: Person[] | null | undefined): string[] {
  return (list ?? []).map((p) => p.name).filter(Boolean);
}

function day(iso: string | null | undefined): string {
  if (!iso) return "";
  const d = new Date(iso);
  return Number.isNaN(d.getTime())
    ? ""
    : d.toLocaleDateString("en-GB", { day: "numeric", month: "long", year: "numeric", timeZone: "UTC" });
}

/** The description as label and value rows, in reading order; the summary is shown on its own. */
export function descriptionRows(meta: Meta | null | undefined): Row[] {
  if (!meta) return [];
  const rows: Row[] = [];
  const add = (label: string, items: Row["items"]) => {
    const kept = items.filter((i) => i.text.trim());
    if (kept.length) rows.push({ label, items: kept });
  };
  add("Date", [{ text: day(meta.navDate) }]);
  add(
    "Language",
    (meta.language ?? []).map((l) => ({ text: langName(l) })),
  );
  const contributors = meta.contributors ?? [];
  add(
    "Speakers",
    contributors.filter((p) => p.role === "speaker").map((p) => ({ text: p.name, href: p.uri })),
  );
  add(
    "Creators",
    people(meta.creators).map((text) => ({ text })),
  );
  add(
    "Contributors",
    contributors
      .filter((p) => p.role !== "speaker")
      .map((p) => ({ text: p.role ? `${p.name} (${p.role})` : p.name, href: p.uri })),
  );
  add(
    "Subjects",
    (meta.subjects ?? []).map((s) => ({ text: s.label, href: s.uri })),
  );
  for (const pair of meta.metadata ?? []) add(first(pair.label), [{ text: first(pair.value) }]);
  const rights = rightsFor(meta.rights);
  if (meta.rights)
    add("Rights", [{ text: rights ? `${rights.name} (${rights.code})` : meta.rights, href: meta.rights }]);
  add("Attribution", [{ text: first(meta.attribution) }]);
  if (meta.provider?.name) add("Provider", [{ text: meta.provider.name, href: meta.provider.homepage }]);
  add(
    "Identifiers",
    (meta.identifiers ?? []).map((i) => ({ text: i.type ? `${i.type}: ${i.value}` : i.value })),
  );
  if (meta.homepage) add("Homepage", [{ text: meta.homepage, href: meta.homepage }]);
  add(
    "Related",
    (meta.related ?? []).map((r) => ({ text: r.label || r.id, href: r.id })),
  );
  return rows;
}

/** Only http(s) links are followed; anything else shows as text. */
export function safeHref(href: string | null | undefined): string | null {
  return href && /^https?:\/\//i.test(href) ? href : null;
}
