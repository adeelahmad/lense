/**
 * The search box: free words plus typed filters (`speaker:"Host B"`, `namespace:podcasts`, `emotion:surprise`,
 * `event:laughter`, `recording:"Episode 12"`, `object:car`, `tag:interview`, `language:de`, `origin:upload`,
 * `from:2026-01-01`, `to:2026-03-31`). Typed filters become chips; the chip and the facet are one filter. The backend's rules:
 * every word must appear (English stemming), "quoted phrases" as written, OR between alternatives, NOT or -word to
 * leave lines out, no prefix search.
 */

export type FilterKey =
  | "namespace"
  | "speaker"
  | "emotion"
  | "event"
  | "recording"
  | "object"
  | "from"
  | "to"
  | "tag"
  | "origin"
  | "language";

/** Filters as the URL holds them: speaker and recording are ids, namespace a name, emotion and event labels. */
export type SearchFilters = {
  namespace?: string;
  speaker?: number;
  emotion?: string;
  /** A sound heard (Laughter, Applause, BGM …). */
  event?: string;
  recording?: number;
  /** A kind of object the recordings have (person, car …). */
  object?: string;
  /** Recorded on or after this day (YYYY-MM-DD). */
  from?: string;
  /** Recorded on or before this day (YYYY-MM-DD). */
  to?: string;
  /** A tag the assets have. */
  tag?: string;
  /** Where the assets came from: upload, paste, iiif, folder, file or source:<id>. */
  origin?: string;
  /** The assets' language code; none for not known. */
  language?: string;
};

/** The filters on the assets rather than on the moments: when, tag, where from and language. */
export const ASSET_FILTERS = ["from", "to", "tag", "origin", "language"] as const;

/** A day as the date filters take it. */
export const DAY_RX = /^\d{4}-\d{2}-\d{2}$/;

/** Filters as typed in the box, by name, before they are matched to ids. */
export type TypedFilters = Partial<Record<FilterKey, string>>;

const KEYS: Record<string, FilterKey> = {
  namespace: "namespace",
  ns: "namespace",
  speaker: "speaker",
  emotion: "emotion",
  event: "event",
  recording: "recording",
  object: "object",
  from: "from",
  to: "to",
  tag: "tag",
  origin: "origin",
  language: "language",
  lang: "language",
};
const FILTER_RX =
  /(?:^|\s)(namespace|ns|speaker|emotion|event|recording|object|from|to|tag|origin|language|lang):(?:"([^"]*)"?|(\S+))/gi;

/** Split what was typed into the words to search for and the typed filters. */
export function parseQuery(input: string): {
  text: string;
  typed: TypedFilters;
} {
  const typed: TypedFilters = {};
  const text = input
    .replace(FILTER_RX, (all, key: string, quoted: string | undefined, bare: string | undefined) => {
      const value = (quoted ?? bare ?? "").trim();
      const k = KEYS[key.toLowerCase()];
      if ((k === "from" || k === "to") && value && !DAY_RX.test(value)) return all; // "from:alice" is words
      if (value) typed[k] = value;
      return " ";
    })
    .replace(/\s+/g, " ")
    .trim();
  return { text, typed };
}

/** `speaker:"Host B"` style text for a filter value (quoted when it has spaces). */
export function filterToken(key: FilterKey, value: string): string {
  return /[\s"]/.test(value) ? `${key}:"${value.replace(/"/g, "")}"` : `${key}:${value}`;
}

/** Emotion labels are stored capitalised (Neutral, Surprise); people type them any way. */
export function normalizeEmotion(value: string): string {
  const v = value.trim().toLowerCase();
  return v ? v[0].toUpperCase() + v.slice(1) : v;
}

/** Sounds are stored capitalised (Laughter, Applause) and music as BGM; people type them any way. */
export function normalizeEvent(value: string): string {
  const v = normalizeEmotion(value);
  return v === "Music" || v === "Bgm" ? "BGM" : v;
}

/** How a sound reads: BGM is music. */
export function eventName(event: string): string {
  return event === "BGM" ? "Music" : event;
}

/** Words typed with a trailing * (prefix search isn't supported; the backend drops the *). */
export function prefixWords(text: string): string[] {
  return tokens(text)
    .filter((t) => !t.startsWith('"') && /\w\*+$/.test(t))
    .map((t) => t.replace(/\*+$/, ""));
}

/** The query with `word*` (a prefix typed with *) replaced by a whole word. */
export function replacePrefix(text: string, prefix: string, word: string): string {
  const esc = prefix.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  return text.replace(new RegExp(`(^|\\s)${esc}\\*+(?=\\s|$)`, "g"), (_m, lead: string) => `${lead}${word}`);
}

/** The "quoted phrases" in a query. */
export function phrases(text: string): string[] {
  return tokens(text)
    .filter((t) => t.length > 2 && t.startsWith('"') && t.endsWith('"'))
    .map((t) => t.slice(1, -1).trim())
    .filter(Boolean);
}

/** Words and phrases, as the backend splits them. */
export function tokens(text: string): string[] {
  return text.match(/-?"[^"]+"|\S+/g) ?? [];
}

/** The words a search looks for, as text to find in an asset (?q= on its page): without typed filters, left-out
 * words (-word, NOT word), OR, quotes or a trailing *. Empty when nothing is left. */
export function findText(input: string): string {
  const ts = tokens(parseQuery(input).text);
  const out: string[] = [];
  for (let i = 0; i < ts.length; i++) {
    const up = ts[i].toUpperCase();
    if (up === "NOT") i++;
    else if (up !== "OR" && up !== "AND" && !ts[i].startsWith("-")) {
      const w = ts[i].replace(/^"|"$/g, "").replace(/\*+$/, "").trim();
      if (/[\p{L}\p{N}]/u.test(w)) out.push(w);
    }
  }
  return out.join(" ");
}

/** True when there is anything to search for (not just OR or punctuation). */
export function hasTerms(text: string): boolean {
  let negate = false;
  return tokens(text).some((t) => {
    const op = t.toUpperCase();
    if (op === "OR" || op === "AND" || op === "NOT") {
      negate = op === "NOT";
      return false;
    }
    const left = negate || t.startsWith("-");
    negate = false;
    return !left && /[\p{L}\p{N}]/u.test(t);
  });
}

/**
 * How the words are matched: "auto" (by their words and, when search by meaning is set up, by what they mean),
 * "keyword" (only the words) or "semantic" (only by meaning). The URL keeps it as `by=words` or `by=meaning`.
 */
export type SearchMode = "auto" | "keyword" | "semantic";

/** Read the search state from URL parameters. */
export function fromParams(p: URLSearchParams): {
  q: string;
  filters: SearchFilters;
  mode: SearchMode;
} {
  const int = (v: string | null) => (v && /^\d+$/.test(v) ? Number(v) : undefined);
  const day = (v: string | null) => (v && DAY_RX.test(v) ? v : undefined);
  const by = p.get("by");
  return {
    q: p.get("q") ?? "",
    mode: by === "words" ? "keyword" : by === "meaning" ? "semantic" : "auto",
    filters: {
      namespace: p.get("ns") || undefined,
      speaker: int(p.get("speaker")),
      emotion: p.get("emotion") || undefined,
      event: p.get("event") || undefined,
      recording: int(p.get("recording")),
      object: p.get("object") || undefined,
      from: day(p.get("from")),
      to: day(p.get("to")),
      tag: p.get("tag") || undefined,
      origin: p.get("origin") || undefined,
      language: p.get("language") || undefined,
    },
  };
}

/** The URL query for a search state (empty values left out). */
export function toParams(q: string, f: SearchFilters, mode: SearchMode = "auto"): string {
  const p = new URLSearchParams();
  if (q.trim()) p.set("q", q.trim());
  if (f.namespace) p.set("ns", f.namespace);
  if (f.speaker != null) p.set("speaker", String(f.speaker));
  if (f.emotion) p.set("emotion", f.emotion);
  if (f.event) p.set("event", f.event);
  if (f.recording != null) p.set("recording", String(f.recording));
  if (f.object) p.set("object", f.object);
  for (const k of ASSET_FILTERS) if (f[k]) p.set(k, f[k]!);
  if (mode !== "auto") p.set("by", mode === "keyword" ? "words" : "meaning");
  return p.toString();
}

export function activeFilterCount(f: SearchFilters): number {
  return [f.namespace, f.speaker, f.emotion, f.event, f.recording, f.object, ...ASSET_FILTERS.map((k) => f[k])].filter(
    (v) => v != null && v !== "",
  ).length;
}

type Saved = {
  q: string;
  namespace?: string | null;
  speaker?: number | null;
  speaker_name?: string | null;
  emotion?: string | null;
  event?: string | null;
  recording?: number | null;
  recording_title?: string | null;
  object?: string | null;
  date_from?: string | null;
  date_to?: string | null;
  tag?: string | null;
  origin?: string | null;
  language?: string | null;
};

/** Where a saved search opens: the search page with its words and filters. */
export function savedSearchHref(s: Saved): string {
  return `/search?${toParams(s.q, {
    namespace: s.namespace ?? undefined,
    speaker: s.speaker ?? undefined,
    emotion: s.emotion ?? undefined,
    event: s.event ?? undefined,
    recording: s.recording ?? undefined,
    object: s.object ?? undefined,
    from: s.date_from ?? undefined,
    to: s.date_to ?? undefined,
    tag: s.tag ?? undefined,
    origin: s.origin ?? undefined,
    language: s.language ?? undefined,
  })}`;
}

/** "podcasts · Alice · Happy · Laughter · Episode 12": a saved search's filters, by name where it has them. */
export function savedSearchFilters(s: Saved): string {
  return [
    s.namespace,
    s.speaker != null ? (s.speaker_name ?? `Person #${s.speaker}`) : null,
    s.emotion,
    s.event ? eventName(s.event) : null,
    s.recording != null ? (s.recording_title ?? `Asset #${s.recording}`) : null,
    s.object ? `with ${s.object}` : null,
    dateRange(s.date_from, s.date_to),
    s.tag ? `#${s.tag}` : null,
    s.origin ? `from ${s.origin}` : null,
    s.language ? `in ${s.language}` : null,
  ]
    .filter(Boolean)
    .join(" · ");
}

/** "2026-01-01 – 2026-03-31", "since 2026-01-01" or "until 2026-03-31": a date filter as words. */
export function dateRange(from?: string | null, to?: string | null): string | null {
  if (from && to) return from === to ? from : `${from} – ${to}`;
  if (from) return `since ${from}`;
  if (to) return `until ${to}`;
  return null;
}
