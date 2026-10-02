/**
 * The search box: free words plus typed filters (`speaker:"Host B"`, `namespace:podcasts`, `emotion:surprise`,
 * `recording:"Episode 12"`, `object:car`). Typed filters become chips; the chip and the facet are one filter. The backend's rules:
 * every word must appear (English stemming), "quoted phrases" as written, OR between alternatives, no prefix search.
 */

export type FilterKey = "namespace" | "speaker" | "emotion" | "recording" | "object";

/** Filters as the URL holds them: speaker and recording are ids, namespace a name, emotion a label. */
export type SearchFilters = {
  namespace?: string;
  speaker?: number;
  emotion?: string;
  recording?: number;
  /** A kind of object the recordings have (person, car …). */
  object?: string;
};

/** Filters as typed in the box, by name, before they are matched to ids. */
export type TypedFilters = Partial<Record<FilterKey, string>>;

const KEYS: Record<string, FilterKey> = {
  namespace: "namespace",
  ns: "namespace",
  speaker: "speaker",
  emotion: "emotion",
  recording: "recording",
  object: "object",
};
const FILTER_RX = /(?:^|\s)(namespace|ns|speaker|emotion|recording|object):(?:"([^"]*)"?|(\S+))/gi;

/** Split what was typed into the words to search for and the typed filters. */
export function parseQuery(input: string): {
  text: string;
  typed: TypedFilters;
} {
  const typed: TypedFilters = {};
  const text = input
    .replace(FILTER_RX, (_all, key: string, quoted: string | undefined, bare: string | undefined) => {
      const value = (quoted ?? bare ?? "").trim();
      if (value) typed[KEYS[key.toLowerCase()]] = value;
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
  return text.match(/"[^"]+"|\S+/g) ?? [];
}

/** True when there is anything to search for (not just OR or punctuation). */
export function hasTerms(text: string): boolean {
  return tokens(text).some((t) => t.toUpperCase() !== "OR" && /[\p{L}\p{N}]/u.test(t));
}

/** Read the search state from URL parameters. */
export function fromParams(p: URLSearchParams): {
  q: string;
  filters: SearchFilters;
  /** Also search by meaning (`meaning=1`). */
  meaning: boolean;
} {
  const int = (v: string | null) => (v && /^\d+$/.test(v) ? Number(v) : undefined);
  return {
    q: p.get("q") ?? "",
    filters: {
      namespace: p.get("ns") || undefined,
      speaker: int(p.get("speaker")),
      emotion: p.get("emotion") || undefined,
      recording: int(p.get("recording")),
      object: p.get("object") || undefined,
    },
    meaning: p.get("meaning") === "1",
  };
}

/** The URL query for a search state (empty values left out). */
export function toParams(q: string, f: SearchFilters, meaning = false): string {
  const p = new URLSearchParams();
  if (q.trim()) p.set("q", q.trim());
  if (f.namespace) p.set("ns", f.namespace);
  if (f.speaker != null) p.set("speaker", String(f.speaker));
  if (f.emotion) p.set("emotion", f.emotion);
  if (f.recording != null) p.set("recording", String(f.recording));
  if (f.object) p.set("object", f.object);
  if (meaning) p.set("meaning", "1");
  return p.toString();
}

export function activeFilterCount(f: SearchFilters): number {
  return [f.namespace, f.speaker, f.emotion, f.recording, f.object].filter((v) => v != null && v !== "").length;
}

type Saved = {
  q: string;
  namespace?: string | null;
  speaker?: number | null;
  speaker_name?: string | null;
  emotion?: string | null;
  recording?: number | null;
  recording_title?: string | null;
  object?: string | null;
};

/** Where a saved search opens: the search page with its words and filters. */
export function savedSearchHref(s: Saved): string {
  return `/search?${toParams(s.q, {
    namespace: s.namespace ?? undefined,
    speaker: s.speaker ?? undefined,
    emotion: s.emotion ?? undefined,
    recording: s.recording ?? undefined,
    object: s.object ?? undefined,
  })}`;
}

/** "podcasts · Alice · Happy · Episode 12": a saved search's filters, by name where it has them. */
export function savedSearchFilters(s: Saved): string {
  return [
    s.namespace,
    s.speaker != null ? (s.speaker_name ?? `Speaker #${s.speaker}`) : null,
    s.emotion,
    s.recording != null ? (s.recording_title ?? `Recording #${s.recording}`) : null,
    s.object ? `with ${s.object}` : null,
  ]
    .filter(Boolean)
    .join(" · ");
}
